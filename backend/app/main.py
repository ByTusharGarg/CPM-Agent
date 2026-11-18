"""FastAPI app: REST + Server-Sent Events for the chat-first UI.

Every project route is protected by `auth.require(<permission>)`, which resolves
the signed-in user's role on that project (see auth.PERMISSIONS).
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlmodel import col, select
from sse_starlette.sse import EventSourceResponse

from . import auth
from .auth import Access, check, current_user, require
from .config import settings
from .db import migrate, session_scope, utcnow
from .harness import authz
from .harness.authz import LOCKED_APPROVAL, _chat_dict
from .harness.health import run_health_check
from .harness.llm import make_llm
from .harness.runtime import Runtime
from .harness.watchdog import watchdog_tick
from .harness.worker import work_item_view
from .harness.workers import registry
from .models import (
    ROLES,
    ActionLog,
    Alert,
    ChatMessage,
    Contractor,
    DailyUpdate,
    Decision,
    DesignDeliverable,
    Event,
    Membership,
    Milestone,
    Notification,
    NotificationPref,
    ProcurementItem,
    Project,
    Report,
    StoredFile,
    User,
    WorkItem,
)
from .notify import prefs_for
from .scheduler import request_report, start_scheduler
from .services import ServiceError, ingest_daily_update, owner_chat, record_alert_decision
from .storage import read_file, save_file
from .tracking.service import project_health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cpm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    rt = Runtime(make_llm())
    app.state.rt = rt
    await rt.resume()
    sched = start_scheduler(rt) if settings.enable_scheduler else None
    log.info("CPM harness up (provider=%s, db=%s)", settings.llm_provider, settings.database_url.split("@")[-1])
    yield
    if sched:
        sched.shutdown(wait=False)
    await rt.shutdown()


app = FastAPI(title="CPM Agent Harness", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True
)

from .channels.routes import router as channels_router  # noqa: E402

app.include_router(channels_router)

from .api_docs import router as docs_router  # noqa: E402

app.include_router(docs_router)


@app.exception_handler(ServiceError)
async def _service_error(request: Request, exc: ServiceError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


def rt_of(request: Request) -> Runtime:
    return request.app.state.rt


def _needs_you(s, pid: int) -> dict[str, Any]:
    alerts = s.exec(select(Alert).where(Alert.project_id == pid, Alert.status == "awaiting_owner").order_by(col(Alert.id).desc())).all()
    approvals = s.exec(select(ActionLog).where(ActionLog.project_id == pid, ActionLog.status == "pending_approval")).all()
    return {
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "approvals": [
            {**x.model_dump(mode="json"), "summary": authz.ACTIONS[x.action].summarize(x.params) if x.action in authz.ACTIONS else x.action,
             "locked": x.action in LOCKED_APPROVAL}
            for x in approvals
        ],
    }


# --------------------------------------------------------------------------- auth


class CodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)


class CodeVerify(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=10)


@app.post("/api/auth/request-code")
def request_code(body: CodeRequest) -> dict[str, Any]:
    from .channels import ChannelError
    from .channels.email import EmailChannel

    with session_scope() as s:
        code = auth.issue_login_code(s, body.email)
    out: dict[str, Any] = {"sent": True}  # same answer for unknown emails (no account enumeration)
    if code is None:
        return out
    if settings.dev_show_otp:
        log.warning("DEV login code for %s: %s", body.email, code)
        out["dev_code"] = code
        return out
    try:
        EmailChannel().send(body.email.strip().lower(), "Your CPM sign-in code", f"Your code is {code}. It expires in 10 minutes.")
    except ChannelError as e:
        log.error("could not email login code: %s", e)
        raise HTTPException(503, "email delivery is not configured") from e
    return out


@app.post("/api/auth/verify")
def verify_code(body: CodeVerify, response: Response) -> dict[str, Any]:
    with session_scope() as s:
        token = auth.verify_login_code(s, body.email, body.code)
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True, samesite="lax", secure=settings.cookie_secure,
    )
    return {"token": token}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = auth._token_from(request)
    if token:
        with session_scope() as s:
            auth.revoke_session(s, token)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        projects = auth.accessible_projects(s, user)
        memberships = {m.project_id: m for m in s.exec(select(Membership).where(Membership.user_id == user.id)).all()}
    return {
        "id": user.id, "email": user.email, "name": user.name, "phone": user.phone, "is_org_admin": user.is_org_admin,
        "projects": [
            {"id": p.id, "role": role,
             "contractor_id": memberships[p.id].contractor_id if p.id in memberships else None,
             "permissions": sorted(k for k, v in auth.PERMISSIONS.items() if role in v)}
            for p, role in projects
        ],
    }


# --------------------------------------------------------------------------- projects


@app.get("/api/projects")
def list_projects(user: User = Depends(current_user)) -> list[dict[str, Any]]:
    out = []
    with session_scope() as s:
        for p, role in auth.accessible_projects(s, user):
            row: dict[str, Any] = {"id": p.id, "name": p.name, "location": p.location, "status": p.status, "role": role}
            if role in auth.PERMISSIONS["project.view"]:
                h = project_health(s, p.id)
                ny = _needs_you(s, p.id)
                row.update(health=h.status, actual_pct=h.actual_pct, slip_days=h.project_slip_days,
                           needs_you=len(ny["alerts"]) + len(ny["approvals"]) if role in auth.PERMISSIONS["alert.decide"] else 0)
            else:  # contractors only see that the project exists
                row.update(health="unknown", actual_pct=0, slip_days=0, needs_you=0)
            out.append(row)
    return out


class MilestoneIn(BaseModel):
    name: str
    planned_start: date
    planned_end: date
    budget: float = 0
    discipline: str = ""
    is_critical: bool = False
    depends_on_index: list[int] = Field(default_factory=list, description="indexes into the milestones list")


class ContractorIn(BaseModel):
    name: str
    trade: str = ""
    contact: str = ""


class ProjectIn(BaseModel):
    name: str
    scope: str = ""
    location: str = ""
    owner_name: str = "Owner"
    currency: str = "INR"
    budget: float
    start_date: date
    target_end_date: date
    milestones: list[MilestoneIn] = Field(default_factory=list)
    contractors: list[ContractorIn] = Field(default_factory=list)


@app.post("/api/projects")
def create_project(body: ProjectIn, user: User = Depends(current_user)) -> dict[str, Any]:
    if not user.is_org_admin:
        raise HTTPException(403, "only organization admins can create projects")
    with session_scope() as s:
        p = Project(org_id=user.org_id, **body.model_dump(exclude={"milestones", "contractors"}))
        s.add(p)
        s.flush()
        for c in body.contractors:
            s.add(Contractor(project_id=p.id, **c.model_dump()))
        created: list[Milestone] = []
        for m in body.milestones:
            row = Milestone(project_id=p.id, **m.model_dump(exclude={"depends_on_index"}))
            s.add(row)
            s.flush()
            created.append(row)
        for m, row in zip(body.milestones, created):
            row.depends_on = [created[i].id for i in m.depends_on_index if 0 <= i < len(created)]
            s.add(row)
        s.add(ChatMessage(project_id=p.id, role="manager", content=f"Project {p.name} is set up. I'll run a health check every morning and only interrupt you when a decision is needed."))
        return {"id": p.id}


@app.get("/api/projects/{pid}")
def get_project(pid: int, acc: Access = Depends(require("project.view"))) -> dict[str, Any]:
    with session_scope() as s:
        p = s.get(Project, pid)
        contractors = s.exec(select(Contractor).where(Contractor.project_id == pid)).all()
        return {
            **p.model_dump(mode="json"),
            "my_role": acc.role,
            "permissions": sorted(k for k, v in auth.PERMISSIONS.items() if acc.role in v),
            "contractors": [{"id": c.id, "name": c.name, "trade": c.trade, "allowlisted": c.allowlisted} for c in contractors],
        }


@app.get("/api/projects/{pid}/health")
def get_health(pid: int, acc: Access = Depends(require("project.view"))) -> dict[str, Any]:
    with session_scope() as s:
        return project_health(s, pid).to_dict()


@app.get("/api/projects/{pid}/records")
def get_records(pid: int, acc: Access = Depends(require("project.view"))) -> dict[str, Any]:
    with session_scope() as s:
        def rows(model):
            return [r.model_dump(mode="json") for r in s.exec(select(model).where(model.project_id == pid).order_by(model.id)).all()]

        return {"milestones": rows(Milestone), "procurement": rows(ProcurementItem), "design": rows(DesignDeliverable)}


@app.post("/api/projects/{pid}/health-check")
async def trigger_health_check(pid: int, request: Request, acc: Access = Depends(require("health.check"))) -> dict[str, Any]:
    return await run_health_check(rt_of(request), pid, reason=f"manual by {acc.user.email}")


# --------------------------------------------------------------------------- chat + stream


@app.get("/api/projects/{pid}/chat")
def get_chat(pid: int, limit: int = 200, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(ChatMessage).where(ChatMessage.project_id == pid).order_by(col(ChatMessage.id).desc()).limit(limit)).all()
        return [_chat_dict(m) for m in reversed(rows)]


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@app.post("/api/projects/{pid}/chat", status_code=202)
async def post_chat(pid: int, body: ChatIn, request: Request, acc: Access = Depends(require("chat.send"))) -> dict[str, Any]:
    return await owner_chat(rt_of(request), pid, body.text, acc.user.id, acc.user.name or acc.user.email, acc.role)


@app.get("/api/projects/{pid}/stream")
async def stream(pid: int, request: Request, acc: Access = Depends(require("project.view"))):
    rt = rt_of(request)
    q = rt.open_stream(pid)

    async def gen():
        try:
            yield {"event": "hello", "data": json.dumps({"project_id": pid, "role": acc.role})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": msg["type"], "data": json.dumps(msg["data"], default=str)}
        finally:
            rt.close_stream(pid, q)

    return EventSourceResponse(gen())


# --------------------------------------------------------------------------- needs-you: alerts + approvals


@app.get("/api/projects/{pid}/needs-you")
def needs_you(pid: int, acc: Access = Depends(require("project.view"))) -> dict[str, Any]:
    with session_scope() as s:
        return _needs_you(s, pid)


@app.get("/api/projects/{pid}/alerts")
def list_alerts(pid: int, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(Alert).where(Alert.project_id == pid).order_by(col(Alert.id).desc()).limit(50)).all()
        return [a.model_dump(mode="json") for a in rows]


class DecideIn(BaseModel):
    option_index: Optional[int] = None
    notes: str = ""


@app.post("/api/alerts/{alert_id}/decide")
async def decide(alert_id: int, body: DecideIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        al = s.get(Alert, alert_id)
        if al is None:
            raise HTTPException(404, "alert not found")
        pid = al.project_id
    acc = check(user, pid, "alert.decide")
    return {"decision_id": await record_alert_decision(rt_of(request), alert_id, body.option_index, body.notes, acc.actor)}


class ApprovalIn(BaseModel):
    approve: bool
    note: str = ""


@app.post("/api/actions/{action_id}/decide")
async def decide_action(action_id: int, body: ApprovalIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        entry = s.get(ActionLog, action_id)
        if entry is None:
            raise HTTPException(404, "action not found")
        pid, name = entry.project_id, entry.action
    acc = check(user, pid, "action.approve_locked" if name in LOCKED_APPROVAL else "action.approve")
    try:
        entry = await authz.decide_approval(rt_of(request), action_id, body.approve, body.note, actor=acc.actor)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return entry.model_dump(mode="json")


# --------------------------------------------------------------------------- authorization modes


@app.get("/api/projects/{pid}/policies")
def get_policies(pid: int, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        return authz.all_policies(s, pid)


class PolicyIn(BaseModel):
    mode: str


@app.put("/api/projects/{pid}/policies/{action}")
def put_policy(pid: int, action: str, body: PolicyIn, acc: Access = Depends(require("policy.edit"))) -> dict[str, Any]:
    with session_scope() as s:
        try:
            authz.set_policy(s, pid, action, body.mode)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return {"action": action, "mode": body.mode}


@app.get("/api/projects/{pid}/actions")
def list_actions(pid: int, status: Optional[str] = None, limit: int = 50, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(ActionLog).where(ActionLog.project_id == pid)
        if status:
            q = q.where(ActionLog.status == status)
        rows = s.exec(q.order_by(col(ActionLog.id).desc()).limit(limit)).all()
        return [
            {**r.model_dump(mode="json"), "summary": authz.ACTIONS[r.action].summarize(r.params) if r.action in authz.ACTIONS else r.action}
            for r in rows
        ]


# --------------------------------------------------------------------------- team / members


class MemberIn(BaseModel):
    email: str
    name: str = ""
    phone: str = ""
    role: str
    contractor_id: Optional[int] = None


def _member_view(m: Membership, u: User) -> dict[str, Any]:
    return {"id": m.id, "user_id": u.id, "email": u.email, "name": u.name, "phone": u.phone, "role": m.role, "contractor_id": m.contractor_id}


@app.get("/api/projects/{pid}/members")
def list_members(pid: int, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        out = []
        for m in s.exec(select(Membership).where(Membership.project_id == pid).order_by(Membership.id)).all():
            u = s.get(User, m.user_id)
            out.append(_member_view(m, u))
        return out


@app.post("/api/projects/{pid}/members")
def add_member(pid: int, body: MemberIn, acc: Access = Depends(require("members.manage"))) -> dict[str, Any]:
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    email = body.email.strip().lower()
    with session_scope() as s:
        if body.role == "contractor":
            c = s.get(Contractor, body.contractor_id) if body.contractor_id else None
            if c is None or c.project_id != pid:
                raise HTTPException(400, "contractor members must be linked to a contractor of this project")
        elif body.contractor_id:
            raise HTTPException(400, "only contractor members can be linked to a contractor")
        u = s.exec(select(User).where(User.email == email)).first()
        if u is None:
            u = User(org_id=acc.user.org_id, email=email, name=body.name, phone=body.phone)
            s.add(u)
            s.flush()
        elif u.org_id != acc.user.org_id and body.role not in ("contractor", "consultant"):
            raise HTTPException(400, "users from another organization can only join as contractor or consultant")
        m = s.exec(select(Membership).where(Membership.user_id == u.id, Membership.project_id == pid)).first()
        if m is None:
            m = Membership(user_id=u.id, project_id=pid, role=body.role, contractor_id=body.contractor_id)
        else:
            m.role, m.contractor_id = body.role, body.contractor_id
        s.add(m)
        s.flush()
        return _member_view(m, u)


@app.delete("/api/projects/{pid}/members/{member_id}")
def remove_member(pid: int, member_id: int, acc: Access = Depends(require("members.manage"))) -> dict[str, Any]:
    with session_scope() as s:
        m = s.get(Membership, member_id)
        if m is None or m.project_id != pid:
            raise HTTPException(404)
        if m.user_id == acc.user.id:
            raise HTTPException(400, "you cannot remove yourself")
        s.delete(m)
    return {"removed": member_id}


# --------------------------------------------------------------------------- notifications


class PrefsIn(BaseModel):
    email_enabled: bool = True
    whatsapp_enabled: bool = False
    immediate_min_severity: str = Field("critical", pattern="^(info|warning|critical)$")
    quiet_start_hour: Optional[int] = Field(21, ge=0, le=23)
    quiet_end_hour: Optional[int] = Field(7, ge=0, le=23)
    digest_enabled: bool = True


class ProfileIn(BaseModel):
    name: str = Field("", max_length=120)
    phone: str = Field("", max_length=24, description="E.164, e.g. +919800000001 - used for WhatsApp")


@app.put("/api/me")
def update_me(body: ProfileIn, user: User = Depends(current_user)) -> dict[str, Any]:
    import re

    phone = body.phone.strip()
    if phone and not re.fullmatch(r"\+\d{8,15}", phone.replace(" ", "")):
        raise HTTPException(400, "phone must be in international format, e.g. +919800000001")
    with session_scope() as s:
        u = s.get(User, user.id)
        u.name, u.phone = body.name.strip(), phone.replace(" ", "")
        s.add(u)
    return {"name": u.name, "phone": u.phone}


@app.get("/api/channels/status")
def channel_status(user: User = Depends(current_user)) -> dict[str, Any]:
    from .notify import CHANNELS

    return {
        "email": {"outbound": CHANNELS["email"].configured(), "inbound": bool(settings.email_inbound_secret)},
        "whatsapp": {"outbound": CHANNELS["whatsapp"].configured(), "inbound": bool(settings.wa_app_secret and settings.wa_verify_token),
                     "template": bool(settings.wa_alert_template)},
        "transcription": settings.transcriber,
    }


@app.get("/api/me/notification-prefs")
def get_prefs(user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        return prefs_for(s, user.id).model_dump(exclude={"id", "user_id"})


@app.put("/api/me/notification-prefs")
def put_prefs(body: PrefsIn, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        p = s.exec(select(NotificationPref).where(NotificationPref.user_id == user.id)).first() or NotificationPref(user_id=user.id)
        for k, v in body.model_dump().items():
            setattr(p, k, v)
        s.add(p)
    return body.model_dump()


@app.get("/api/me/notifications")
def my_notifications(limit: int = 50, user: User = Depends(current_user)) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(Notification).where(Notification.user_id == user.id).order_by(col(Notification.id).desc()).limit(limit)).all()
        return [n.model_dump(mode="json") for n in rows]


# --------------------------------------------------------------------------- files


MAX_UPLOAD = 25 * 1024 * 1024


@app.post("/api/projects/{pid}/files")
async def upload_file(pid: int, file: UploadFile = File(...), acc: Access = Depends(require("update.submit"))) -> dict[str, Any]:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large (25 MB max)")
    f = save_file(data, file.filename or "upload", pid, uploaded_by=acc.user.id, content_type=file.content_type)
    return {"id": f.id, "filename": f.filename, "size": f.size, "content_type": f.content_type}


@app.get("/api/files/{fid}")
def download_file(fid: int, user: User = Depends(current_user)) -> Response:
    with session_scope() as s:
        f = s.get(StoredFile, fid)
        if f is None or f.project_id is None:
            raise HTTPException(404)
    check(user, f.project_id, "project.view")
    return Response(read_file(f), media_type=f.content_type, headers={"Content-Disposition": f'inline; filename="{f.filename}"'})


# --------------------------------------------------------------------------- workers / events / reports


@app.get("/api/workers")
def workers(user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [{"name": s.name, "title": s.title, "purpose": s.purpose} for s in registry().values()]


@app.get("/api/projects/{pid}/work-items")
def list_work_items(pid: int, limit: int = 30, acc: Access = Depends(require("project.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(WorkItem).where(WorkItem.project_id == pid).order_by(col(WorkItem.id).desc()).limit(limit)).all()
        return [work_item_view(w) for w in rows]


@app.get("/api/work-items/{wid}")
def get_work_item(wid: int, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        w = s.get(WorkItem, wid)
        if w is None:
            raise HTTPException(404)
    check(user, w.project_id, "project.view")
    return {**work_item_view(w), "instructions": w.instructions, "transcript": w.transcript, "input_tokens": w.input_tokens, "output_tokens": w.output_tokens}


@app.post("/api/work-items/{wid}/cancel")
async def cancel_work_item(wid: int, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        w = s.get(WorkItem, wid)
        if w is None:
            raise HTTPException(404)
    acc = check(user, w.project_id, "work.cancel")
    return {"cancelled": await rt_of(request).cancel_worker(wid, f"cancelled by {acc.user.email}")}


@app.get("/api/projects/{pid}/events")
def list_events(pid: int, limit: int = 50, acc: Access = Depends(require("project.edit"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(Event).where(Event.project_id == pid).order_by(col(Event.id).desc()).limit(limit)).all()
        return [e.model_dump(mode="json") for e in rows]


@app.get("/api/projects/{pid}/reports")
def list_reports(pid: int, acc: Access = Depends(require("report.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(Report).where(Report.project_id == pid).order_by(col(Report.id).desc())).all()
        return [r.model_dump(mode="json") for r in rows]


@app.get("/api/reports/{rid}")
def get_report(rid: int, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        r = s.get(Report, rid)
        if r is None:
            raise HTTPException(404)
    check(user, r.project_id, "report.view")
    return r.model_dump(mode="json")


class ReportReq(BaseModel):
    period: str = "weekly"


@app.post("/api/projects/{pid}/reports", status_code=202)
async def make_report(pid: int, body: ReportReq, request: Request, acc: Access = Depends(require("report.request"))) -> dict[str, Any]:
    await request_report(rt_of(request), pid, body.period)
    return {"requested": True}


@app.post("/api/watchdog/tick")
async def manual_watchdog(request: Request, user: User = Depends(current_user)) -> list[dict[str, Any]]:
    if not user.is_org_admin:
        raise HTTPException(403)
    return await watchdog_tick(rt_of(request))


# --------------------------------------------------------------------------- contractor daily updates


@app.get("/api/projects/{pid}/contractors")
def contractors(pid: int, acc: Access = Depends(require("contractors.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Contractor).where(Contractor.project_id == pid)
        if acc.role == "contractor":  # contractors only see their own company
            q = q.where(Contractor.id == acc.contractor_id)
        return [{"id": c.id, "name": c.name, "trade": c.trade, "allowlisted": c.allowlisted} for c in s.exec(q).all()]


class DailyUpdateIn(BaseModel):
    contractor_id: Optional[int] = Field(None, description="Required for owner/owner_rep submitting on a contractor's behalf; ignored for contractor users.")
    text: str = Field(min_length=3, max_length=8000)
    report_date: Optional[date] = None
    file_ids: list[int] = Field(default_factory=list, description="Photos uploaded via /files")


@app.post("/api/projects/{pid}/daily-updates", status_code=202)
async def post_daily_update(pid: int, body: DailyUpdateIn, request: Request, acc: Access = Depends(require("update.submit"))) -> dict[str, Any]:
    if acc.role == "contractor":
        cid = acc.contractor_id
        if body.contractor_id not in (None, cid):
            raise HTTPException(403, "contractors can only report for their own company")
    else:
        if body.contractor_id is None:
            raise HTTPException(400, "contractor_id is required")
        cid = body.contractor_id
    return await ingest_daily_update(rt_of(request), pid, cid, body.text, body.report_date, acc.actor, body.file_ids)


# --------------------------------------------------------------------------- schedule (phase 9)


def _act_view(r) -> dict[str, Any]:
    return {
        "id": r.id, "ref": f"activity:{r.id}", "name": r.name, "milestone_id": r.milestone_id, "duration": r.duration,
        "remaining": r.remaining, "status": r.status, "pct": r.pct,
        "early_start": r.early_start.isoformat(), "early_finish": r.early_finish.isoformat(),
        "late_start": r.late_start.isoformat(), "late_finish": r.late_finish.isoformat(),
        "total_float": r.total_float, "free_float": r.free_float, "critical": r.critical, "driver": r.driver,
        "baseline_start": r.baseline_start.isoformat() if r.baseline_start else None,
        "baseline_finish": r.baseline_finish.isoformat() if r.baseline_finish else None,
        "finish_variance_days": r.finish_variance_days,
    }


@app.get("/api/projects/{pid}/schedule")
def get_schedule(pid: int, acc: Access = Depends(require("project.view"))) -> dict[str, Any]:
    from .models import Baseline, Dependency

    with session_scope() as s:
        h = project_health(s, pid)
        deps = s.exec(select(Dependency).where(Dependency.project_id == pid)).all()
        baselines = s.exec(select(Baseline).where(Baseline.project_id == pid).order_by(col(Baseline.id).desc())).all()
        milestones = s.exec(select(Milestone).where(Milestone.project_id == pid).order_by(Milestone.planned_start)).all()
    sched = h.schedule
    return {
        "has_network": sched is not None,
        "data_date": h.as_of.isoformat(),
        "target_finish": h.target_end.isoformat(),
        "forecast_finish": (sched.project_finish if sched else h.forecast_end).isoformat(),
        "critical_path": sched.critical_path if sched else [],
        "logic_loops": sched.cycles if sched else [],
        "milestones": [{"id": m.id, "name": m.name} for m in milestones],
        "activities": [_act_view(r) for r in sched.activities] if sched else [],
        "dependencies": [{"pred": d.predecessor_id, "succ": d.successor_id, "type": d.type, "lag": d.lag_days} for d in deps],
        "baselines": [{"id": b.id, "name": b.name, "source": b.source, "current": b.is_current, "created_at": b.created_at.isoformat()} for b in baselines],
    }


@app.get("/api/projects/{pid}/lookahead")
def get_lookahead(pid: int, weeks: int = 3, acc: Access = Depends(require("contractors.view"))) -> dict[str, Any]:
    """Short-term plan. Contractors may see it too (only their own activities)."""
    from .models import Activity
    from .tracking.cpm import lookahead

    with session_scope() as s:
        h = project_health(s, pid)
        own = None
        if acc.role == "contractor":
            own = {a.id for a in s.exec(select(Activity).where(Activity.project_id == pid, Activity.contractor_id == acc.contractor_id)).all()}
    if h.schedule is None:
        return {"has_network": False, "activities": []}
    items = [r for r in lookahead(h.schedule, h.as_of, max(1, min(weeks, 12))) if own is None or r.id in own]
    return {"has_network": True, "from": h.as_of.isoformat(), "weeks": weeks, "activities": [_act_view(r) for r in items]}


@app.post("/api/projects/{pid}/schedule/import")
async def import_schedule(pid: int, request: Request, file: UploadFile = File(...),
                          acc: Access = Depends(require("schedule.import"))) -> dict[str, Any]:
    from .tracking.schedule_import import ImportError_, apply_import, parse_schedule

    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "schedule file too large (20 MB max)")
    try:
        imp = parse_schedule(file.filename or "", data)
        with session_scope() as s:
            result = apply_import(s, s.get(Project, pid), imp, f"Imported {file.filename} ({utcnow():%d %b %Y})")
    except ImportError_ as e:
        raise HTTPException(400, str(e)) from e
    rt = rt_of(request)
    rt.publish(pid, "kpi", {})
    await rt.emit(pid, source="system", kind="schedule_imported",
                  payload={"file": file.filename, "by": acc.actor, **{k: v for k, v in result.items() if k != "warnings"}})
    return result
