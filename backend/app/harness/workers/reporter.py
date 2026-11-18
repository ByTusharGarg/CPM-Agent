from __future__ import annotations

from pydantic import Field

from ...db import session_scope
from ...models import Report
from ..common_tools import READ_TOOLS
from ..tools import Args, Tool, ToolContext
from . import WorkerSpec


class SaveReportArgs(Args):
    period: str = Field(description='e.g. "Week 47 2026" or "2026-11-18 daily"')
    markdown: str = Field(description="The full report in Markdown.")


def _save(ctx: ToolContext, a: SaveReportArgs) -> str:
    with session_scope(ctx.runtime.engine) as s:
        r = Report(project_id=ctx.project_id, period=a.period, content=a.markdown)
        s.add(r)
        s.flush()
        rid = r.id
    ctx.runtime.publish(ctx.project_id, "report", {"report_id": rid})
    return f"Saved as report:{rid}"


SPEC = WorkerSpec(
    name="reporter",
    title="Report writer",
    purpose="Write the periodic owner status report (weekly by default).",
    system=(
        "You write the owner's periodic status report. Gather facts with get_health (detail=true), "
        "list_records for alerts, decisions, work_items and updates in the period. Structure: "
        "1) Headline (status colour + one sentence) 2) Progress vs plan 3) Schedule (forecast "
        "finish, late milestones and why) 4) Cost (budget, actual, EAC, CPI) 5) Procurement & "
        "design risks 6) Decisions taken and their effect 7) Decisions needed from the owner "
        "8) Next period lookahead. Use tables where useful. Every number must come from the tools. "
        "Save it with save_report, then reply with a 3-line summary and the report ref."
    ),
    tools=[*READ_TOOLS, Tool("save_report", "Save the finished report.", SaveReportArgs, _save)],
)
