"""Inbound WhatsApp / email routing.

sender -> user (by phone digits or email) -> project -> meaning:
* button press or "1"/"approve" in reply to a decision request -> decision / approval
* contractor text (+ photos)  -> daily report (fast path -> Site Updates worker)
* owner / owner's rep text    -> message to the project manager (reply goes back on the same channel)
Unknown senders are dropped silently (no reply = no spam amplification) and logged.
Message text is untrusted: it only ever becomes data in events, never instructions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlmodel import select

from ..auth import PERMISSIONS, accessible_projects
from ..db import session_scope
from ..harness.authz import LOCKED_APPROVAL, decide_approval
from ..models import ActionLog, Alert, InboundMessage, Membership, User
from ..notify import latest_context, resend_awaiting
from ..services import ServiceError, contractor_files_pending, ingest_daily_update, owner_chat, record_alert_decision
from ..storage import save_file
from .transcribe import transcribe
from .whatsapp import digits

if TYPE_CHECKING:
    from ..harness.runtime import Runtime

log = logging.getLogger(__name__)

HELP = (
    "Site Manager - how to use this chat:\n"
    "- Contractors: send your daily report as text (photos welcome; send photos first or with a caption).\n"
    "- Owners: ask the project manager anything; reply with an option number or Approve/Reject to decide.\n"
    "- Several projects? Start with #<project number>, e.g. \"#2 slab cast today\".\n"
    "- Send HELP to see this again."
)

YES = {"approve", "approved", "yes", "y", "ok", "okay", "go", "go ahead", "👍"}
NO = {"reject", "rejected", "no", "n", "decline", "👎"}


@dataclass
class Inbound:
    channel: str  # whatsapp | email
    provider_id: str
    from_addr: str
    kind: str = "text"  # text | button | image | audio | email
    text: str = ""
    button_id: str = ""
    ref_hint: str = ""  # e.g. "alert:12" from an email subject tag
    files: list[tuple[bytes, str, str]] = field(default_factory=list)  # (data, mime, filename)


def find_user(channel: str, addr: str) -> User | None:
    with session_scope() as s:
        if channel == "email":
            return s.exec(select(User).where(User.email == addr.strip().lower(), User.is_active == True)).first()  # noqa: E712
        d = digits(addr)
        if not d:
            return None
        for u in s.exec(select(User).where(User.is_active == True, User.phone != "")).all():  # noqa: E712
            if digits(u.phone) == d:
                return u
    return None


def _record(m: Inbound, user_id: int | None, project_id: int | None, outcome: str) -> None:
    with session_scope() as s:
        row = s.exec(select(InboundMessage).where(InboundMessage.provider_id == m.provider_id)).first()
        if row is None:
            row = InboundMessage(channel=m.channel, provider_id=m.provider_id, from_addr=m.from_addr, kind=m.kind,
                                 body=(m.text or m.button_id)[:4000])
        row.user_id, row.project_id, row.outcome = user_id, project_id, outcome[:200]
        s.add(row)


def already_seen(provider_id: str) -> bool:
    with session_scope() as s:
        return s.exec(select(InboundMessage).where(InboundMessage.provider_id == provider_id)).first() is not None


def parse_choice(text: str) -> int | str | None:
    """'2' / 'option 2' / 'B' -> 1 (0-based); 'approve'/'reject' -> 'approve'/'reject'."""
    t = text.strip().lower().rstrip(".!")
    if t in YES:
        return "approve"
    if t in NO:
        return "reject"
    m = re.fullmatch(r"(?:option\s*)?(\d{1,2})", t)
    if m:
        return int(m.group(1)) - 1
    if re.fullmatch(r"[abc]", t):
        return "abc".index(t)
    return None


def _role(user: User, project_id: int) -> tuple[str, int | None]:
    with session_scope() as s:
        for p, role in accessible_projects(s, user):
            if p.id == project_id:
                m = s.exec(select(Membership).where(Membership.user_id == user.id, Membership.project_id == project_id)).first()
                return role, (m.contractor_id if m else None)
    return "", None


async def _decide(rt: "Runtime", user: User, ref: str, choice: int | str, channel: str, note: str = "") -> str:
    kind, _, rest = ref.partition(":")
    rid = int(rest.split(":")[0])
    actor_suffix = f"{user.email} via {channel}"
    if kind == "alert":
        with session_scope() as s:
            al = s.get(Alert, rid)
            pid = al.project_id if al else None
        role, _ = _role(user, pid) if pid else ("", None)
        if role not in PERMISSIONS["alert.decide"]:
            return "Your role can't decide this alert."
        if not isinstance(choice, int):
            return "Reply with the option number (e.g. 2)."
        try:
            await record_alert_decision(rt, rid, choice, note, f"{role}:{actor_suffix}", channel=channel)
        except ServiceError as e:
            return f"Couldn't record that: {e.detail}."
        with session_scope() as s:
            label = s.get(Alert, rid).options[choice]["label"]
        return f"✅ Decision recorded: {label}. The project manager is on it."
    if kind == "action":
        with session_scope() as s:
            entry = s.get(ActionLog, rid)
            pid, name = (entry.project_id, entry.action) if entry else (None, "")
        role, _ = _role(user, pid) if pid else ("", None)
        perm = "action.approve_locked" if name in LOCKED_APPROVAL else "action.approve"
        if role not in PERMISSIONS[perm]:
            return "Only the owner can approve this."
        if choice not in ("approve", "reject"):
            return "Reply APPROVE or REJECT."
        try:
            e = await decide_approval(rt, rid, choice == "approve", note, actor=f"{role}:{actor_suffix}")
        except ValueError as ex:
            return f"Couldn't do that: {ex}."
        return f"✅ {'Approved' if e.status == 'approved' else e.status.capitalize()}: {e.result[:200]}"
    return "Sorry, I couldn't match that reply."


def _pending(ref: str) -> bool:
    kind, _, rest = ref.partition(":")
    rid = int(rest.split(":")[0]) if rest.split(":")[0].isdigit() else 0
    with session_scope() as s:
        if kind == "alert":
            al = s.get(Alert, rid)
            return bool(al and al.status in ("awaiting_owner", "open"))
        if kind == "action":
            e = s.get(ActionLog, rid)
            return bool(e and e.status == "pending_approval")
    return False


async def handle_inbound(rt: "Runtime", m: Inbound) -> str | None:
    """Route one inbound message. Returns the text to reply with (or None)."""
    if already_seen(m.provider_id):
        return None
    user = find_user(m.channel, m.from_addr)
    if user is None:
        _record(m, None, None, "dropped:unknown_sender")
        log.info("dropped %s message from unknown sender %s", m.channel, m.from_addr)
        return None
    if m.channel == "whatsapp":
        resend_awaiting(user.id)  # they wrote to us: the 24h window is open again
    text = (m.text or "").strip()

    if text.lower() in ("help", "?", "hi", "hello", "menu"):
        _record(m, user.id, None, "help")
        return HELP

    # 1) explicit button press
    if m.button_id:
        ref, _, tail = m.button_id.rpartition(":")
        ref = ref.removesuffix(":opt")
        choice: int | str = int(tail) if tail.isdigit() else tail
        reply = await _decide(rt, user, ref, choice, m.channel)
        _record(m, user.id, None, f"decision:{m.button_id}")
        return reply

    # 2) plain reply to the latest decision request ("2", "approve")
    choice = parse_choice(text) if text else None
    if choice is not None:
        ref = m.ref_hint
        if not ref:
            ctx = latest_context(user.id, m.channel)
            ref = ctx.ref if ctx else ""
        if ref and _pending(ref):
            reply = await _decide(rt, user, ref, choice, m.channel)
            _record(m, user.id, None, f"decision:{ref}:{choice}")
            return reply

    # 3) which project?
    with session_scope() as s:
        projects = [(p, r) for p, r in accessible_projects(s, user) if r == "contractor" or r in PERMISSIONS["chat.send"]]
    if not projects:
        _record(m, user.id, None, "dropped:read_only")
        return "Your role is read-only, so I can't act on messages from you here. Use the web app to follow the project."
    sel = re.match(r"^#(\d+)\s*", text)
    pid: int | None = None
    if sel:
        pid = int(sel.group(1))
        text = text[sel.end():].strip()
        if pid not in {p.id for p, _ in projects}:
            _record(m, user.id, None, "dropped:no_access")
            return f"You don't have access to project #{pid}."
    elif len(projects) == 1:
        pid = projects[0][0].id
    else:
        ctx = latest_context(user.id, m.channel)
        if ctx and ctx.project_id in {p.id for p, _ in projects}:
            pid = ctx.project_id
    if pid is None:
        _record(m, user.id, None, "asked_project")
        listing = "\n".join(f"#{p.id} {p.name}" for p, _ in projects)
        return f"Which project is this for? Start your message with the number:\n{listing}"

    role, contractor_id = _role(user, pid)

    # 4) store media
    file_ids: list[int] = []
    voice_note = False
    for data, mime, fname in m.files:
        f = save_file(data, fname, pid, uploaded_by=user.id, content_type=mime)
        with session_scope() as s:
            from ..models import StoredFile

            row = s.get(StoredFile, f.id)
            row.source = m.channel
            s.add(row)
        file_ids.append(f.id)
        if mime.startswith("audio/"):
            voice_note = True
            heard = transcribe(data, mime)
            if heard:
                text = (text + "\n" if text else "") + f"[voice note transcript] {heard}"

    # 5) route by role
    if role == "contractor":
        image_ids = [i for i in file_ids]
        if not text and not voice_note:
            # Photos without a caption wait for the report text.
            with session_scope() as s:
                from ..models import StoredFile

                for fid in image_ids:
                    row = s.get(StoredFile, fid)
                    row.pending_for_user_id = user.id
                    s.add(row)
            _record(m, user.id, pid, "media_pending")
            return f"📷 Got {len(image_ids)} photo(s). Now send today's report as text and I'll attach them."
        if voice_note and not text:
            text = "(voice note received - transcription is not enabled)"
        with session_scope() as s:
            pending = contractor_files_pending(s, user.id, pid)
        try:
            r = await ingest_daily_update(rt, pid, contractor_id, text, None, f"contractor:{user.email} via {m.channel}",
                                          [*pending, *file_ids], channel=m.channel)
        except ServiceError as e:
            _record(m, user.id, pid, f"error:{e.detail}")
            return f"Couldn't file that report: {e.detail}."
        _record(m, user.id, pid, "daily_update" if r["event_id"] else "daily_update_dropped")
        if not r["event_id"]:
            return "That looks like a duplicate of a report you sent a few minutes ago, so I skipped it."
        extra = f" with {len(pending) + len(file_ids)} photo(s)" if (pending or file_ids) else ""
        ask = " Please also send a short text summary - voice notes aren't transcribed yet." if text.startswith("(voice note") else ""
        return f"✅ Report received{extra} (#{r['update_id']}). Thank you!{ask}"

    if role in PERMISSIONS["chat.send"]:
        if not text:
            _record(m, user.id, pid, "ignored:media_from_owner")
            return "I can't take photos from owners yet - please describe what you need in text."
        await owner_chat(rt, pid, text, user.id, user.name or user.email, role, channel=m.channel)
        _record(m, user.id, pid, "chat")
        return None  # the project manager's answer is sent back when ready

    _record(m, user.id, pid, f"dropped:role_{role}")
    return "Your role on this project is read-only, so I can't act on messages from you here."
