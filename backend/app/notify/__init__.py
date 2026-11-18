"""Notification service - the attention budget made explicit.

In-app delivery is the SSE stream (always). This module decides who else gets
told, on which channel, and when:
* Recipients: project members whose role can act on the item (e.g. alert.decide).
* Severity >= the user's `immediate_min_severity` and outside quiet hours ->
  sent now on their enabled channels. Critical items ignore quiet hours.
* Everything else -> queued for the daily digest.
* `actions` become WhatsApp reply buttons or signed email links; `ref`
  ("alert:12") lets a plain-text reply be matched to the item later.
* `send_direct` is for conversation replies (manager answering a WhatsApp
  message, acknowledging a contractor report) - no preference filtering.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from sqlmodel import Session, col, select

from ..auth import PERMISSIONS
from ..channels import ChannelError
from ..channels.email import EmailChannel
from ..channels.whatsapp import REENGAGEMENT_CODES, WhatsAppChannel, WhatsAppError
from ..config import settings
from ..db import session_scope, utcnow
from ..models import Membership, Notification, NotificationPref, Project, User

log = logging.getLogger(__name__)
SEV_RANK = {"info": 0, "warning": 1, "critical": 2}

CHANNELS: dict = {"email": EmailChannel(), "whatsapp": WhatsAppChannel()}


def register_channel(name: str, channel) -> None:
    CHANNELS[name] = channel


def prefs_for(s: Session, user_id: int) -> NotificationPref:
    p = s.exec(select(NotificationPref).where(NotificationPref.user_id == user_id)).first()
    return p or NotificationPref(user_id=user_id)


def in_quiet_hours(p: NotificationPref, now_local: datetime) -> bool:
    if p.quiet_start_hour is None or p.quiet_end_hour is None:
        return False
    h = now_local.hour
    if p.quiet_start_hour <= p.quiet_end_hour:
        return p.quiet_start_hour <= h < p.quiet_end_hour
    return h >= p.quiet_start_hour or h < p.quiet_end_hour  # wraps midnight


def recipients(s: Session, project_id: int, perm: str) -> list[User]:
    roles = PERMISSIONS[perm]
    project = s.get(Project, project_id)
    users: dict[int, User] = {}
    for m in s.exec(select(Membership).where(Membership.project_id == project_id)).all():
        if m.role in roles:
            u = s.get(User, m.user_id)
            if u and u.is_active:
                users[u.id] = u
    if project is not None and project.org_id and "owner" in roles:
        for u in s.exec(select(User).where(User.org_id == project.org_id, User.is_org_admin == True, User.is_active == True)).all():  # noqa: E712
            users[u.id] = u
    return list(users.values())


def _address(u: User | None, channel: str, fallback: str = "") -> str:
    if u is None:
        return fallback
    return u.phone if channel == "whatsapp" else u.email


def notify(
    project_id: int,
    severity: str,
    title: str,
    body: str = "",
    link: str = "",
    perm: str = "alert.decide",
    now_local: datetime | None = None,
    ref: str = "",
    actions: list[dict] | None = None,
) -> list[Notification]:
    """Fan a project event out to people. Returns the outbox rows created."""
    now_local = now_local or datetime.now()
    created: list[Notification] = []
    with session_scope() as s:
        for u in recipients(s, project_id, perm):
            p = prefs_for(s, u.id)
            immediate = SEV_RANK.get(severity, 0) >= SEV_RANK.get(p.immediate_min_severity, 2)
            if immediate and severity != "critical" and in_quiet_hours(p, now_local):
                immediate = False
            channels = [c for c, on in (("email", p.email_enabled), ("whatsapp", p.whatsapp_enabled)) if on and _address(u, c)]
            for ch in channels:
                if not immediate and not p.digest_enabled:
                    continue
                n = Notification(user_id=u.id, project_id=project_id, channel=ch, severity=severity, title=title, body=body,
                                 link=link, ref=ref, actions=list(actions or []), status="queued" if immediate else "digest")
                s.add(n)
                s.flush()
                created.append(n)
        ids = [n.id for n in created if n.status == "queued"]
    deliver(ids)
    return created


def send_direct(
    channel: str, title: str, body: str, user_id: int | None = None, address: str = "", project_id: int | None = None,
    ref: str = "", actions: list[dict] | None = None,
) -> Notification:
    """A conversational message to one person on one channel (audited in the outbox)."""
    with session_scope() as s:
        n = Notification(user_id=user_id, address=address, project_id=project_id, channel=channel, title=title,
                         body=body, ref=ref, actions=list(actions or []), status="queued", severity="info")
        s.add(n)
        s.flush()
        nid = n.id
    deliver([nid])
    with session_scope() as s:
        return s.get(Notification, nid)


def _render_actions(n: Notification) -> list[dict]:
    acts = [dict(a) for a in (n.actions or [])]
    if n.channel == "email" and n.user_id:
        from ..channels.links import action_url

        for a in acts:
            a["url"] = action_url(n.user_id, a["id"])
    return acts


def deliver(notification_ids: Iterable[int]) -> None:
    for nid in notification_ids:
        with session_scope() as s:
            n = s.get(Notification, nid)
            if n is None or n.status not in ("queued", "awaiting_window"):
                continue
            u = s.get(User, n.user_id) if n.user_id else None
            ch = CHANNELS.get(n.channel)
            to = _address(u, n.channel, n.address)
            subject = f"[CPM {n.ref}] {n.title}" if (n.ref and n.channel == "email") else n.title
            try:
                if ch is None:
                    raise ChannelError(f"channel {n.channel} not available")
                n.provider_id = ch.send(to, subject, n.body, n.link, actions=_render_actions(n)) or ""
                n.status, n.sent_at, n.error = "sent", utcnow(), ""
            except WhatsAppError as e:
                if e.code in REENGAGEMENT_CODES:
                    # Outside the 24h window: nudge with the approved template (if any);
                    # the full interactive message is re-sent when the user replies.
                    already_waiting = s.exec(
                        select(Notification).where(Notification.user_id == n.user_id, Notification.channel == "whatsapp",
                                                   Notification.status == "awaiting_window", Notification.id != n.id)
                    ).first()
                    n.status, n.error = "awaiting_window", str(e)
                    if already_waiting is None:  # one template nudge per closed window, not one per message
                        try:
                            ch.send_template(to, n.title)
                        except ChannelError as te:
                            n.error += f" | template: {te}"
                else:
                    n.status, n.error = "failed", str(e)
            except ChannelError as e:
                n.status, n.error = "failed", str(e)
                log.info("notification %s not delivered via %s: %s", nid, n.channel, e)
            s.add(n)


def resend_awaiting(user_id: int) -> int:
    """The user just wrote to us on WhatsApp, so the 24h window is open: send what was waiting."""
    with session_scope() as s:
        ids = [n.id for n in s.exec(
            select(Notification).where(Notification.user_id == user_id, Notification.channel == "whatsapp",
                                       Notification.status == "awaiting_window").order_by(Notification.id)
        ).all()]
    deliver(ids)
    return len(ids)


def latest_context(user_id: int, channel: str) -> Notification | None:
    """The most recent decision request sent to this user on this channel (for plain-text replies)."""
    with session_scope() as s:
        return s.exec(
            select(Notification).where(Notification.user_id == user_id, Notification.channel == channel,
                                       Notification.ref != "", col(Notification.status).in_(["sent", "awaiting_window"]))
            .order_by(col(Notification.id).desc())
        ).first()


def send_digests() -> int:
    """Bundle queued digest items per user/channel into one message. Returns messages sent."""
    sent = 0
    with session_scope() as s:
        rows = s.exec(select(Notification).where(Notification.status == "digest")).all()
        groups: dict[tuple[int, str], list[Notification]] = {}
        for n in rows:
            groups.setdefault((n.user_id, n.channel), []).append(n)
        plan = [(k, [n.id for n in v]) for k, v in groups.items()]
    for (user_id, channel), ids in plan:
        with session_scope() as s:
            items = [s.get(Notification, i) for i in ids]
            u = s.get(User, user_id)
            lines = [f"- [{n.severity}] {n.title}" + (f" ({n.link})" if n.link else "") for n in items]
            body = f"{len(items)} update(s) since your last digest:\n\n" + "\n".join(lines)
            ch = CHANNELS.get(channel)
            try:
                if ch is None:
                    raise ChannelError(f"channel {channel} not available")
                ch.send(_address(u, channel), f"[CPM] Daily digest ({len(items)})", body, settings.app_base_url)
                status, err = "sent", ""
                sent += 1
            except ChannelError as e:
                status, err = "failed", str(e)
            for n in items:
                n.status, n.error, n.sent_at = status, err, utcnow() if status == "sent" else None
                s.add(n)
    return sent


def notify_background(*args, **kwargs) -> None:
    """Fire-and-forget from async code (network calls must not block the event loop)."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        notify(*args, **kwargs)
        return
    fut = loop.run_in_executor(None, lambda: notify(*args, **kwargs))
    fut.add_done_callback(lambda f: f.exception() and log.error("notify failed: %s", f.exception()))
