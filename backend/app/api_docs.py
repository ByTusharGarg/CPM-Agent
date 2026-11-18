"""REST endpoints: document register, search, RFIs, submittals (phase 10)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import col, or_, select

from . import doc_services as ds
from .auth import Access, check, current_user, require
from .db import session_scope
from .docs import document_view, file_of, rfi_view, search, submittal_view
from .models import RFI, Document, DocumentVersion, Submittal, User
from .storage import read_file

router = APIRouter(prefix="/api")
MAX_DOC = 50 * 1024 * 1024
ISSUED = ("approved", "for_construction")


def _rt(request: Request):
    return request.app.state.rt


async def _read(file: UploadFile, limit: int = MAX_DOC) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > limit:
        raise HTTPException(413, f"file too large ({limit // 1024 // 1024} MB max)")
    return data


def _contractor_can_see(acc: Access, d: Document, s) -> bool:
    if d.status in ISSUED:
        return True
    v = s.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
    if v is not None and v.uploaded_by == acc.user.id:
        return True
    sub = s.exec(select(Submittal).where(Submittal.document_id == d.id)).first()
    return bool(sub and sub.contractor_id == acc.contractor_id)


# --------------------------------------------------------------------------- documents


@router.get("/projects/{pid}/documents")
def list_documents(pid: int, status: Optional[str] = None, discipline: Optional[str] = None, doc_type: Optional[str] = None,
                   acc: Access = Depends(require("document.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Document).where(Document.project_id == pid)
        if status:
            q = q.where(Document.status == status)
        if discipline:
            q = q.where(Document.discipline == discipline)
        if doc_type:
            q = q.where(Document.doc_type == doc_type)
        rows = s.exec(q.order_by(Document.number, Document.id)).all()
        if acc.role == "contractor":
            rows = [d for d in rows if _contractor_can_see(acc, d, s)]
        return [document_view(s, d) for d in rows]


@router.post("/projects/{pid}/documents")
async def upload_document(pid: int, request: Request, file: UploadFile = File(...), number: str = Form(""), title: str = Form(""),
                          revision: str = Form(""), discipline: str = Form(""), doc_type: str = Form(""), notes: str = Form(""),
                          acc: Access = Depends(require("document.upload"))) -> dict[str, Any]:
    data = await _read(file)
    return await ds.upload_document(_rt(request), pid, data, file.filename or "document", file.content_type, user_id=acc.user.id,
                                    by=acc.actor, number=number, title=title, revision=revision, discipline=discipline,
                                    doc_type=doc_type, notes=notes)


@router.get("/projects/{pid}/documents/search")
def search_documents(pid: int, q: str, k: int = 10, all_revisions: bool = False,
                     acc: Access = Depends(require("document.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        hits = search(s, pid, q, max(1, min(k, 30)), all_revisions)
        if acc.role == "contractor":
            hits = [h for h in hits if _contractor_can_see(acc, s.get(Document, int(h["document"].split(":")[1])), s)]
        return hits


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404)
        acc = check(user, d.project_id, "document.view")
        if acc.role == "contractor" and not _contractor_can_see(acc, d, s):
            raise HTTPException(404)
        return document_view(s, d, with_versions=True)


class DocPatch(BaseModel):
    number: Optional[str] = None
    title: Optional[str] = None
    discipline: Optional[str] = None
    doc_type: Optional[str] = None
    status: Optional[Literal["draft", "for_review", "approved", "for_construction", "superseded", "rejected"]] = None


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: int, body: DocPatch, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404)
        check(user, d.project_id, "document.manage")
        if body.number is not None and body.number.strip():
            num = body.number.strip().upper()
            clash = s.exec(select(Document).where(Document.project_id == d.project_id, Document.number == num, Document.id != d.id)).first()
            if clash:
                raise HTTPException(409, f"number {num} is already document:{clash.id}")
            d.number = num
        for k in ("title", "discipline", "doc_type", "status"):
            v = getattr(body, k)
            if v is not None:
                setattr(d, k, v)
        if body.status in ISSUED and d.design_id:
            from .models import DesignDeliverable

            dd = s.get(DesignDeliverable, d.design_id)
            dd.status = "approved"
            s.add(dd)
        d.classified = True
        s.add(d)
        view, pid = document_view(s, d), d.project_id
    _rt(request).publish(pid, "documents", {"document_id": doc_id})
    _rt(request).publish(pid, "kpi", {})
    return view


@router.get("/document-versions/{vid}/file")
def download_version(vid: int, user: User = Depends(current_user)) -> Response:
    with session_scope() as s:
        v = s.get(DocumentVersion, vid)
        d = s.get(Document, v.document_id) if v else None
        if d is None:
            raise HTTPException(404)
        acc = check(user, d.project_id, "document.view")
        if acc.role == "contractor" and not _contractor_can_see(acc, d, s):
            raise HTTPException(404)
        f = file_of(s, v)
    return Response(read_file(f), media_type=f.content_type, headers={"Content-Disposition": f'inline; filename="{f.filename}"'})


# --------------------------------------------------------------------------- RFIs


class RfiIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    question: str = Field(min_length=3, max_length=8000)
    due_date: Optional[date] = None
    activity_id: Optional[int] = None
    document_ids: list[int] = Field(default_factory=list)
    contractor_id: Optional[int] = Field(None, description="Raising on behalf of a contractor (owner/owner's rep)")


@router.get("/projects/{pid}/rfis")
def list_rfis(pid: int, status: Optional[str] = None, acc: Access = Depends(require("document.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(RFI).where(RFI.project_id == pid)
        if status:
            q = q.where(RFI.status == status)
        if acc.role == "contractor":
            q = q.where(or_(RFI.contractor_id == acc.contractor_id, RFI.raised_by == acc.user.id))
        return [rfi_view(r) for r in s.exec(q.order_by(col(RFI.number).desc())).all()]


@router.post("/projects/{pid}/rfis")
async def raise_rfi(pid: int, body: RfiIn, request: Request, acc: Access = Depends(require("rfi.create"))) -> dict[str, Any]:
    contractor_id = acc.contractor_id if acc.role == "contractor" else body.contractor_id
    return await ds.create_rfi(_rt(request), pid, subject=body.subject, question=body.question, user_id=acc.user.id, by=acc.actor,
                               role=acc.role, contractor_id=contractor_id, due_date=body.due_date, activity_id=body.activity_id,
                               document_ids=body.document_ids)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)
    close: bool = False


def _rfi_project(rfi_id: int) -> int:
    with session_scope() as s:
        r = s.get(RFI, rfi_id)
        if r is None:
            raise HTTPException(404, "RFI not found")
        return r.project_id


@router.post("/rfis/{rfi_id}/answer")
async def answer_rfi(rfi_id: int, body: AnswerIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    acc = check(user, _rfi_project(rfi_id), "rfi.answer")
    return await ds.answer_rfi(_rt(request), rfi_id, body.answer, user.id, acc.actor, body.close)


@router.post("/rfis/{rfi_id}/close")
async def close_rfi(rfi_id: int, request: Request, void: bool = False, user: User = Depends(current_user)) -> dict[str, Any]:
    pid = _rfi_project(rfi_id)
    acc = check(user, pid, "rfi.create")
    with session_scope() as s:
        r = s.get(RFI, rfi_id)
        mine = r.raised_by == user.id
    if not (mine or acc.can("rfi.answer")):
        raise HTTPException(403, "only the person who raised it or a reviewer can close an RFI")
    return await ds.close_rfi(_rt(request), rfi_id, void)


# --------------------------------------------------------------------------- submittals


@router.get("/projects/{pid}/submittals")
def list_submittals(pid: int, acc: Access = Depends(require("document.view"))) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = select(Submittal).where(Submittal.project_id == pid)
        if acc.role == "contractor":
            q = q.where(Submittal.contractor_id == acc.contractor_id)
        return [submittal_view(x) for x in s.exec(q.order_by(col(Submittal.number).desc())).all()]


@router.post("/projects/{pid}/submittals")
async def create_submittal(pid: int, request: Request, title: str = Form(...), spec_section: str = Form(""),
                           activity_id: Optional[int] = Form(None), required_by: Optional[date] = Form(None),
                           contractor_id: Optional[int] = Form(None), file: Optional[UploadFile] = File(None),
                           acc: Access = Depends(require("submittal.submit"))) -> dict[str, Any]:
    cid = acc.contractor_id if acc.role == "contractor" else contractor_id
    payload = None
    if file is not None and file.filename:
        payload = (await _read(file), file.filename, file.content_type)
    return await ds.submit_submittal(_rt(request), pid, title=title, spec_section=spec_section, contractor_id=cid, user_id=acc.user.id,
                                     by=acc.actor, file=payload, activity_id=activity_id, required_by=required_by)


class ReviewIn(BaseModel):
    status: Literal["under_review", "approved", "approved_as_noted", "revise_resubmit", "rejected"]
    comments: str = ""


def _sub_project(sub_id: int) -> tuple[int, int | None]:
    with session_scope() as s:
        x = s.get(Submittal, sub_id)
        if x is None:
            raise HTTPException(404, "submittal not found")
        return x.project_id, x.contractor_id


@router.post("/submittals/{sub_id}/review")
async def review_submittal(sub_id: int, body: ReviewIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    pid, _ = _sub_project(sub_id)
    acc = check(user, pid, "submittal.review")
    return await ds.review_submittal(_rt(request), sub_id, body.status, body.comments, user.id, acc.actor)


@router.post("/submittals/{sub_id}/resubmit")
async def resubmit(sub_id: int, request: Request, file: UploadFile = File(...), notes: str = Form(""),
                   user: User = Depends(current_user)) -> dict[str, Any]:
    pid, cid = _sub_project(sub_id)
    acc = check(user, pid, "submittal.submit")
    if acc.role == "contractor" and acc.contractor_id != cid:
        raise HTTPException(403, "not your submittal")
    return await ds.resubmit_submittal(_rt(request), sub_id, (await _read(file), file.filename or "resubmittal", file.content_type),
                                       notes, user.id, acc.actor)
