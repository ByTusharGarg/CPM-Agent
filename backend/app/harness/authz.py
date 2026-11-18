"""Authorization modes for side-effecting actions (DeepSeek: off / observe / auto).

* auto      - execute immediately (audited)
* observe   - do NOT execute; log what would have happened for owner review
* approval  - queue for the owner; executes only when the owner approves
* off       - blocked

Every side effect an agent can cause goes through `perform()`, so the audit
log (ActionLog) is complete. Baseline schedule/budget changes are locked to
`approval` and cannot be promoted to auto.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from ..db import session_scope, today, utcnow
from ..models import (
    ActionLog,
    AuthzPolicy,
    ChatMessage,
    Contractor,
    CostEntry,
    DesignDeliverable,
    Milestone,
    ProcurementItem,
    Project,
)

if TYPE_CHECKING:
    from .runtime import Runtime

MODES = ("off", "observe", "auto", "approval")

DEFAULT_POLICIES: dict[str, str] = {
    "progress.record": "auto",
    "design.update": "auto",
    "procurement.update": "auto",
    "cost.record": "auto",
    "contractor.remind": "auto",
    "procurement.expedite": "observe",
    "contractor.instruct": "observe",
    "schedule.change": "approval",
    "schedule.rebaseline": "approval",
    "budget.change": "approval",
}
LOCKED_APPROVAL = {"schedule.change", "schedule.rebaseline", "budget.change"}


@dataclass
class ActionDef:
    name: str
    description: str
    execute: Callable[[Session, int, dict[str, Any]], str]
    summarize: Callable[[dict[str, Any]], str]


ACTIONS: dict[str, ActionDef] = {}

# Notification ids queued by executors inside a transaction; delivered after commit.
_PENDING_DELIVERY: list[int] = []


def _flush_deliveries() -> None:
    import asyncio

    from ..notify import deliver

    ids = list(_PENDING_DELIVERY)
    _PENDING_DELIVERY.clear()
    if not ids:
        return
    try:
        asyncio.get_running_loop().run_in_executor(None, deliver, ids)
    except RuntimeError:
        deliver(ids)


def action(name: str, description: str, summarize: Callable[[dict[str, Any]], str]):
    def deco(fn: Callable[[Session, int, dict[str, Any]], str]):
        ACTIONS[name] = ActionDef(name, description, fn, summarize)
        return fn

    return deco


def policy_for(s: Session, project_id: int, name: str) -> str:
    if name in LOCKED_APPROVAL:
        return "approval"
    row = s.exec(
        select(AuthzPolicy).where(AuthzPolicy.project_id == project_id, AuthzPolicy.action == name)
    ).first()
    return row.mode if row else DEFAULT_POLICIES.get(name, "observe")


def set_policy(s: Session, project_id: int, name: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if name in LOCKED_APPROVAL and mode != "approval":
        raise ValueError(f"{name} always requires owner approval")
    if name not in ACTIONS:
        raise ValueError(f"unknown action {name}")
    row = s.exec(
        select(AuthzPolicy).where(AuthzPolicy.project_id == project_id, AuthzPolicy.action == name)
    ).first()
    if row is None:
        row = AuthzPolicy(project_id=project_id, action=name, mode=mode)
    row.mode = mode
    s.add(row)
    return mode


def all_policies(s: Session, project_id: int) -> list[dict[str, Any]]:
    return [
        {
            "action": a.name,
            "description": a.description,
            "mode": policy_for(s, project_id, a.name),
            "locked": a.name in LOCKED_APPROVAL,
        }
        for a in ACTIONS.values()
    ]


async def perform(
    rt: "Runtime",
    project_id: int,
    actor: str,
    name: str,
    params: dict[str, Any],
    reason: str = "",
    work_item_id: int | None = None,
) -> str:
    """Gatekeeper for every side effect. Returns a message for the calling agent."""
    adef = ACTIONS[name]
    with session_scope(rt.engine) as s:
        mode = policy_for(s, project_id, name)
        entry = ActionLog(
            project_id=project_id,
            actor=actor,
            action=name,
            params=params,
            mode=mode,
            status="pending",
            reason=reason,
            work_item_id=work_item_id,
        )
        if mode == "off":
            entry.status = "blocked"
            s.add(entry)
            return f"Action {name} is turned off for this project. Nothing was done."
        if mode == "observe":
            entry.status = "observed"
            entry.result = "not executed (observe mode)"
            s.add(entry)
            s.flush()
            msg = (
                f"OBSERVE MODE: '{adef.summarize(params)}' was NOT executed. It is logged as "
                f"action:{entry.id} so the owner can review it and enable auto mode for {name}."
            )
            rt.publish(project_id, "observe", {"action_id": entry.id})
            return msg
        if mode == "approval":
            entry.status = "pending_approval"
            s.add(entry)
            s.flush()
            card = ChatMessage(
                project_id=project_id,
                role="manager",
                kind="approval",
                content=f"Approval needed: {adef.summarize(params)}" + (f"\n\nWhy: {reason}" if reason else ""),
                meta={"action_id": entry.id, "action": name, "params": params},
            )
            s.add(card)
            s.flush()
            rt.publish(project_id, "chat", _chat_dict(card))
            from ..config import settings
            from ..notify import notify_background

            notify_background(
                project_id, "critical" if name in LOCKED_APPROVAL else "warning",
                f"Approval needed: {adef.summarize(params)}", reason,
                f"{settings.app_base_url}/projects/{project_id}",
                perm="action.approve_locked" if name in LOCKED_APPROVAL else "action.approve",
                ref=f"action:{entry.id}",
                actions=[{"id": f"action:{entry.id}:approve", "label": "Approve"},
                         {"id": f"action:{entry.id}:reject", "label": "Reject"}],
            )
            return (
                f"Sent to the owner for approval as action:{entry.id}. It will only take effect if approved; "
                "you will receive an approval_result event."
            )
        # auto
        try:
            entry.result = adef.execute(s, project_id, params)
            entry.status = "executed"
        except Exception as e:
            entry.status = "failed"
            entry.result = f"{type(e).__name__}: {e}"
        s.add(entry)
        s.flush()
        msg = f"{entry.status}: {entry.result} (action:{entry.id})"
    _flush_deliveries()
    rt.publish(project_id, "kpi", {})
    return msg


async def decide_approval(rt: "Runtime", action_id: int, approve: bool, note: str = "", actor: str = "owner") -> ActionLog:
    with session_scope(rt.engine) as s:
        entry = s.get(ActionLog, action_id)
        if entry is None or entry.status != "pending_approval":
            raise ValueError("action is not awaiting approval")
        entry.decided_at = utcnow()
        entry.reason = (entry.reason + "\n" if entry.reason else "") + f"decided by {actor}"
        if approve:
            try:
                entry.result = ACTIONS[entry.action].execute(s, entry.project_id, entry.params)
                entry.status = "approved"
            except Exception as e:
                entry.status = "failed"
                entry.result = f"{type(e).__name__}: {e}"
        else:
            entry.status = "rejected"
            entry.result = note or "rejected by owner"
        s.add(entry)
        project_id = entry.project_id
        summary = ACTIONS[entry.action].summarize(entry.params)
    _flush_deliveries()
    rt.publish(project_id, "kpi", {})
    await rt.emit(
        project_id,
        source="owner",
        kind="approval_result",
        payload={"action_id": action_id, "action": entry.action, "summary": summary, "status": entry.status, "note": note, "result": entry.result, "by": actor},
        thread_key=f"action:{action_id}",
    )
    return entry


def _chat_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "meta": m.meta,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# --------------------------------------------------------------------------- executors


def _get(s: Session, model, rid: int, project_id: int):
    row = s.get(model, rid)
    if row is None or row.project_id != project_id:
        raise ValueError(f"{model.__name__} {rid} not found in this project")
    return row


@action("progress.record", "Record % complete for an activity or milestone (from contractor reports)",
        lambda p: f"Set {'activity:' + str(p['activity_id']) if p.get('activity_id') else 'milestone:' + str(p.get('milestone_id'))} progress to {p['percent_complete']}%")
def _progress(s: Session, pid: int, p: dict[str, Any]) -> str:
    if p.get("activity_id"):
        return _activity_progress(s, pid, p)
    from ..models import Activity

    if s.exec(select(Activity).where(Activity.project_id == pid, Activity.milestone_id == p["milestone_id"])).first():
        raise ValueError("this milestone is scheduled by activities - record progress on its activity (see get_schedule / lookahead)")
    m = _get(s, Milestone, p["milestone_id"], pid)
    new = max(0.0, min(100.0, float(p["percent_complete"])))
    if new < m.percent_complete - 10:
        raise ValueError(f"refusing to reduce progress by more than 10 points ({m.percent_complete} -> {new}); escalate instead")
    old = m.percent_complete
    m.percent_complete = new
    d = date.fromisoformat(p["as_of"]) if p.get("as_of") else today()
    if new > 0 and not m.actual_start:
        m.actual_start = d
    if new >= 100 and not m.actual_end:
        m.actual_end = d
    if p.get("note"):
        m.notes = (m.notes + "\n" if m.notes else "") + f"[{d}] {p['note']}"
    s.add(m)
    return f"{m.name}: {old}% -> {new}%"


def _activity_progress(s: Session, pid: int, p: dict[str, Any]) -> str:
    from ..models import Activity
    from ..tracking.schedule_import import sync_milestone_progress

    a = _get(s, Activity, p["activity_id"], pid)
    new = max(0.0, min(100.0, float(p["percent_complete"])))
    if new < a.percent_complete - 10:
        raise ValueError(f"refusing to reduce progress by more than 10 points ({a.percent_complete} -> {new}); escalate instead")
    old = a.percent_complete
    d = date.fromisoformat(p["as_of"]) if p.get("as_of") else today()
    a.percent_complete = new
    if new > 0 and not a.actual_start:
        a.actual_start = d
    if new >= 100 and not a.actual_finish:
        a.actual_finish = d
    s.add(a)
    s.flush()
    sync_milestone_progress(s, pid)
    return f"{a.name}: {old}% -> {new}%"


@action("schedule.rebaseline", "Make the current forecast the new baseline for every activity (owner approval required)",
        lambda p: f"Re-baseline the whole schedule as '{p.get('name', 'Re-baseline')}'")
def _rebaseline(s: Session, pid: int, p: dict[str, Any]) -> str:
    from ..models import Baseline, BaselineActivity
    from ..tracking.service import project_health

    h = project_health(s, pid)
    if h.schedule is None:
        raise ValueError("no activity network to re-baseline")
    for b in s.exec(select(Baseline).where(Baseline.project_id == pid, Baseline.is_current == True)).all():  # noqa: E712
        b.is_current = False
        s.add(b)
    nb = Baseline(project_id=pid, name=p.get("name") or f"Re-baseline {today()}", source="rebaseline", is_current=True)
    s.add(nb)
    s.flush()
    for r in h.schedule.activities:
        s.add(BaselineActivity(baseline_id=nb.id, activity_id=r.id, start=r.early_start, finish=r.early_finish, duration_days=r.duration))
    proj = s.get(Project, pid)
    if p.get("move_target"):
        proj.target_end_date = h.schedule.project_finish
        s.add(proj)
    return f"New baseline '{nb.name}' with {len(h.schedule.activities)} activities (finish {h.schedule.project_finish})"


@action("design.update", "Update a design deliverable's status/due date",
        lambda p: f"Update design:{p['design_id']} -> {p.get('status') or ''} {p.get('due_date') or ''}".strip())
def _design(s: Session, pid: int, p: dict[str, Any]) -> str:
    d = _get(s, DesignDeliverable, p["design_id"], pid)
    if p.get("status"):
        d.status = p["status"]
    if p.get("due_date"):
        d.due_date = date.fromisoformat(p["due_date"])
    if p.get("revision"):
        d.revision = p["revision"]
    if p.get("note"):
        d.notes = (d.notes + "\n" if d.notes else "") + p["note"]
    s.add(d)
    return f"{d.title}: status={d.status}, due={d.due_date}"


@action("procurement.update", "Update a procurement item's status / expected delivery",
        lambda p: f"Update procurement:{p['item_id']} status={p.get('status')} expected={p.get('expected_delivery')}")
def _procurement(s: Session, pid: int, p: dict[str, Any]) -> str:
    it = _get(s, ProcurementItem, p["item_id"], pid)
    if p.get("status"):
        it.status = p["status"]
    if p.get("expected_delivery"):
        it.expected_delivery = date.fromisoformat(p["expected_delivery"])
    if p.get("po_date"):
        it.po_date = date.fromisoformat(p["po_date"])
    if p.get("note"):
        it.notes = (it.notes + "\n" if it.notes else "") + p["note"]
    s.add(it)
    return f"{it.item}: status={it.status}, expected={it.expected_delivery}"


@action("procurement.expedite", "Ask a vendor to expedite a delivery (may cost extra)",
        lambda p: f"Request vendor to expedite procurement:{p['item_id']} to {p['target_date']} (est. extra cost {p.get('est_cost', 0)})")
def _expedite(s: Session, pid: int, p: dict[str, Any]) -> str:
    it = _get(s, ProcurementItem, p["item_id"], pid)
    it.notes = (it.notes + "\n" if it.notes else "") + f"Expedite requested to {p['target_date']} (est. +{p.get('est_cost', 0)})"
    s.add(it)
    return f"Expedite request recorded for {it.item} (no vendor channel configured - recorded only)"


@action("cost.record", "Record a cost entry (committed / actual / change order)",
        lambda p: f"Record {p['kind']} cost {p['amount']} - {p.get('description', '')}")
def _cost(s: Session, pid: int, p: dict[str, Any]) -> str:
    if p.get("milestone_id"):
        _get(s, Milestone, p["milestone_id"], pid)
    c = CostEntry(
        project_id=pid,
        milestone_id=p.get("milestone_id"),
        kind=p["kind"],
        amount=float(p["amount"]),
        description=p.get("description", ""),
        entry_date=date.fromisoformat(p["entry_date"]) if p.get("entry_date") else today(),
    )
    s.add(c)
    s.flush()
    return f"cost:{c.id} recorded"


def _queue_contractor_message(s: Session, pid: int, contractor_id: int, title: str, body: str) -> str:
    """Queue a WhatsApp (preferred) or email message to the contractor's people.
    Delivery happens after the surrounding transaction commits (see perform)."""
    from ..models import Membership, Notification, User

    c = _get(s, Contractor, contractor_id, pid)
    project = s.get(Project, pid)
    rows: list[Notification] = []
    for m in s.exec(select(Membership).where(Membership.project_id == pid, Membership.contractor_id == c.id)).all():
        u = s.get(User, m.user_id)
        if u is None or not u.is_active:
            continue
        ch = "whatsapp" if u.phone else "email"
        rows.append(Notification(user_id=u.id, project_id=pid, channel=ch, title=title, body=body))
    if not rows and c.phone:
        rows.append(Notification(address=c.phone, project_id=pid, channel="whatsapp", title=title, body=body))
    if not rows and "@" in (c.contact or ""):
        rows.append(Notification(address=c.contact, project_id=pid, channel="email", title=title, body=body))
    if not rows:
        return f"No phone/email on file for {c.name}; message logged only"
    for n in rows:
        n.body = f"{body}\n\n- Project manager, {project.name}"
        s.add(n)
    s.flush()
    _PENDING_DELIVERY.extend(n.id for n in rows)
    return f"Message to {c.name} queued on {', '.join(sorted({n.channel for n in rows}))} ({len(rows)} recipient(s))"


@action("contractor.remind", "Send a contractor a reminder (e.g. missing daily update)",
        lambda p: f"Remind contractor:{p['contractor_id']}: {p['message'][:120]}")
def _remind(s: Session, pid: int, p: dict[str, Any]) -> str:
    return _queue_contractor_message(s, pid, p["contractor_id"], "Reminder", p["message"])


@action("contractor.instruct", "Instruct a contractor to change working arrangement (extra shift, resequence)",
        lambda p: f"Instruct contractor:{p['contractor_id']}: {p['instruction'][:160]}")
def _instruct(s: Session, pid: int, p: dict[str, Any]) -> str:
    return _queue_contractor_message(s, pid, p["contractor_id"], "Instruction", p["instruction"])


@action("schedule.change", "Change a milestone's baseline dates (owner approval required)",
        lambda p: f"Re-baseline milestone:{p['milestone_id']} to {p.get('planned_start') or 'same start'} -> {p.get('planned_end') or 'same end'}")
def _schedule(s: Session, pid: int, p: dict[str, Any]) -> str:
    m = _get(s, Milestone, p["milestone_id"], pid)
    if p.get("planned_start"):
        m.planned_start = date.fromisoformat(p["planned_start"])
    if p.get("planned_end"):
        m.planned_end = date.fromisoformat(p["planned_end"])
    if m.planned_end < m.planned_start:
        raise ValueError("planned_end before planned_start")
    s.add(m)
    if p.get("project_end"):
        proj = s.get(Project, pid)
        proj.target_end_date = date.fromisoformat(p["project_end"])
        s.add(proj)
    return f"{m.name} re-baselined {m.planned_start} -> {m.planned_end}"


@action("budget.change", "Change the project or a milestone budget (owner approval required)",
        lambda p: f"Change budget of {'milestone:' + str(p['milestone_id']) if p.get('milestone_id') else 'project'} to {p['new_budget']}")
def _budget(s: Session, pid: int, p: dict[str, Any]) -> str:
    if p.get("milestone_id"):
        m = _get(s, Milestone, p["milestone_id"], pid)
        m.budget = float(p["new_budget"])
        s.add(m)
        return f"{m.name} budget -> {m.budget}"
    proj = s.get(Project, pid)
    proj.budget = float(p["new_budget"])
    s.add(proj)
    return f"Project budget -> {proj.budget}"
