"""Operations shared by every entry point (web UI, WhatsApp, email)."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from .db import session_scope, today, utcnow
from .harness.authz import _chat_dict
from .models import Alert, ChatMessage, Contractor, DailyUpdate, Decision, StoredFile

if TYPE_CHECKING:
    from .harness.runtime import Runtime


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def record_alert_decision(
    rt: "Runtime", alert_id: int, option_index: int | None, notes: str, by: str, channel: str = "web"
) -> int:
    with session_scope(rt.engine) as s:
        al = s.get(Alert, alert_id)
        if al is None:
            raise ServiceError(404, "alert not found")
        if al.status not in ("awaiting_owner", "open"):
            raise ServiceError(409, f"alert is already {al.status}")
        if option_index is not None:
            if not 0 <= option_index < len(al.options or []):
                raise ServiceError(400, "bad option number")
            opt = al.options[option_index]
            choice = opt["label"]
        else:
            if not notes.strip():
                raise ServiceError(400, "choose an option or write instructions")
            opt, choice = None, "custom: " + notes.strip()[:200]
        dec = Decision(project_id=al.project_id, alert_id=al.id, choice=choice, notes=notes)
        al.status = "resolved"
        al.resolved_at = utcnow()
        s.add(dec)
        s.add(al)
        text = f"Decision on \"{al.title}\": **{choice}**" + (f"\n\n{notes}" if notes else "")
        m = ChatMessage(project_id=al.project_id, role="owner", content=text, kind="text",
                        meta={"alert_id": al.id, "decision": True, "by": by, "channel": channel})
        s.add(m)
        s.flush()
        pid, did, d = al.project_id, dec.id, _chat_dict(m)
    rt.publish(pid, "chat", d)
    rt.publish(pid, "alerts", {})
    await rt.emit(
        pid, source="owner", kind="decision",
        payload={"decision": f"decision:{did}", "alert": f"alert:{alert_id}", "choice": choice, "option": opt,
                 "notes": notes, "by": by, "channel": channel},
        thread_key=f"alert:{alert_id}",
    )
    return did


async def ingest_daily_update(
    rt: "Runtime",
    pid: int,
    contractor_id: int,
    text: str,
    report_date: date | None,
    submitted_by: str,
    file_ids: list[int] | None = None,
    channel: str = "web",
) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        c = s.get(Contractor, contractor_id)
        if c is None or c.project_id != pid:
            raise ServiceError(404, "contractor not found")
        for fid in file_ids or []:
            f = s.get(StoredFile, fid)
            if f is None or f.project_id != pid:
                raise ServiceError(400, f"file {fid} not found in this project")
        u = DailyUpdate(project_id=pid, contractor_id=c.id, report_date=report_date or today(), raw_text=text,
                        parsed={"files": file_ids or [], "submitted_by": submitted_by, "channel": channel})
        s.add(u)
        s.flush()
        uid, cname = u.id, c.name
    eid = await rt.emit(
        pid, source="contractor", kind="daily_update",
        payload={"update": f"update:{uid}", "contractor_id": contractor_id, "contractor": cname,
                 "report_date": str(report_date or today()), "files": [f"file:{f}" for f in (file_ids or [])],
                 "channel": channel, "text": text},
        thread_key=f"contractor:{contractor_id}", thread_title=f"Daily updates - {cname}",
    )
    return {"update_id": uid, "event_id": eid}


async def owner_chat(rt: "Runtime", pid: int, text: str, user_id: int, by: str, role: str, channel: str = "web") -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        m = ChatMessage(project_id=pid, role="owner", content=text, meta={"by": by, "role": role, "channel": channel})
        s.add(m)
        s.flush()
        d = _chat_dict(m)
    rt.publish(pid, "chat", d)
    eid = await rt.emit(pid, source="owner", kind="owner_message",
                        payload={"chat_id": d["id"], "from": by, "role": role, "user_id": user_id, "channel": channel, "text": text})
    return {"message": d, "event_id": eid}


def contractor_files_pending(s, user_id: int, pid: int) -> list[int]:
    """Photos this user sent without a caption, waiting for their next text report (last 6 h)."""
    from datetime import timedelta

    rows = s.exec(
        select(StoredFile).where(
            StoredFile.pending_for_user_id == user_id,
            StoredFile.project_id == pid,
            StoredFile.created_at >= utcnow() - timedelta(hours=6),
        )
    ).all()
    for f in rows:
        f.pending_for_user_id = None
        s.add(f)
    return [f.id for f in rows]
