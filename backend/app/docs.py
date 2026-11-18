"""Document register: versions, text extraction and search (BM25), RFIs and submittals.

Search is a small in-process BM25 over page-sized chunks so it behaves the same
on Postgres and SQLite; at this scale (thousands of pages per project) that is
fast enough. Results always cite `document:<id>#p<page>` and say whether the
revision is current or superseded.
"""
from __future__ import annotations

import io
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlmodel import Session, col, func, select

from .db import today, utcnow
from .models import RFI, DocChunk, Document, DocumentVersion, StoredFile, Submittal
from .storage import save_file

log = logging.getLogger(__name__)

CHUNK_CHARS = 1500
TEXT_TYPES = ("text/plain", "text/markdown", "text/csv")
NUMBER_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{1,4}-?\d{2,5}(?:-\d{1,4})?)(?![A-Z0-9])")  # "_" counts as a separator
REV_RE = re.compile(r"(?:^|[\s_\-.(])(?:rev|r)[\s_\-.]?([0-9]{1,2}|[A-Z])(?=$|[\s_\-.)])", re.I)


# --------------------------------------------------------------------------- extraction


def extract_text(data: bytes, mime: str, filename: str) -> list[str]:
    """Return text per page ([] if nothing extractable)."""
    name = filename.lower()
    if mime == "application/pdf" or name.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return [(p.extract_text() or "").strip() for p in reader.pages]
        except Exception as e:  # noqa: BLE001 - damaged/encrypted PDFs are still stored
            log.warning("PDF text extraction failed for %s: %s", filename, e)
            return []
    if mime in TEXT_TYPES or name.endswith((".txt", ".md", ".csv")):
        return [data.decode("utf-8", errors="replace")]
    return []


def guess_number_and_revision(filename: str) -> tuple[str, str]:
    stem = filename.rsplit(".", 1)[0]
    num = NUMBER_RE.search(stem.upper())
    rev = REV_RE.search(stem)
    return (num.group(1) if num else ""), (f"R{rev.group(1).upper()}" if rev else "")


def _chunks(pages: list[str]) -> list[tuple[int, int, str]]:
    out = []
    for p, text in enumerate(pages, start=1):
        text = re.sub(r"[ \t]+", " ", text).strip()
        for i in range(0, len(text), CHUNK_CHARS):
            piece = text[i : i + CHUNK_CHARS].strip()
            if piece:
                out.append((p, i // CHUNK_CHARS, piece))
    return out


# --------------------------------------------------------------------------- register


@dataclass
class Registered:
    document: Document
    version: DocumentVersion
    new_document: bool
    superseded_revision: str | None


def register_document(
    s: Session,
    project_id: int,
    data: bytes,
    filename: str,
    content_type: str | None = None,
    *,
    number: str = "",
    title: str = "",
    revision: str = "",
    discipline: str = "",
    doc_type: str = "",
    uploaded_by: int | None = None,
    source: str = "upload",
    notes: str = "",
) -> Registered:
    """Store a file as a new document or as a new revision of an existing one (matched by number)."""
    g_num, g_rev = guess_number_and_revision(filename)
    number = (number or g_num).strip().upper()
    f = save_file(data, filename, project_id, uploaded_by=uploaded_by, content_type=content_type, session=s)
    pages = extract_text(data, f.content_type, filename)

    doc = None
    if number:
        doc = s.exec(select(Document).where(Document.project_id == project_id, Document.number == number)).first()
    superseded = None
    new_doc = doc is None
    if doc is None:
        doc = Document(project_id=project_id, number=number, title=title or filename.rsplit(".", 1)[0],
                       discipline=discipline, doc_type=doc_type or "drawing", status="for_review")
        s.add(doc)
        s.flush()
    else:
        if doc.current_version_id:
            prev = s.get(DocumentVersion, doc.current_version_id)
            superseded = prev.revision if prev else None
        if title:
            doc.title = title
        if discipline:
            doc.discipline = discipline
        doc.status = "for_review"  # a new revision needs review again
        doc.classified = False
    n_versions = s.exec(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == doc.id)).one()
    v = DocumentVersion(document_id=doc.id, revision=revision or g_rev or f"R{n_versions}", file_id=f.id,
                        uploaded_by=uploaded_by, source=source, notes=notes, pages=len(pages),
                        text_chars=sum(len(p) for p in pages))
    s.add(v)
    s.flush()
    for page, seq, text in _chunks(pages):
        s.add(DocChunk(project_id=project_id, version_id=v.id, page=page, seq=seq, text=text))
    doc.current_version_id = v.id
    doc.updated_at = utcnow()
    s.add(doc)
    s.flush()
    return Registered(doc, v, new_doc, superseded)


def document_view(s: Session, d: Document, with_versions: bool = False) -> dict[str, Any]:
    v = s.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
    out = {
        "id": d.id, "ref": f"document:{d.id}", "number": d.number, "title": d.title, "discipline": d.discipline,
        "doc_type": d.doc_type, "status": d.status, "classified": d.classified,
        "design_id": d.design_id, "milestone_id": d.milestone_id, "activity_id": d.activity_id,
        "revision": v.revision if v else None, "version_id": v.id if v else None, "pages": v.pages if v else 0,
        "searchable": bool(v and v.text_chars), "updated_at": d.updated_at.isoformat(),
    }
    if with_versions:
        vs = s.exec(select(DocumentVersion).where(DocumentVersion.document_id == d.id).order_by(col(DocumentVersion.id).desc())).all()
        out["versions"] = [{"id": x.id, "revision": x.revision, "file_id": x.file_id, "source": x.source, "notes": x.notes,
                            "pages": x.pages, "current": x.id == d.current_version_id, "created_at": x.created_at.isoformat()} for x in vs]
    return out


# --------------------------------------------------------------------------- search


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
STOP = set("a an and are as at be by for from has in is it of on or that the this to was were will with".split())


def _tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) > 1]


