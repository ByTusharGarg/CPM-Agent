from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ...db import session_scope
from ...models import CostEntry
from ..authz import perform
from ..common_tools import READ_TOOLS
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


SPEC = WorkerSpec(
    name="cost",
    title="Cost controller",
    purpose="Budget burn, committed vs actual cost, change orders, cost forecast (EAC) and cost impact of recovery options.",
    system=(
        "You are the COST CONTROLLER. Explain cost performance using the engine's CPI/EAC "
        "(get_health), break costs down by milestone (cost_breakdown), identify drivers of "
        "overrun, and estimate the cost of proposed recovery measures (extra shifts, expediting, "
        "alternates). Clearly label any figure that is your estimate rather than engine output."
    ),
    tools=[
        *READ_TOOLS,
        Tool("cost_breakdown", "Committed / actual / change-order totals per milestone.", NoArgs, _breakdown),
        Tool("record_cost", "Record a cost entry (authorization-controlled).", CostArgs, _record),
    ],
)
