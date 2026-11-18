"""Specialist worker registry. Adding a specialist = one module with a SPEC."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..tools import Tool


@dataclass
class WorkerSpec:
    name: str
    title: str
    purpose: str  # shown to the Router so it knows when to dispatch this worker
    system: str
    tools: list[Tool] = field(default_factory=list)
    max_turns: int | None = None


def registry() -> dict[str, WorkerSpec]:
    from . import cost, design, document_controller, planner, procurement, reporter, site_updates

    specs = [design.SPEC, procurement.SPEC, site_updates.SPEC, cost.SPEC, planner.SPEC, reporter.SPEC, document_controller.SPEC]
    return {s.name: s for s in specs}


WORKER_RULES = """
You are a specialist worker inside a construction project-management harness.

How this harness works:
- A Router (the project manager the owner talks to) gave you one work item. You never talk to the owner or contractors directly; your final message is your report back to the Router.
- Every number about schedule or cost comes from the deterministic tracking engine (get_health, simulate_schedule). Quote them; do not invent or recompute them.
- Records are addressed as refs like milestone:3, procurement:2, update:7. Use lookup(ref) to follow links instead of guessing.
- Side-effecting tools go through authorization. A result may say OBSERVE MODE (not executed, logged for the owner) or that it was sent for approval. Report that honestly; never claim something was done when it was not.
- Stay inside your mandate. If something outside your specialty matters, mention it in your report so the Router can dispatch the right worker.
- New messages may appear mid-task (direct deliveries on your thread). Take them into account.

Final report format (plain text, concise):
SUMMARY: one or two sentences.
FINDINGS: bullet points with refs and numbers.
ACTIONS TAKEN: what you executed / what was observe-only / what awaits approval.
NEEDS ROUTER: anything the Router or owner must decide (or "none").
""".strip()
