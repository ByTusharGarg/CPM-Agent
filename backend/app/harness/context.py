"""Context-as-cache + reference graph ("Chain of Recall").

* Every record is addressable as "<kind>:<id>".
* `lookup(ref)` returns a record plus the refs of related records, so an agent
  can walk the graph hop-by-hop instead of carrying everything in context.
* Long fields are "spilled": truncated inline with a pointer to fetch the rest.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlmodel import Session, SQLModel, col, select

from ..models import (
    RFI,
    Activity,
    Document,
    Submittal,
    ActionLog,
    Dependency,
    Alert,
    ChatMessage,
    Contractor,
    CostEntry,
    DailyUpdate,
    Decision,
    DesignDeliverable,
    Event,
    Milestone,
    ProcurementItem,
    Project,
    Report,
    Subscription,
    Thread,
    WorkItem,
)

KINDS: dict[str, type[SQLModel]] = {
    "project": Project,
    "milestone": Milestone,
    "procurement": ProcurementItem,
    "activity": Activity,
    "document": Document,
    "rfi": RFI,
    "submittal": Submittal,
    "design": DesignDeliverable,
    "contractor": Contractor,
    "update": DailyUpdate,
    "cost": CostEntry,
    "alert": Alert,
    "decision": Decision,
    "work_item": WorkItem,
    "thread": Thread,
    "event": Event,
    "action": ActionLog,
    "report": Report,
}
HIDDEN_FIELDS = {"transcript", "inbox", "submit_token"}
SPILL_AT = 1200


def ref_of(row: SQLModel) -> str:
    for k, m in KINDS.items():
        if isinstance(row, m):
            return f"{k}:{row.id}"
    return f"?:{getattr(row, 'id', '?')}"


def to_plain(row: SQLModel, spill: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"ref": ref_of(row)}
    for k, v in row.model_dump().items():
        if k in HIDDEN_FIELDS:
            continue
        if isinstance(v, (date, datetime)):
            v = v.isoformat()
        if spill and isinstance(v, str) and len(v) > SPILL_AT:
            v = v[:SPILL_AT] + f"... [truncated {len(v) - SPILL_AT} chars; lookup('{out['ref']}', full=true) for all]"
        out[k] = v
    return out


def parse_ref(ref: str) -> tuple[str, int]:
    kind, _, rid = ref.strip().partition(":")
    if kind not in KINDS or not rid.isdigit():
        raise ValueError(f"Bad ref '{ref}'. Use <kind>:<id> with kind in {sorted(KINDS)}")
    return kind, int(rid)


def lookup(s: Session, project_id: int, ref: str, full: bool = False) -> dict[str, Any]:
    kind, rid = parse_ref(ref)
    row = s.get(KINDS[kind], rid)
    if row is None or (kind != "project" and getattr(row, "project_id", None) != project_id) or (
        kind == "project" and rid != project_id
    ):
        raise ValueError(f"{ref} not found in this project")
    data = to_plain(row, spill=not full)
    data["related"] = _related(s, kind, row)
    return data


def _related(s: Session, kind: str, row: Any) -> list[str]:
    rel: list[str] = []
    pid = getattr(row, "project_id", None) or row.id

    def refs(model, *conds, k: str):
        return [f"{k}:{r.id}" for r in s.exec(select(model).where(*conds)).all()]

    if kind == "document":
        from ..models import DocumentVersion

        rel += [f"version:{v.revision}" for v in s.exec(select(DocumentVersion).where(DocumentVersion.document_id == row.id)).all()]
        for ref in (f"design:{row.design_id}" if row.design_id else "", f"activity:{row.activity_id}" if row.activity_id else "",
                    f"milestone:{row.milestone_id}" if row.milestone_id else ""):
            rel.append(ref)
        rel += refs(Submittal, Submittal.document_id == row.id, k="submittal")
    elif kind == "rfi":
        rel += [f"document:{d}" for d in (row.document_ids or [])]
        rel.append(f"activity:{row.activity_id}" if row.activity_id else "")
        rel.append(f"contractor:{row.contractor_id}" if row.contractor_id else "")
    elif kind == "submittal":
        rel += [f"document:{row.document_id}" if row.document_id else "", f"activity:{row.activity_id}" if row.activity_id else "",
                f"contractor:{row.contractor_id}" if row.contractor_id else ""]
    elif kind == "activity":
        rel.append(f"milestone:{row.milestone_id}" if row.milestone_id else "")
        rel += [f"activity:{d.predecessor_id} ({d.type}{d.lag_days:+d}) -> this" for d in
                s.exec(select(Dependency).where(Dependency.successor_id == row.id)).all()]
        rel += [f"this -> activity:{d.successor_id} ({d.type}{d.lag_days:+d})" for d in
                s.exec(select(Dependency).where(Dependency.predecessor_id == row.id)).all()]
        rel += refs(ProcurementItem, ProcurementItem.activity_id == row.id, k="procurement")
        rel += refs(DesignDeliverable, DesignDeliverable.activity_id == row.id, k="design")
        rel += refs(RFI, RFI.activity_id == row.id, k="rfi")
        rel += refs(Submittal, Submittal.activity_id == row.id, k="submittal")
    elif kind == "milestone":
        rel += refs(Activity, Activity.milestone_id == row.id, k="activity")
        rel += [f"milestone:{d}" for d in (row.depends_on or [])]
        rel += [  # successors
            f"milestone:{m.id}"
            for m in s.exec(select(Milestone).where(Milestone.project_id == pid)).all()
            if row.id in (m.depends_on or [])
        ]
        rel += refs(ProcurementItem, ProcurementItem.milestone_id == row.id, k="procurement")
        rel += refs(DesignDeliverable, DesignDeliverable.milestone_id == row.id, k="design")
        if row.contractor_id:
            rel.append(f"contractor:{row.contractor_id}")
    elif kind in ("procurement", "design", "cost"):
        if row.milestone_id:
            rel.append(f"milestone:{row.milestone_id}")
    elif kind == "contractor":
        rel += refs(Milestone, Milestone.contractor_id == row.id, k="milestone")
        ups = s.exec(
            select(DailyUpdate).where(DailyUpdate.contractor_id == row.id).order_by(col(DailyUpdate.id).desc()).limit(5)
        ).all()
        rel += [f"update:{u.id}" for u in ups]
    elif kind == "update":
        rel.append(f"contractor:{row.contractor_id}")
    elif kind == "alert":
        rel += list(row.refs or [])
        rel += refs(Decision, Decision.alert_id == row.id, k="decision")
        rel += refs(WorkItem, WorkItem.alert_id == row.id, k="work_item")
        if row.thread_id:
            rel.append(f"thread:{row.thread_id}")
    elif kind == "decision":
        if row.alert_id:
            rel.append(f"alert:{row.alert_id}")
    elif kind == "work_item":
        if row.thread_id:
            rel.append(f"thread:{row.thread_id}")
        if row.alert_id:
            rel.append(f"alert:{row.alert_id}")
        rel += refs(ActionLog, ActionLog.work_item_id == row.id, k="action")
    elif kind == "thread":
        rel += [
            f"work_item:{sub.work_item_id}"
            for sub in s.exec(select(Subscription).where(Subscription.thread_id == row.id)).all()
        ]
        evs = s.exec(select(Event).where(Event.thread_id == row.id).order_by(col(Event.id).desc()).limit(10)).all()
        rel += [f"event:{e.id}" for e in evs]
        rel.append(row.key)
    elif kind == "event":
        if row.thread_id:
            rel.append(f"thread:{row.thread_id}")
    elif kind == "action" and row.work_item_id:
        rel.append(f"work_item:{row.work_item_id}")
    return list(dict.fromkeys(r for r in rel if r))


def list_records(
    s: Session, project_id: int, kind: str, status: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    if kind not in KINDS or kind == "project":
        raise ValueError(f"kind must be one of {sorted(k for k in KINDS if k != 'project')}")
    model = KINDS[kind]
    q = select(model).where(model.project_id == project_id)
    if status and hasattr(model, "status"):
        q = q.where(model.status == status)
    q = q.order_by(col(model.id).desc()).limit(max(1, min(limit, 100)))
    rows = list(s.exec(q).all())
    return [_brief(kind, r) for r in rows]


def _brief(kind: str, r: Any) -> dict[str, Any]:
    """One-line summaries for lists; full record is one lookup away."""
    d = to_plain(r)
    keep = {
        "milestone": ["name", "planned_start", "planned_end", "percent_complete", "is_critical", "contractor_id"],
        "activity": ["name", "milestone_id", "duration_days", "planned_start", "planned_finish", "percent_complete"],
        "document": ["number", "title", "discipline", "doc_type", "status", "activity_id"],
        "rfi": ["number", "subject", "status", "due_date", "ball_in_court", "time_impact", "activity_id"],
        "submittal": ["number", "title", "status", "revision", "due_date", "required_by", "activity_id", "contractor_id"],
        "procurement": ["item", "vendor", "status", "required_on_site", "expected_delivery", "lead_time_days", "milestone_id"],
        "design": ["title", "discipline", "status", "due_date", "milestone_id"],
        "contractor": ["name", "trade", "allowlisted"],
        "update": ["contractor_id", "report_date", "processed"],
        "cost": ["kind", "amount", "description", "entry_date", "milestone_id"],
        "alert": ["severity", "title", "status", "rule_key", "created_at"],
        "decision": ["alert_id", "choice", "created_at"],
        "work_item": ["worker", "title", "status", "turns_used", "updated_at"],
        "thread": ["key", "title", "last_event_at"],
        "event": ["source", "kind", "status", "created_at"],
        "action": ["actor", "action", "mode", "status", "created_at"],
        "report": ["period", "created_at"],
    }[kind]
    return {"ref": d["ref"], **{k: d.get(k) for k in keep}}


def recent_chat(s: Session, project_id: int, n: int = 12) -> list[dict[str, Any]]:
    rows = s.exec(
        select(ChatMessage)
        .where(ChatMessage.project_id == project_id, ChatMessage.kind != "activity")
        .order_by(col(ChatMessage.id).desc())
        .limit(n)
    ).all()
    out = []
    for m in reversed(rows):
        text = m.content if len(m.content) < 600 else m.content[:600] + " ...[truncated]"
        out.append({"role": m.role, "kind": m.kind, "at": m.created_at.isoformat(timespec="minutes"), "text": text, **({"meta": m.meta} if m.meta else {})})
    return out
