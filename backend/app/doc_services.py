"""Document / RFI / submittal operations shared by the web API, WhatsApp and email (phase 10)."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from .config import settings
from .db import session_scope, utcnow
from .docs import (
    default_review_due,
    default_rfi_due,
    document_view,
    next_number,
    register_document,
    rfi_view,
    submittal_view,
)
from .models import RFI, Activity, Document, Membership, Submittal, User
from .notify import notify_background, send_direct
from .services import ServiceError

if TYPE_CHECKING:
    from .harness.runtime import Runtime


def _send_later(*args) -> None:
    asyncio.get_running_loop().run_in_executor(None, lambda: send_direct(*args))


async def upload_document(rt: "Runtime", pid: int, data: bytes, filename: str, content_type: str | None, *,
                          user_id: int | None, by: str, source: str = "upload", **meta) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        reg = register_document(s, pid, data, filename, content_type, uploaded_by=user_id, source=source, **meta)
        view = document_view(s, reg.document)
        rev = reg.version.revision
        searchable = reg.version.text_chars > 0
    rt.publish(pid, "documents", {"document_id": view["id"]})
    await rt.emit(pid, source="system", kind="document_uploaded",
                  payload={"document": view["ref"], "number": view["number"] or "(unnumbered)", "title": view["title"],
                           "revision": rev, "new_document": reg.new_document, "supersedes": reg.superseded_revision,
                           "searchable": searchable, "by": by, "via": source},
                  thread_key=f"document:{view['id']}", thread_title=f"Document {view['number'] or view['title']}")
    return {**view, "new_document": reg.new_document, "supersedes": reg.superseded_revision}


async def create_rfi(rt: "Runtime", pid: int, *, subject: str, question: str, user_id: int | None, by: str, role: str,
                     contractor_id: int | None = None, due_date: date | None = None, activity_id: int | None = None,
                     document_ids: list[int] | None = None, channel: str = "web") -> dict[str, Any]:
    if not subject.strip() or not question.strip():
        raise ServiceError(400, "an RFI needs a subject and a question")
    with session_scope(rt.engine) as s:
        if activity_id:
            a = s.get(Activity, activity_id)
            if a is None or a.project_id != pid:
                raise ServiceError(400, "activity not found in this project")
        for did in document_ids or []:
            d = s.get(Document, did)
            if d is None or d.project_id != pid:
                raise ServiceError(400, f"document {did} not found in this project")
        r = RFI(project_id=pid, number=next_number(s, RFI, pid), subject=subject.strip()[:200], question=question.strip(),
                raised_by=user_id, contractor_id=contractor_id, due_date=due_date or default_rfi_due(),
                activity_id=activity_id, document_ids=list(document_ids or []))
        s.add(r)
        s.flush()
        view = rfi_view(r)
    rt.publish(pid, "rfis", {"rfi_id": view["id"]})
    payload: dict[str, Any] = {"rfi": view["ref"], "number": view["number"], "subject": view["subject"], "due_date": view["due_date"],
                               "raised_by": by, "role": role, "channel": channel, "text": question}
    if contractor_id:
        payload["contractor_id"] = contractor_id
    # The question is free text from a person: as `text` on a contractor-sourced event it is fenced as untrusted.
    await rt.emit(pid, source="contractor" if role == "contractor" else "owner", kind="rfi_raised", payload=payload,
                  thread_key=f"rfi:{view['id']}", thread_title=f"RFI #{view['number']} {view['subject']}")
    notify_background(pid, "warning", f"RFI #{view['number']}: {view['subject']}",
                      f"{question.strip()[:800]}\n\nRaised by {by}. Answer due {view['due_date']}.",
                      f"{settings.app_base_url}/projects/{pid}/documents?tab=rfis", perm="rfi.answer")
    return view


async def answer_rfi(rt: "Runtime", rfi_id: int, answer: str, user_id: int, by: str, close: bool = False) -> dict[str, Any]:
    if not answer.strip():
        raise ServiceError(400, "the answer is empty")
    with session_scope(rt.engine) as s:
        r = s.get(RFI, rfi_id)
        if r is None:
            raise ServiceError(404, "RFI not found")
        if r.status in ("closed", "void"):
            raise ServiceError(409, f"RFI is {r.status}")
        r.answer, r.answered_by, r.answered_at = answer.strip(), user_id, utcnow()
        r.status = "closed" if close else "answered"
        r.ball_in_court = "raiser"
        s.add(r)
        view, pid = rfi_view(r), r.project_id
        raiser = s.get(User, r.raised_by) if r.raised_by else None
    rt.publish(pid, "rfis", {"rfi_id": rfi_id})
    await rt.emit(pid, source="owner", kind="rfi_answered",
                  payload={"rfi": view["ref"], "number": view["number"], "subject": view["subject"], "answered_by": by,
                           "answer": answer.strip()[:2000], "closed": close},
                  thread_key=f"rfi:{rfi_id}")
    if raiser is not None and raiser.id != user_id:
        ch = "whatsapp" if raiser.phone else "email"
        body = f"RFI #{view['number']} \"{view['subject']}\" was answered by {by}:\n\n{answer.strip()[:1500]}"
        _send_later(ch, f"RFI #{view['number']} answered", body, raiser.id, "", pid)
    return view


async def close_rfi(rt: "Runtime", rfi_id: int, void: bool = False) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        r = s.get(RFI, rfi_id)
        if r is None:
            raise ServiceError(404, "RFI not found")
        r.status = "void" if void else "closed"
        s.add(r)
        view, pid = rfi_view(r), r.project_id
    rt.publish(pid, "rfis", {"rfi_id": rfi_id})
    return view


async def submit_submittal(rt: "Runtime", pid: int, *, title: str, spec_section: str, contractor_id: int | None,
                           user_id: int | None, by: str, file: tuple[bytes, str, str | None] | None = None,
                           activity_id: int | None = None, required_by: date | None = None,
                           due_date: date | None = None) -> dict[str, Any]:
    if not title.strip():
        raise ServiceError(400, "a submittal needs a title")
    with session_scope(rt.engine) as s:
        if activity_id:
            a = s.get(Activity, activity_id)
            if a is None or a.project_id != pid:
                raise ServiceError(400, "activity not found in this project")
        number = next_number(s, Submittal, pid)
        doc_id = None
        if file is not None:
            data, fname, ctype = file
            reg = register_document(s, pid, data, fname, ctype, number=f"SUB-{number:03d}", title=title, revision="R0",
                                    doc_type="submittal", uploaded_by=user_id, source="submittal")
            doc_id = reg.document.id
        x = Submittal(project_id=pid, number=number, title=title.strip()[:200], spec_section=spec_section.strip()[:60],
                      contractor_id=contractor_id, status="submitted", due_date=due_date or default_review_due(),
                      required_by=required_by, document_id=doc_id, activity_id=activity_id)
        s.add(x)
        s.flush()
        view = submittal_view(x)
    rt.publish(pid, "submittals", {"submittal_id": view["id"]})
    await rt.emit(pid, source="system", kind="submittal_submitted",
                  payload={"submittal": view["ref"], "number": view["number"], "title": view["title"], "spec_section": spec_section,
                           "revision": 0, "document": f"document:{doc_id}" if doc_id else None, "by": by,
                           "activity": f"activity:{activity_id}" if activity_id else None},
                  thread_key=f"submittal:{view['id']}", thread_title=f"Submittal #{number} {title}")
    notify_background(pid, "info", f"Submittal #{number} for review: {title}", f"Spec {spec_section or '-'}; review due {view['due_date']}.",
                      f"{settings.app_base_url}/projects/{pid}/documents?tab=submittals", perm="submittal.review")
    return view


REVIEW_STATUSES = ("under_review", "approved", "approved_as_noted", "revise_resubmit", "rejected")


async def review_submittal(rt: "Runtime", sub_id: int, status: str, comments: str, user_id: int, by: str) -> dict[str, Any]:
    if status not in REVIEW_STATUSES:
        raise ServiceError(400, f"status must be one of {REVIEW_STATUSES}")
    if status in ("revise_resubmit", "rejected") and not comments.strip():
        raise ServiceError(400, "say what must change (comments are required for revise & resubmit / rejected)")
    with session_scope(rt.engine) as s:
        x = s.get(Submittal, sub_id)
        if x is None:
            raise ServiceError(404, "submittal not found")
        if x.status in ("approved", "approved_as_noted", "rejected"):
            raise ServiceError(409, f"submittal already {x.status}")
        x.status, x.reviewer_comments = status, comments.strip()
        if status != "under_review":
            x.decided_by, x.decided_at = user_id, utcnow()
        s.add(x)
        view, pid, cid = submittal_view(x), x.project_id, x.contractor_id
        people = []
        if cid:
            for m in s.exec(select(Membership).where(Membership.project_id == pid, Membership.contractor_id == cid)).all():
                u = s.get(User, m.user_id)
                if u and u.is_active:
                    people.append((u.id, "whatsapp" if u.phone else "email"))
    rt.publish(pid, "submittals", {"submittal_id": sub_id})
    rt.publish(pid, "kpi", {})
    await rt.emit(pid, source="owner", kind="submittal_reviewed",
                  payload={"submittal": view["ref"], "number": view["number"], "title": view["title"], "status": status,
                           "comments": comments.strip()[:1500], "by": by},
                  thread_key=f"submittal:{sub_id}")
    if status != "under_review":
        body = f"Submittal #{view['number']} \"{view['title']}\": {status.replace('_', ' ').upper()}"
        if comments.strip():
            body += f"\n\nComments: {comments.strip()[:1200]}"
        for uid, ch in people:
            _send_later(ch, f"Submittal #{view['number']} reviewed", body, uid, "", pid)
    return view


async def resubmit_submittal(rt: "Runtime", sub_id: int, file: tuple[bytes, str, str | None], notes: str,
                             user_id: int | None, by: str) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        x = s.get(Submittal, sub_id)
        if x is None:
            raise ServiceError(404, "submittal not found")
        if x.status not in ("revise_resubmit", "rejected"):
            raise ServiceError(409, f"only revise-and-resubmit or rejected submittals can be resubmitted (status {x.status})")
        x.revision += 1
        data, fname, ctype = file
        reg = register_document(s, x.project_id, data, fname, ctype, number=f"SUB-{x.number:03d}", title=x.title,
                                revision=f"R{x.revision}", doc_type="submittal", uploaded_by=user_id, source="submittal", notes=notes)
        x.document_id = reg.document.id
        x.status, x.due_date, x.decided_at, x.decided_by = "submitted", default_review_due(), None, None
        s.add(x)
        view, pid = submittal_view(x), x.project_id
    rt.publish(pid, "submittals", {"submittal_id": sub_id})
    await rt.emit(pid, source="system", kind="submittal_submitted",
                  payload={"submittal": view["ref"], "number": view["number"], "title": view["title"], "revision": view["revision"],
                           "document": f"document:{view['document_id']}", "by": by, "notes": notes[:500]},
                  thread_key=f"submittal:{sub_id}")
    return view
