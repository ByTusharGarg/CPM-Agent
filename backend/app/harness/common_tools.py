"""Read-only tools shared by the Router and Workers."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from ..db import session_scope
from ..tracking.service import project_health
from . import context
from .tools import Args, Tool, ToolContext, ToolError

Kind = Literal[
    "milestone", "procurement", "design", "contractor", "update", "cost", "alert",
    "decision", "work_item", "thread", "event", "action", "report",
]


class HealthArgs(Args):
    detail: bool = Field(False, description="Include every milestone forecast (otherwise a compact summary).")


def _get_health(ctx: ToolContext, a: HealthArgs) -> dict[str, Any]:
    with session_scope(ctx.runtime.engine) as s:
        h = project_health(s, ctx.project_id)
    return h.to_dict() if a.detail else h.summary()


class LookupArgs(Args):
    ref: str = Field(description='Record reference "<kind>:<id>", e.g. "milestone:3", "alert:2", "work_item:5".')
    full: bool = Field(False, description="Return long text fields untruncated.")


def _lookup(ctx: ToolContext, a: LookupArgs) -> dict[str, Any]:
    with session_scope(ctx.runtime.engine) as s:
        try:
            return context.lookup(s, ctx.project_id, a.ref, a.full)
        except ValueError as e:
            raise ToolError(str(e)) from e


class ListArgs(Args):
    kind: Kind
    status: Optional[str] = Field(None, description="Optional status filter (e.g. open, running, delayed).")
    limit: int = Field(30, ge=1, le=100)


def _list(ctx: ToolContext, a: ListArgs) -> list[dict[str, Any]]:
    with session_scope(ctx.runtime.engine) as s:
        return context.list_records(s, ctx.project_id, a.kind, a.status, a.limit)


class Override(Args):
    milestone_id: Optional[int] = Field(None, description="Apply to every activity of this milestone")
    activity_id: Optional[int] = Field(None, description="Apply to one activity (projects with a schedule network)")
    duration_factor: Optional[float] = Field(None, description="e.g. 0.8 = remaining work 20% faster (crashing / extra shift).")
    productivity_factor: Optional[float] = Field(None, description="Multiplier on observed production rate for in-progress work.")
    start_shift_days: Optional[int] = Field(None, description="Shift planned start (negative = earlier, e.g. fast-tracking).")
    material_date: Optional[str] = Field(None, description="YYYY-MM-DD: assume materials for this milestone arrive by this date.")
    design_date: Optional[str] = Field(None, description="YYYY-MM-DD: assume design approval by this date.")


class SimulateArgs(Args):
    overrides: list[Override] = Field(description="What-if changes to evaluate together as one scenario.")


def _simulate(ctx: ToolContext, a: SimulateArgs) -> dict[str, Any]:
    ov: dict[int, dict[str, Any]] = {}
    aov: dict[int, dict[str, Any]] = {}
    for o in a.overrides:
        vals = {k: v for k, v in o.model_dump().items() if v is not None and k not in ("milestone_id", "activity_id")}
        if o.activity_id:
            aov[o.activity_id] = vals
        elif o.milestone_id:
            ov[o.milestone_id] = vals
        else:
            raise ToolError("each override needs milestone_id or activity_id")
    with session_scope(ctx.runtime.engine) as s:
        base = project_health(s, ctx.project_id)
        what_if = project_health(s, ctx.project_id, overrides=ov, activity_overrides=aov)
    changed = []
    base_by = {m.id: m for m in base.milestones}
    for m in what_if.milestones:
        b = base_by[m.id]
        if m.forecast_end != b.forecast_end:
            changed.append({"ref": f"milestone:{m.id}", "name": m.name, "forecast_end_before": b.forecast_end.isoformat(), "forecast_end_after": m.forecast_end.isoformat()})
    return {
        "baseline_forecast_end": base.forecast_end.isoformat(),
        "scenario_forecast_end": what_if.forecast_end.isoformat(),
        "days_recovered": (base.forecast_end - what_if.forecast_end).days,
        "scenario_project_slip_days": what_if.project_slip_days,
        "changed_milestones": changed,
        "note": "Computed by the deterministic tracking engine. Cost of the measures is NOT included - estimate it separately and label it as an estimate.",
    }


GET_HEALTH = Tool(
    "get_health",
    "Current project health from the deterministic tracking engine: progress, SPI/CPI, EAC, forecast finish, late milestones, rule hits. Always cite these numbers rather than computing your own.",
    HealthArgs,
    _get_health,
)
LOOKUP = Tool(
    "lookup",
    "Fetch one record by ref and the refs of related records (follow them hop-by-hop to recall history).",
    LookupArgs,
    _lookup,
)
LIST_RECORDS = Tool("list_records", "List recent records of a kind (brief rows; use lookup for detail).", ListArgs, _list)
SIMULATE = Tool(
    "simulate_schedule",
    "What-if: re-run the schedule forecast with changes (faster work, earlier material/design dates, start shifts) and report the new project finish date.",
    SimulateArgs,
    _simulate,
)

class ScheduleArgs(Args):
    critical_only: bool = Field(False, description="Only the critical (longest) path")
    milestone_id: Optional[int] = None
    limit: int = Field(60, ge=1, le=300)


def _schedule_rows(sched, ids=None) -> list[dict[str, Any]]:
    rows = []
    for r in sched.activities:
        if ids is not None and r.id not in ids:
            continue
        rows.append({
            "ref": f"activity:{r.id}", "name": r.name, "milestone": f"milestone:{r.milestone_id}" if r.milestone_id else None,
            "status": r.status, "pct": r.pct, "early_start": r.early_start.isoformat(), "early_finish": r.early_finish.isoformat(),
            "total_float": r.total_float, "free_float": r.free_float, "critical": r.critical, "driver": r.driver,
            "baseline_finish": r.baseline_finish.isoformat() if r.baseline_finish else None, "finish_variance_days": r.finish_variance_days,
        })
    return rows


def _get_schedule(ctx: ToolContext, a: ScheduleArgs) -> dict[str, Any]:
    with session_scope(ctx.runtime.engine) as s:
        h = project_health(s, ctx.project_id)
    sched = h.schedule
    if sched is None:
        return {"has_network": False, "note": "This project has milestones only (no activity network). Use get_health(detail=true)."}
    rows = _schedule_rows(sched)
    if a.critical_only:
        rows = [r for r in rows if r["critical"]]
    if a.milestone_id:
        rows = [r for r in rows if r["milestone"] == f"milestone:{a.milestone_id}"]
    return {
        "has_network": True, "data_date": h.as_of.isoformat(), "target_finish": h.target_end.isoformat(),
        "forecast_finish": sched.project_finish.isoformat(),
        "critical_path": [f"activity:{i}" for i in sched.critical_path],
        "activities": rows[: a.limit],
        "how_to_read": "Float is in working days against the target finish; negative = late. 'driver' is what sets the early start "
                       "(a predecessor link, a procurement/design constraint, the data date or actuals).",
    }


class LookaheadArgs(Args):
    weeks: int = Field(3, ge=1, le=12)


def _lookahead(ctx: ToolContext, a: LookaheadArgs) -> dict[str, Any]:
    from ..tracking.cpm import lookahead

    with session_scope(ctx.runtime.engine) as s:
        h = project_health(s, ctx.project_id)
    if h.schedule is None:
        return {"has_network": False}
    items = lookahead(h.schedule, h.as_of, a.weeks)
    return {"weeks": a.weeks, "from": h.as_of.isoformat(), "activities": _schedule_rows(h.schedule, {i.id for i in items})}


GET_SCHEDULE = Tool(
    "get_schedule",
    "Activity-level CPM schedule: early dates, total/free float, critical path, what drives each activity, variance to baseline.",
    ScheduleArgs,
    _get_schedule,
)
LOOKAHEAD = Tool("lookahead", "Short-term (N-week) lookahead: work in progress or due to start soon, with float and drivers.", LookaheadArgs, _lookahead)

READ_TOOLS = [GET_HEALTH, LOOKUP, LIST_RECORDS, GET_SCHEDULE]
