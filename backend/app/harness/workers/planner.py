from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..authz import perform
from ..common_tools import LOOKAHEAD, READ_TOOLS, SIMULATE
from ..tools import Args, Tool, ToolContext
from . import WorkerSpec


class RebaselineArgs(Args):
    milestone_id: int
    planned_start: Optional[str] = Field(None, description="YYYY-MM-DD")
    planned_end: Optional[str] = Field(None, description="YYYY-MM-DD")
    project_end: Optional[str] = Field(None, description="YYYY-MM-DD new project completion, if it changes")
    reason: str


async def _rebaseline(ctx: ToolContext, a: RebaselineArgs) -> str:
    params = a.model_dump(exclude={"reason"}, exclude_none=True)
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "schedule.change", params, a.reason, ctx.work_item_id)


class FullRebaselineArgs(Args):
    name: str = Field(description="Baseline name, e.g. 'Rev 2 - after glazing re-award'")
    move_target: bool = Field(False, description="Also move the contractual target finish to the new forecast")
    reason: str


async def _full_rebaseline(ctx: ToolContext, a: FullRebaselineArgs) -> str:
    return await perform(ctx.runtime, ctx.project_id, ctx.actor, "schedule.rebaseline",
                         {"name": a.name, "move_target": a.move_target}, a.reason, ctx.work_item_id)


SPEC = WorkerSpec(
    name="planner",
    title="Planner / recovery scheduler",
    purpose="Root-cause schedule slippage and produce 2-3 costed recovery options using what-if simulation; prepare re-baseline proposals.",
    system=(
        "You are the PLANNER. When the project slips, find the driving cause on the critical "
        "path: call get_schedule(critical_only=true) and follow each critical activity's driver "
        "(predecessor link, procurement:/design: constraint) with lookup. Only work on the critical path "
        "(minimum float) moves the finish date - accelerating anything else just uses up float, so say so. "
        "Watch near-critical paths (float within ~10 working days of the minimum): fixing one path may "
        "expose the next. For projects without activities use get_health(detail=true). Then design 2-3 distinct recovery "
        "options, e.g. crashing (extra shift / more crews -> duration_factor), fast-tracking "
        "(overlap successors -> start_shift_days), fixing the constraint (earlier material or "
        "design date), re-sequencing, or accepting the delay. Evaluate EVERY option with "
        "simulate_schedule and report the engine's days_recovered. Estimate each option's cost "
        "and label it as an estimate. Do not re-baseline unless the work item explicitly says "
        "the owner chose that; re-baselining always goes to the owner for approval.\n"
        "End your report with a JSON block:\n"
        '```json\n{"options": [{"label": "...", "description": "...", "impact_days": <days recovered>, '
        '"impact_cost": <estimated extra cost>, "risks": "..."}]}\n```'
    ),
    tools=[
        *READ_TOOLS, LOOKAHEAD, SIMULATE,
        Tool("propose_rebaseline", "Propose new baseline dates for one milestone (always needs owner approval).", RebaselineArgs, _rebaseline),
        Tool("propose_full_rebaseline", "Propose making the current CPM forecast the new baseline (owner approval).", FullRebaselineArgs, _full_rebaseline),
    ],
)
