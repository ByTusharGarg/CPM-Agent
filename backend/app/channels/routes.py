"""Webhook endpoints for WhatsApp and email, plus the email action confirm page."""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlmodel import select

from ..config import settings
from ..db import session_scope, utcnow
from ..models import ActionLog, Alert, Notification, User
from ..notify import CHANNELS, send_direct
from .intake import Inbound, already_seen, handle_inbound
from .links import read_token
from .whatsapp import verify_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/channels")


def _rt(request: Request):
    return request.app.state.rt


async def _reply(channel: str, user_addr: str, text: str, subject: str = "Site Manager") -> None:
    from .intake import find_user

    u = find_user(channel, user_addr)
    await asyncio.to_thread(send_direct, channel, subject, text, u.id if u else None, "" if u else user_addr)


# --------------------------------------------------------------------------- WhatsApp


@router.get("/whatsapp/webhook")
def whatsapp_verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and settings.wa_verify_token and token == settings.wa_verify_token:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "verification failed")


def _parse_whatsapp(payload: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    messages, statuses = [], []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages.extend(value.get("messages", []))
            statuses.extend(value.get("statuses", []))
    return messages, statuses


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403, "bad signature")
    payload = await request.json()
    messages, statuses = _parse_whatsapp(payload)

    for st in statuses:  # delivery receipts -> outbox
        with session_scope() as s:
            n = s.exec(select(Notification).where(Notification.provider_id == st.get("id"))).first()
            if n is not None:
                if st.get("status") == "failed":
                    n.status, n.error = "failed", str(st.get("errors", ""))[:500]
                elif st.get("status") in ("delivered", "read"):
                    n.status = st["status"]
                s.add(n)

    wa = CHANNELS["whatsapp"]
    handled = 0
    for msg in messages:
        mid, sender, mtype = msg.get("id", ""), msg.get("from", ""), msg.get("type", "")
        if not mid or already_seen(mid):
            continue
        m = Inbound(channel="whatsapp", provider_id=mid, from_addr=sender, kind=mtype)
        if mtype == "text":
            m.text = msg.get("text", {}).get("body", "")
        elif mtype == "interactive":
            reply = msg.get("interactive", {})
            m.kind = "button"
            m.button_id = (reply.get("button_reply") or reply.get("list_reply") or {}).get("id", "")
        elif mtype == "button":  # quick-reply on a template
            m.text = msg.get("button", {}).get("text", "")
        elif mtype in ("image", "audio", "document", "video"):
            media = msg.get(mtype, {})
            m.text = media.get("caption", "")
            try:
                data, mime = await asyncio.to_thread(wa.download_media, media["id"])
                ext = (mime.split("/")[-1].split(";")[0] or "bin")[:5]
                m.files.append((data, mime, media.get("filename") or f"{mtype}-{mid[-8:]}.{ext}"))
            except Exception as e:  # noqa: BLE001
                log.warning("WhatsApp media %s not downloaded: %s", media.get("id"), e)
        else:
            m.text = ""
        reply_text = await handle_inbound(_rt(request), m)
        handled += 1
        if reply_text:
            await _reply("whatsapp", sender, reply_text)
    return {"ok": True, "handled": handled}


# --------------------------------------------------------------------------- email inbound


REF_RE = re.compile(r"\[CPM ((?:alert|action):\d+)\]")


