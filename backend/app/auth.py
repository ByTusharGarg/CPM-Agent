"""Authentication (email one-time codes -> session token) and role-based access control.

Tenancy: a Project belongs to an Organization; every project-scoped record
(milestones, alerts, work items, ...) is reached through its project_id, so
checking project membership scopes all of them.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .config import settings
from .db import session_scope, utcnow
from .models import ROLES, AuthSession, LoginCode, Membership, Project, User

log = logging.getLogger(__name__)

SESSION_COOKIE = "cpm_session"
SESSION_TTL = timedelta(days=14)
CODE_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 5

# Permission -> roles allowed. Owners of the organization (is_org_admin) act as "owner".
PERMISSIONS: dict[str, set[str]] = {
    "project.view": {"owner", "owner_rep", "consultant", "viewer"},
    "project.edit": {"owner", "owner_rep"},
    "chat.send": {"owner", "owner_rep"},
    "alert.decide": {"owner", "owner_rep"},
    "action.approve": {"owner", "owner_rep"},
    "action.approve_locked": {"owner"},  # baseline schedule / budget / money approvals
    "policy.edit": {"owner"},
    "members.manage": {"owner"},
    "work.cancel": {"owner", "owner_rep"},
    "health.check": {"owner", "owner_rep", "consultant"},
    "report.request": {"owner", "owner_rep", "consultant"},
    "report.view": {"owner", "owner_rep", "consultant", "viewer"},
    "update.submit": {"owner", "owner_rep", "contractor"},
    "contractors.view": {"owner", "owner_rep", "consultant", "contractor"},
    "schedule.import": {"owner"},  # replaces the activity network and the current baseline
}
assert all(r in ROLES for roles in PERMISSIONS.values() for r in roles)


def _h(value: str) -> str:
    return hashlib.sha256(f"{settings.secret_key}:{value}".encode()).hexdigest()


# --------------------------------------------------------------------------- login flow


def issue_login_code(s: Session, email: str) -> str | None:
    """Create a 6-digit code for an existing active user. Returns the code (caller
    delivers it) or None for unknown users - callers must not reveal which."""
    email = email.strip().lower()
    user = s.exec(select(User).where(User.email == email, User.is_active == True)).first()  # noqa: E712
    if user is None:
        return None
    code = f"{secrets.randbelow(1_000_000):06d}"
    s.add(LoginCode(email=email, code_hash=_h(code), expires_at=utcnow() + CODE_TTL))
    return code


def verify_login_code(s: Session, email: str, code: str) -> str:
    """Returns a new session token, or raises HTTPException(401)."""
    email = email.strip().lower()
    rows = s.exec(
        select(LoginCode).where(LoginCode.email == email, LoginCode.used == False)  # noqa: E712
        .order_by(LoginCode.id.desc())
    ).all()
    lc = rows[0] if rows else None
    if lc is None or lc.expires_at < utcnow() or lc.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(401, "code expired - request a new one")
    lc.attempts += 1
    s.add(lc)
    if not hmac.compare_digest(lc.code_hash, _h(code.strip())):
        s.commit()  # persist the attempt counter before failing
        raise HTTPException(401, "invalid code")
    lc.used = True
    user = s.exec(select(User).where(User.email == email)).one()
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(user_id=user.id, token_hash=_h(token), expires_at=utcnow() + SESSION_TTL))
    return token


def revoke_session(s: Session, token: str) -> None:
    row = s.exec(select(AuthSession).where(AuthSession.token_hash == _h(token))).first()
    if row:
        s.delete(row)


# --------------------------------------------------------------------------- request auth


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def current_user(request: Request) -> User:
    token = _token_from(request)
    if not token:
        raise HTTPException(401, "not signed in")
    with session_scope() as s:
        sess = s.exec(select(AuthSession).where(AuthSession.token_hash == _h(token))).first()
        if sess is None or sess.expires_at < utcnow():
            raise HTTPException(401, "session expired")
        user = s.get(User, sess.user_id)
        if user is None or not user.is_active:
            raise HTTPException(401, "account disabled")
        return user


@dataclass
class Access:
    user: User
    project_id: int
    role: str
    contractor_id: int | None = None

    def can(self, perm: str) -> bool:
        return self.role in PERMISSIONS[perm]

    @property
    def actor(self) -> str:
        return f"{self.role}:{self.user.email}"


def project_access(s: Session, user: User, project_id: int) -> Access | None:
    project = s.get(Project, project_id)
    if project is None:
        return None
    if user.is_org_admin and project.org_id == user.org_id:
        return Access(user, project_id, "owner")
    m = s.exec(select(Membership).where(Membership.user_id == user.id, Membership.project_id == project_id)).first()
    if m is None:
        return None
    return Access(user, project_id, m.role, m.contractor_id)


def check(user: User, project_id: int, perm: str) -> Access:
    with session_scope() as s:
        acc = project_access(s, user, project_id)
    if acc is None:
        raise HTTPException(404, "project not found")  # don't reveal existence
    if not acc.can(perm):
        raise HTTPException(403, f"your role ({acc.role}) cannot do this ({perm})")
    return acc


def require(perm: str):
    """FastAPI dependency for routes with a `pid` path parameter."""

    def dep(pid: int, user: User = Depends(current_user)) -> Access:
        return check(user, pid, perm)

    return dep


def accessible_projects(s: Session, user: User) -> list[tuple[Project, str]]:
    out: list[tuple[Project, str]] = []
    if user.is_org_admin:
        for p in s.exec(select(Project).where(Project.org_id == user.org_id).order_by(Project.id)).all():
            out.append((p, "owner"))
        return out
    for m in s.exec(select(Membership).where(Membership.user_id == user.id)).all():
        p = s.get(Project, m.project_id)
        if p is not None:
            out.append((p, m.role))
    return sorted(out, key=lambda x: x[0].id)
