"""Timers: daily health check, weekly owner report, watchdog."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .db import today
from .harness.health import run_health_check
from .harness.runtime import Runtime
from .harness.watchdog import watchdog_tick

log = logging.getLogger(__name__)


async def daily_checks(rt: Runtime) -> None:
    for pid in rt.active_projects():
        try:
            await run_health_check(rt, pid, reason="daily")
        except Exception:
            log.exception("daily health check failed for project %s", pid)


async def weekly_reports(rt: Runtime) -> None:
    for pid in rt.active_projects():
        await request_report(rt, pid, "weekly")


async def request_report(rt: Runtime, project_id: int, period: str = "weekly") -> None:
    iso = today().isocalendar()
    await rt.emit(
        project_id,
        source="scheduler",
        kind="report_request",
        payload={"period": f"Week {iso.week} {iso.year}" if period == "weekly" else period, "type": period},
    )


async def _digests() -> None:
    import asyncio

    from .notify import send_digests

    n = await asyncio.to_thread(send_digests)
    log.info("sent %s digest message(s)", n)


def start_scheduler(rt: Runtime) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(daily_checks, "cron", hour=settings.daily_check_hour, minute=0, args=[rt], id="daily_checks")
    sched.add_job(weekly_reports, "cron", day_of_week=settings.weekly_report_day, hour=settings.daily_check_hour, minute=30, args=[rt], id="weekly_reports")
    sched.add_job(watchdog_tick, "interval", minutes=settings.watchdog_interval_minutes, args=[rt], id="watchdog")
    sched.add_job(_digests, "cron", hour=settings.daily_check_hour, minute=15, id="digests")
    sched.start()
    return sched