def _strip_quoted(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:$", line.strip()) or line.strip() == "-- ":
            break
        out.append(line)
    return "\n".join(out).strip()


def _email_addr(raw: str) -> str:
    m = re.search(r"<([^>]+)>", raw or "")
    return (m.group(1) if m else (raw or "")).strip().lower()


@router.post("/email/inbound/{secret}")
async def email_inbound(secret: str, request: Request) -> dict[str, Any]:
    """Accepts SendGrid Inbound Parse or Mailgun route posts (multipart form)."""
    if not settings.email_inbound_secret or secret != settings.email_inbound_secret:
        raise HTTPException(403, "bad secret")
    form = await request.form()
    sender = _email_addr(str(form.get("from") or form.get("sender") or ""))
    subject = str(form.get("subject") or "")
    text = str(form.get("text") or form.get("body-plain") or form.get("stripped-text") or "")
    headers = str(form.get("headers") or "")
    mid_m = re.search(r"^Message-I[Dd]:\s*(\S+)", headers, re.M)
    provider_id = form.get("Message-Id") or (mid_m.group(1) if mid_m else None) or \
        "email-" + hashlib.sha256(f"{sender}|{subject}|{text}".encode()).hexdigest()[:32]
    files = []
    for key, val in form.multi_items():
        if hasattr(val, "read") and hasattr(val, "filename"):
            data = await val.read()
            if data and len(data) <= 25 * 1024 * 1024:
                files.append((data, val.content_type or "application/octet-stream", val.filename or key))
    ref = REF_RE.search(subject)
    m = Inbound(channel="email", provider_id=str(provider_id), from_addr=sender, kind="email",
                text=_strip_quoted(text), ref_hint=ref.group(1) if ref else "", files=files)
    reply_text = await handle_inbound(_rt(request), m)
    if reply_text:
        await _reply("email", sender, reply_text, subject=f"Re: {subject}" if subject else "Site Manager")
    return {"ok": True}


# --------------------------------------------------------------------------- email action links

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Site Manager</title><style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:48px auto;padding:0 16px;color:#0f172a}}
.card{{border:1px solid #e2e8f0;border-radius:12px;padding:20px}}button{{background:#0f172a;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-size:15px}}
.muted{{color:#64748b;font-size:14px}}</style></head><body><div class="card">{body}</div></body></html>"""


def _describe(action_id: str) -> tuple[str, str]:
    """(question, button label) for a confirm page."""
    parts = action_id.split(":")
    with session_scope() as s:
        if parts[0] == "alert":
            al = s.get(Alert, int(parts[1]))
            if al is None:
                raise ValueError("this decision no longer exists")
            label = al.options[int(parts[3])]["label"] if int(parts[3]) < len(al.options or []) else "?"
            return f"Decision on <b>{html.escape(al.title)}</b>", f"Choose: {html.escape(label)}"
        if parts[0] == "action":
            e = s.get(ActionLog, int(parts[1]))
            if e is None:
                raise ValueError("this request no longer exists")
            from ..harness.authz import ACTIONS

            return f"Request: <b>{html.escape(ACTIONS[e.action].summarize(e.params))}</b>", parts[2].capitalize()
    raise ValueError("unknown action")


@router.get("/email/act", response_class=HTMLResponse)
def email_action_page(t: str) -> HTMLResponse:
    try:
        _, action_id = read_token(t)
        question, label = _describe(action_id)
    except ValueError as e:
        return HTMLResponse(PAGE.format(body=f"<p>{html.escape(str(e))}</p>"), status_code=400)
    body = (f"<p>{question}</p><form method='post'><input type='hidden' name='t' value='{html.escape(t)}'>"
            f"<button>{label}</button></form><p class='muted'>Nothing happens until you press the button.</p>")
    return HTMLResponse(PAGE.format(body=body))


@router.post("/email/act", response_class=HTMLResponse)
async def email_action_confirm(request: Request) -> HTMLResponse:
    from .intake import _decide

    form = await request.form()
    try:
        user_id, action_id = read_token(str(form.get("t", "")))
    except ValueError as e:
        return HTMLResponse(PAGE.format(body=f"<p>{html.escape(str(e))}</p>"), status_code=400)
    with session_scope() as s:
        user = s.get(User, user_id)
    if user is None or not user.is_active:
        return HTMLResponse(PAGE.format(body="<p>Account not active.</p>"), status_code=403)
    ref, _, tail = action_id.rpartition(":")
    ref = ref.removesuffix(":opt")
    choice: int | str = int(tail) if tail.isdigit() else tail
    result = await _decide(_rt(request), user, ref, choice, "email-link")
    return HTMLResponse(PAGE.format(body=f"<p>{html.escape(result)}</p><p class='muted'>{utcnow():%d %b %Y %H:%M} UTC</p>"))