def search(s: Session, project_id: int, query: str, k: int = 8, include_superseded: bool = False) -> list[dict[str, Any]]:
    q = _tokens(query)
    if not q:
        return []
    rows = s.exec(
        select(DocChunk, DocumentVersion, Document)
        .join(DocumentVersion, DocumentVersion.id == DocChunk.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocChunk.project_id == project_id)
    ).all()
    if not include_superseded:
        rows = [r for r in rows if r[1].id == r[2].current_version_id]
    if not rows:
        return []
    docs = [(_tokens(c.text), c, v, d) for c, v, d in rows]
    n = len(docs)
    avgdl = sum(len(t) for t, *_ in docs) / n or 1
    df = Counter(term for t, *_ in docs for term in set(t))
    k1, b = 1.4, 0.75
    scored = []
    for toks, c, v, d in docs:
        tf = Counter(toks)
        score = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avgdl))
        if score > 0:
            scored.append((score, c, v, d))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c, v, d in scored[:k]:
        out.append({
            "cite": f"document:{d.id}#p{c.page}", "document": f"document:{d.id}", "number": d.number, "title": d.title,
            "revision": v.revision, "current": v.id == d.current_version_id, "status": d.status, "page": c.page,
            "score": round(score, 3), "excerpt": _excerpt(c.text, q),
        })
    return out


def _excerpt(text: str, terms: list[str], width: int = 320) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(pos - width // 3, 0)
    piece = text[start : start + width].strip()
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


# --------------------------------------------------------------------------- RFIs & submittals


def next_number(s: Session, model, project_id: int) -> int:
    cur = s.exec(select(func.max(model.number)).where(model.project_id == project_id)).one()
    return int(cur or 0) + 1


def rfi_view(r: RFI) -> dict[str, Any]:
    return {
        "id": r.id, "ref": f"rfi:{r.id}", "number": r.number, "subject": r.subject, "question": r.question,
        "status": r.status, "ball_in_court": r.ball_in_court, "due_date": r.due_date.isoformat(),
        "overdue": r.status == "open" and r.due_date < today(), "answer": r.answer,
        "answered_at": r.answered_at.isoformat() if r.answered_at else None, "cost_impact": r.cost_impact,
        "time_impact": r.time_impact, "activity_id": r.activity_id, "contractor_id": r.contractor_id,
        "raised_by": r.raised_by, "document_ids": r.document_ids, "created_at": r.created_at.isoformat(),
    }


def submittal_view(x: Submittal) -> dict[str, Any]:
    return {
        "id": x.id, "ref": f"submittal:{x.id}", "number": x.number, "title": x.title, "spec_section": x.spec_section,
        "contractor_id": x.contractor_id, "revision": x.revision, "status": x.status, "due_date": x.due_date.isoformat(),
        "required_by": x.required_by.isoformat() if x.required_by else None,
        "overdue": x.status in ("submitted", "under_review") and x.due_date < today(),
        "document_id": x.document_id, "activity_id": x.activity_id, "reviewer_comments": x.reviewer_comments,
        "decided_at": x.decided_at.isoformat() if x.decided_at else None, "created_at": x.created_at.isoformat(),
    }


APPROVED_SUBMITTAL = {"approved", "approved_as_noted"}
OPEN_SUBMITTAL = {"submitted", "under_review", "revise_resubmit"}
DEFAULT_RFI_DAYS = 7
DEFAULT_REVIEW_DAYS = 10


def default_rfi_due():
    return today() + timedelta(days=DEFAULT_RFI_DAYS)


def default_review_due():
    return today() + timedelta(days=DEFAULT_REVIEW_DAYS)


def file_of(s: Session, version: DocumentVersion) -> StoredFile:
    return s.get(StoredFile, version.file_id)
