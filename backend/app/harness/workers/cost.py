from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ...db import session_scope
from ...models import CostEntry
from ..authz import perform
from ..common_tools import READ_TOOLS, SIMULATE
from ..tools import Args, NoArgs, Tool, ToolContext
from . import WorkerSpec


class CostArgs(Args):
    kind: Literal["committed", "actual", "change_order"]
    amount: float
    description: str
    milestone_id: Optional[int] = None
    entry_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    reason: str = Field(description="Source document / event ref.")


async def _record(ctx: ToolContext, a: CostArgs) -> str:
    params = a.model_dump(exclude={"reason"}, exclude_none=True)
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "cost.record", params, a.reason, ctx.work_item_id)


def _breakdown(ctx: ToolContext, _: NoArgs) -> dict:
    from sqlmodel import select

    with session_scope(ctx.runtime.engine) as s:
        rows = s.exec(select(CostEntry).where(CostEntry.project_id == ctx.project_id)).all()
    by: dict[str, dict[str, float]] = {}
    for r in rows:
        key = f"milestone:{r.milestone_id}" if r.milestone_id else "unallocated"
        by.setdefault(key, {"committed": 0.0, "actual": 0.0, "change_order": 0.0})
        by[key][r.kind] += r.amount
    return by


def _commercial(ctx: ToolContext, _: NoArgs) -> dict:
    from ...tracking.service import project_health

    with session_scope(ctx.runtime.engine) as s:
        h = project_health(s, ctx.project_id)
    c = h.commercial
    if c is None:
        return {"has_contracts": False}
    from dataclasses import asdict

    out = asdict(c)
    out["cashflow"] = out["cashflow"][-6:] if len(out["cashflow"]) > 6 else out["cashflow"]
    out.pop("rule_hits", None)
    out["note"] = "All figures from the commercial engine. Quote them; label any of your own numbers as estimates."
    return out


class AssessArgs(Args):
    change_order_id: int
    assessment: str = Field(description="Entitlement, scope check, basis of valuation, risks - cite refs")
    recommended_amount: Optional[float] = Field(None, description="Your fair-value estimate (label it an estimate in the text)")
    time_impact_days: Optional[int] = Field(None, description="From simulate_schedule, not a guess")


async def _assess(ctx: ToolContext, a: AssessArgs) -> str:
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "change_order.assess", a.model_dump(exclude_none=True),
                         "commercial assessment", ctx.work_item_id)


class CertifyArgs(Args):
    pay_app_id: int
    line_pcts: dict[str, float] = Field(default_factory=dict, description="contract_line_id -> % to certify (<= claimed); omit lines to certify as claimed")
    note: str = Field(description="Why each reduction - cite recorded progress / photos")


async def _propose_certify(ctx: ToolContext, a: CertifyArgs) -> str:
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "payapp.certify",
                         {"pay_app_id": a.pay_app_id, "line_pcts": a.line_pcts, "note": a.note}, a.note, ctx.work_item_id)


class ApproveCoArgs(Args):
    change_order_id: int
    approve: bool
    amount: Optional[float] = None
    note: str


async def _propose_co(ctx: ToolContext, a: ApproveCoArgs) -> str:
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "change_order.approve", a.model_dump(exclude_none=True), a.note, ctx.work_item_id)


SPEC = WorkerSpec(
    name="cost",
    title="Commercial manager",
    purpose="Budget, commitments, change orders (entitlement and valuation), pay applications (overbilling checks), cash flow, cost impact of recovery options.",
    system=(
        "You are the COMMERCIAL MANAGER (COST CONTROLLER). Explain cost performance using the engine's CPI/EAC "
        "(get_health), break costs down by milestone (cost_breakdown), identify drivers of "
        "overrun, and estimate the cost of proposed recovery measures (extra shifts, expediting, "
        "alternates). Clearly label any figure that is your estimate rather than engine output.\n"
        "Change orders: check entitlement against the contract scope and its source (RFI, design change, site "
        "condition), value it (quote rates you used), get any time impact from simulate_schedule, and record it with "
        "record_co_assessment. Pay applications: compare each claimed % with recorded progress (commercial_summary "
        "overbilling list, get_schedule, daily updates); propose certification at supportable %s with "
        "propose_certification. You never approve money - approvals always go to the owner."
    ),
    tools=[
        *READ_TOOLS,
        SIMULATE,
        Tool("cost_breakdown", "Committed / actual / change-order totals per milestone.", NoArgs, _breakdown),
        Tool("record_cost", "Record a cost entry (authorization-controlled).", CostArgs, _record),
        Tool("commercial_summary", "Commitments, change-order exposure, forecast final cost, cash flow, contract positions and overbilling.", NoArgs, _commercial),
        Tool("record_co_assessment", "Record your assessment and recommended value of a change order.", AssessArgs, _assess),
        Tool("propose_certification", "Propose certifying a pay application (owner approval).", CertifyArgs, _propose_certify),
        Tool("propose_change_order_decision", "Propose approving/rejecting a change order (owner approval).", ApproveCoArgs, _propose_co),
    ],
)
