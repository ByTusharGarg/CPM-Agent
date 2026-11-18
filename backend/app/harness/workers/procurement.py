from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ..authz import perform
from ..common_tools import READ_TOOLS, SIMULATE
from ..tools import Args, Tool, ToolContext
from . import WorkerSpec


class UpdateItemArgs(Args):
    item_id: int
    status: Optional[Literal["not_ordered", "ordered", "shipped", "delivered", "delayed"]] = None
    expected_delivery: Optional[str] = Field(None, description="YYYY-MM-DD")
    po_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    note: Optional[str] = None
    reason: str = Field(description="Source of the information (event/update ref).")


class ExpediteArgs(Args):
    item_id: int
    target_date: str = Field(description="YYYY-MM-DD delivery date to request")
    est_cost: float = Field(0, description="Estimated extra cost of expediting (an estimate - say so).")
    reason: str


async def _update(ctx: ToolContext, a: UpdateItemArgs) -> str:
    params = a.model_dump(exclude={"reason"}, exclude_none=True)
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "procurement.update", params, a.reason, ctx.work_item_id)


async def _expedite(ctx: ToolContext, a: ExpediteArgs) -> str:
    params = a.model_dump(exclude={"reason"})
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "procurement.expedite", params, a.reason, ctx.work_item_id)


SPEC = WorkerSpec(
    name="procurement",
    title="Procurement manager",
    purpose="Materials & long-lead equipment: POs, vendor lead times, delivery vs required-on-site dates, expediting, alternates.",
    system=(
        "You are the PROCUREMENT MANAGER. You track materials and long-lead equipment against the "
        "dates they are required on site. For each at-risk item: quantify the gap (required vs "
        "expected), identify the milestone it blocks, and evaluate options - expedite with the "
        "current vendor, split deliveries, alternate supplier/approved equal, air freight, "
        "re-sequencing work so the late item is needed later. Use simulate_schedule with "
        "material_date overrides to show how many days each option recovers."
    ),
    tools=[
        *READ_TOOLS,
        Tool("update_procurement_item", "Update status / expected delivery / notes for a procurement item.", UpdateItemArgs, _update),
        Tool("request_expedite", "Ask the vendor to expedite an item (authorization-controlled).", ExpediteArgs, _expedite),
        SIMULATE,
    ],
)
