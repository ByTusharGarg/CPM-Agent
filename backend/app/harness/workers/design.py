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


class RfiImpactArgs(Args):
    rfi_id: int
    time_impact: Optional[bool] = None
    cost_impact: Optional[bool] = None
    activity_id: Optional[int] = Field(None, description="The activity waiting on this answer")
    ball_in_court: Optional[str] = Field(None, description="Who must answer: consultant | owner_rep | owner | user:<id>")
    reason: str


async def _rfi_impact(ctx: ToolContext, a: RfiImpactArgs) -> str:
    params = a.model_dump(exclude={"reason"}, exclude_none=True)
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "rfi.update", params, a.reason, ctx.work_item_id)


SPEC = WorkerSpec(
    name="design",
    title="Design coordinator",
    purpose="Design deliverables, drawing approvals, RFI impact assessment, submittal pre-checks, design-driven schedule risk.",
    system=(
        "You are the DESIGN COORDINATOR. You track design deliverables (drawings, shop drawings, "
        "specifications), their approval status and due dates, and which construction milestones "
        "they unlock. Identify overdue or at-risk deliverables, their knock-on effect on the "
        "schedule (use get_health / lookup on the linked milestone), and practical ways to "
        "accelerate approvals (priority review, partial/conditional approval, design freeze).\n"
        "RFIs: when an RFI is raised, find the activity it affects (get_schedule / lookahead) and whether it "
        "has time or cost impact, record that with set_rfi_impact, and search_documents for the drawings and "
        "specification clauses that bear on the question. You do NOT answer RFIs - the consultant does - but "
        "include a suggested answer with citations (document:<id>#p<page>) in your report for the Router.\n"
        "Submittals: pre-check a submitted item against the specification (search_documents) and list "
        "compliance points and gaps with citations, plus the date the linked activity needs approval by. "
        "Reviewers make the decision."
    ),
    tools=[
        *READ_TOOLS,
        Tool("update_design", "Update a design deliverable's status, due date or notes.", UpdateDesignArgs, _update),
        Tool("set_rfi_impact", "Record an RFI's schedule/cost impact and the activity it holds up.", RfiImpactArgs, _rfi_impact),
    ],
)
