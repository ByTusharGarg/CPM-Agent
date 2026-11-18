from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from ..authz import perform
from ..common_tools import READ_TOOLS
from ..tools import Args, Tool, ToolContext
from . import WorkerSpec


class UpdateDesignArgs(Args):
    design_id: int
    status: Optional[Literal["not_started", "in_progress", "submitted", "approved", "rejected"]] = None
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    revision: Optional[str] = None
    note: Optional[str] = None
    reason: str = Field(description="Why (cite the source, e.g. update:12 or event:40).")


async def _update(ctx: ToolContext, a: UpdateDesignArgs) -> str:
    params = a.model_dump(exclude={"reason"}, exclude_none=True)
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "design.update", params, a.reason, ctx.work_item_id)


SPEC = WorkerSpec(
    name="design",
    title="Design coordinator",
    purpose="Design deliverables, drawing approvals, RFIs, design-driven schedule risk.",
    system=(
        "You are the DESIGN COORDINATOR. You track design deliverables (drawings, shop drawings, "
        "specifications), their approval status and due dates, and which construction milestones "
        "they unlock. Identify overdue or at-risk deliverables, their knock-on effect on the "
        "schedule (use get_health / lookup on the linked milestone), and practical ways to "
        "accelerate approvals (priority review, partial/conditional approval, design freeze)."
    ),
    tools=[*READ_TOOLS, Tool("update_design", "Update a design deliverable's status, due date or notes.", UpdateDesignArgs, _update)],
)
