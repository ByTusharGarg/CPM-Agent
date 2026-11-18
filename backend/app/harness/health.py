"""Health check: run the tracking engine and emit new rule hits as events."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from ..db import session_scope, utcnow
from ..models import Alert, Event
from ..tracking.service import project_health

if TYPE_CHECKING:
    from .runtime import Runtime

REALERT_AFTER = timedelta(hours=24)


async def run_health_check(rt: "Runtime", project_id: int, reason: str = "scheduled") -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        h = project_health(s, project_id)
        open_keys = {
            a.rule_key
            for a in s.exec(
                select(Alert).where(Alert.project_id == project_id, col(Alert.status).in_(["open", "awaiting_owner"]))
            ).all()
        }
        recent_keys = {
            (e.payload or {}).get("rule_key")
            for e in s.exec(
                select(Event).where(
                    Event.project_id == project_id,
                    Event.kind == "rule_hit",
                    Event.created_at >= utcnow() - REALERT_AFTER,
                    Event.status != "dropped",
                )
            ).all()
        }
    new_hits = [hit for hit in h.rule_hits if hit.rule_key not in open_keys and hit.rule_key not in recent_keys]
    for hit in new_hits:
        await rt.emit(
            project_id,
            source="engine",
            kind="rule_hit",
            payload={**asdict(hit), "check_reason": reason},
            thread_key=f"rule:{hit.rule_key}",
            thread_title=hit.title,
        )
    rt.publish(project_id, "kpi", {})
    return {"status": h.status, "rule_hits": len(h.rule_hits), "new_hits": len(new_hits), "keys": [x.rule_key for x in new_hits]}
