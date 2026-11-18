"""Watchdog: a timer-driven check that workers are making real progress.

Liveness alone cannot catch a worker that loops or drifts, so in addition to
the "no progress for N minutes" and "over turn budget" rules, long-running
workers get a short LLM judgement (progressing vs drifting) on their recent
transcript. Findings are reported to the Router as `watchdog` events.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from ..config import settings
from ..db import session_scope, utcnow
from ..models import WorkItem

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger(__name__)

DRIFT_CHECK_AFTER_TURNS = 6
TOTAL_TURN_CAP_FACTOR = 3  # across re-activations

JUDGE_SYSTEM = (
    "You supervise background AI workers in a construction project-management system. "
    "Given a worker's task and its recent steps, answer with exactly one line: "
    "'PROGRESSING: <reason>' if it is moving toward completing its task, or "
    "'DRIFTING: <reason>' if it is looping, repeating the same calls, or working off-task."
)


def _recent_steps(transcript: list[dict[str, Any]], n: int = 8) -> str:
    out = []
    for m in transcript[-n:]:
        content = m.get("content")
        if isinstance(content, str):
            out.append(f"{m['role']}: {content[:400]}")
            continue
        for b in content or []:
            t = b.get("type")
            if t == "text":
                out.append(f"{m['role']} text: {b['text'][:300]}")
            elif t == "tool_use":
                out.append(f"tool_use {b['name']} {json.dumps(b.get('input'))[:200]}")
            elif t == "tool_result":
                c = b.get("content")
                out.append(f"tool_result: {str(c)[:200]}")
    return "\n".join(out)


async def watchdog_tick(rt: "Runtime", use_llm: bool = True) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stuck_before = utcnow() - timedelta(minutes=settings.watchdog_stuck_minutes)
    with session_scope(rt.engine) as s:
        running = list(s.exec(select(WorkItem).where(col(WorkItem.status).in_(["running", "queued"]))).all())

    for wi in running:
        verdict = None
        if wi.turns_used >= wi.max_turns * TOTAL_TURN_CAP_FACTOR:
            verdict = f"over total turn budget ({wi.turns_used} turns)"
        elif wi.last_progress_at < stuck_before:
            verdict = f"no progress for more than {settings.watchdog_stuck_minutes} minutes"
        elif use_llm and wi.turns_used >= DRIFT_CHECK_AFTER_TURNS:
            try:
                resp = await rt.llm.complete(
                    model=settings.watchdog_model,
                    system=JUDGE_SYSTEM,
                    messages=[{
                        "role": "user",
                        "content": f"Task: {wi.title}\nInstructions: {wi.instructions[:800]}\n\nRecent steps:\n{_recent_steps(wi.transcript or [])}",
                    }],
                    tools=[],
                    effort="low",
                )
                line = resp.text.strip()
                if line.upper().startswith("DRIFTING"):
                    verdict = line
            except Exception:  # judging is best-effort
                log.exception("watchdog judge failed for work_item:%s", wi.id)
        if verdict:
            await rt.cancel_worker(wi.id, f"watchdog: {verdict}")
            finding = {"work_item": f"work_item:{wi.id}", "worker": wi.worker, "title": wi.title, "verdict": verdict}
            findings.append(finding)
            await rt.emit(wi.project_id, source="watchdog", kind="watchdog", payload=finding)
    return findings
