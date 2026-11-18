"""Harness runtime: event bus, per-project Router loops (with batching),
worker task management and the SSE fan-out to the UI.

State lives in the DB; this object only holds asyncio plumbing, so the
process can be restarted and `resume()` picks up where it left off.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sqlmodel import col, select

from ..db import engine as default_engine
from ..db import session_scope, utcnow
from ..models import Event, Project, WorkItem
from .events import (
    ROUTER_ONLY_KINDS,
    describe_event,
    fast_path_drop_reason,
    get_or_create_thread,
    live_subscribers,
    subscribe,
)
from .llm import LLM

log = logging.getLogger(__name__)

# Deterministic routing (code rules, no model call): events of these kinds that
# no worker is subscribed to start a worker directly. The Router hears the
# worker's result, never the raw (untrusted) contractor text.
FAST_PATH_DISPATCH: dict[str, tuple[str, str, str]] = {
    "document_uploaded": (
        "document_controller",
        "Register {number} {title} ({revision})",
        "A document or new revision was uploaded (see <source_events>). Read its title block, classify it and link it "
        "to what it serves. If it supersedes a revision, check open RFIs and submittals that may use the old one.",
    ),
    "rfi_raised": (
        "design",
        "RFI #{number}: {subject}",
        "A new RFI was raised (see <source_events>). Find the affected activity and impact (set_rfi_impact), "
        "search the documents for relevant clauses, and report a suggested answer with citations.",
    ),
    "submittal_submitted": (
        "design",
        "Pre-check submittal #{number}: {title}",
        "A submittal was submitted for review (see <source_events>). Check it against the specification "
        "(search_documents) and report compliance points, gaps and the date the linked work needs approval.",
    ),
    "daily_update": (
        "site_updates",
        "Daily report: {contractor}",
        "Process the contractor daily report in <source_events>: look up the update and the contractor's "
        "milestones, record progress with record_progress, save_parsed_update, and report blockers.",
    ),
}


class DispatchRefused(Exception):
    """Raised when a dispatch would duplicate work or exceed the loop guards."""


# Loop guards: the harness, not the model, prevents runaway delegation.
MAX_RUNS_PER_WORKER_PER_ALERT = 2
MAX_DISPATCHES_PER_HOUR = 12


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "?"


class Runtime:
    def __init__(self, llm: LLM, engine=None):
        self.llm = llm
        self.engine = engine or default_engine
        self._queues: dict[int, asyncio.Queue[int]] = {}
        self._router_tasks: dict[int, asyncio.Task] = {}
        self._worker_tasks: dict[int, asyncio.Task] = {}
        self._worker_events: dict[int, list[int]] = {}
        self._restart: set[int] = set()
        self._router_busy: dict[int, bool] = {}
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self.router_batches: list[tuple[int, list[int]]] = []  # (project_id, event_ids) - observability/tests
        from .leases import Leases

        self.leases = Leases(self.engine)
        self._queued_ids: set[int] = set()
        self._poller: asyncio.Task | None = None

    # ------------------------------------------------------------------ UI fan-out

    def publish(self, project_id: int, type_: str, data: dict[str, Any]) -> None:
        for q in list(self._subscribers[project_id]):
            try:
                q.put_nowait({"type": type_, "data": data})
            except asyncio.QueueFull:  # slow client - drop
                pass

    def open_stream(self, project_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[project_id].add(q)
        return q

    def close_stream(self, project_id: int, q: asyncio.Queue) -> None:
        self._subscribers[project_id].discard(q)

    # ------------------------------------------------------------------ events

    async def emit(
        self,
        project_id: int,
        source: str,
        kind: str,
        payload: dict[str, Any],
        thread_key: str | None = None,
        thread_title: str = "",
    ) -> int | None:
        """Ingest an event: fast-path filter -> thread -> direct delivery or Router."""
        to_start: list[int] = []
        to_router = False
        with session_scope(self.engine) as s:
            drop = fast_path_drop_reason(s, project_id, source, kind, payload)
            thread = get_or_create_thread(s, project_id, thread_key, thread_title) if thread_key else None
            ev = Event(project_id=project_id, source=source, kind=kind, payload=payload, thread_id=thread.id if thread else None)
            if drop:
                ev.status, ev.route_note = "dropped", drop
                s.add(ev)
                s.flush()
                self.publish(project_id, "event", {"id": ev.id, "kind": kind, "status": "dropped", "note": drop})
                return None
            s.add(ev)
            s.flush()
            targets = [] if (thread is None or kind in ROUTER_ONLY_KINDS) else live_subscribers(s, thread.id)
            auto_dispatch = None
            if not targets and kind in FAST_PATH_DISPATCH and thread is not None:
                auto_dispatch = FAST_PATH_DISPATCH[kind]
            if auto_dispatch:
                ev.status = "to_worker"
                ev.route_note = f"fast path: auto-dispatch {auto_dispatch[0]}"
            elif targets:
                ev.status = "to_worker"
                ev.route_note = "direct delivery to " + ", ".join(f"work_item:{w.id}" for w in targets)
                text = describe_event(ev)
                for w in targets:
                    w.inbox = [*(w.inbox or []), text]
                    s.add(w)
                    to_start.append(w.id)
            else:
                ev.status = "to_router"
                to_router = True
            s.add(ev)
            eid = ev.id
        self.publish(project_id, "event", {"id": eid, "kind": kind, "status": "to_worker" if (to_start or auto_dispatch) else "to_router"})
        if auto_dispatch:
            worker, title_tpl, instr = auto_dispatch
            await self.dispatch(
                project_id, worker, title_tpl.format_map(_Missing(payload)), instr,
                thread_key=thread_key, event_ids=[eid],
            )
        for wid in to_start:
            self.start_worker(wid)
        if to_router:
            self._enqueue_router(project_id, eid)
        return eid

    # ------------------------------------------------------------------ router

    def _enqueue_router(self, project_id: int, event_id: int) -> bool:
        """Queue for this instance's Router loop - only if we hold the project's
        router lease. Otherwise the event stays `to_router` in the DB and the
        instance holding the lease picks it up in `_poll`."""
        if event_id in self._queued_ids:
            return True
        if not self.leases.acquire("router", project_id):
            return False
        self._queued_ids.add(event_id)
        self._queue(project_id).put_nowait(event_id)
        return True

    async def _poll(self, interval: float = 1.0) -> None:
        """Multi-instance only: take over unowned projects/workers and pick up
        events or deliveries written by other instances."""
        while True:
            try:
                with session_scope(self.engine) as s:
                    evs = [(e.project_id, e.id) for e in s.exec(select(Event).where(Event.status == "to_router")).all()]
                    pending = [w.id for w in s.exec(select(WorkItem).where(col(WorkItem.status).in_(["queued", "running", "done"]))).all()
                               if w.inbox]
                    orphaned = [w.id for w in s.exec(select(WorkItem).where(col(WorkItem.status).in_(["queued", "running"]))).all()]
                for pid, eid in evs:
                    self._enqueue_router(pid, eid)
                for wid in {*pending, *orphaned}:
                    if not self.is_worker_running(wid):
                        self.start_worker(wid)  # lease-gated: no-op if another instance runs it
            except Exception:
                log.exception("lease poller error")
            await asyncio.sleep(interval)

    def _queue(self, project_id: int) -> asyncio.Queue[int]:
        if project_id not in self._queues:
            self._queues[project_id] = asyncio.Queue()
        task = self._router_tasks.get(project_id)
        if task is None or task.done():
            self._router_tasks[project_id] = asyncio.create_task(self._router_loop(project_id), name=f"router-{project_id}")
        return self._queues[project_id]

    async def _router_loop(self, project_id: int) -> None:
        from .router import run_router

        q = self._queues[project_id]
        while True:
            first = await q.get()
            self._router_busy[project_id] = True
            batch = [first]
            # Batching: everything that queued up while the previous call ran
            # (or arrives in the same tick) is handled in ONE router call.
            await asyncio.sleep(0)
            while not q.empty():
                batch.append(q.get_nowait())
            self.router_batches.append((project_id, batch))
            try:
                await run_router(self, project_id, batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("router loop error (project %s)", project_id)
            finally:
                self._router_busy[project_id] = False

    # ------------------------------------------------------------------ workers

    async def dispatch(
        self,
        project_id: int,
        worker: str,
        title: str,
        instructions: str,
        thread_key: str | None = None,
        alert_id: int | None = None,
        event_ids: list[int] | None = None,
        max_turns: int | None = None,
    ) -> WorkItem:
        from ..config import settings

        with session_scope(self.engine) as s:
            self._check_dispatch_guards(s, project_id, worker, alert_id, thread_key)
            thread_id = None
            if thread_key:
                thread_id = get_or_create_thread(s, project_id, thread_key).id
            elif alert_id:
                thread_id = get_or_create_thread(s, project_id, f"alert:{alert_id}").id
            wi = WorkItem(
                project_id=project_id,
                worker=worker,
                title=title,
                instructions=instructions,
                thread_id=thread_id,
                alert_id=alert_id,
                max_turns=max_turns or settings.worker_max_turns,
            )
            s.add(wi)
            s.flush()
            if thread_id:
                subscribe(s, thread_id, wi.id)
        self._worker_events[wi.id] = list(event_ids or [])
        self.start_worker(wi.id)
        return wi

    def _check_dispatch_guards(self, s, project_id: int, worker: str, alert_id: int | None, thread_key: str | None) -> None:
        from datetime import timedelta

        from .events import get_or_create_thread

        active = s.exec(
            select(WorkItem).where(
                WorkItem.project_id == project_id, WorkItem.worker == worker, col(WorkItem.status).in_(["running", "queued"])
            )
        ).all()
        for w in active:
            if (alert_id and w.alert_id == alert_id) or (thread_key and w.thread_id and w.thread_id == get_or_create_thread(s, project_id, thread_key).id):
                raise DispatchRefused(f"work_item:{w.id} ({worker}) is already working on this. Wait for its work_result.")
        if alert_id:
            runs = s.exec(select(WorkItem).where(WorkItem.alert_id == alert_id, WorkItem.worker == worker)).all()
            if len(runs) >= MAX_RUNS_PER_WORKER_PER_ALERT:
                raise DispatchRefused(
                    f"{worker} already ran {len(runs)} times for alert:{alert_id} "
                    f"({', '.join(f'work_item:{w.id}' for w in runs)}). Use those results or ask the owner."
                )
        recent = s.exec(
            select(WorkItem).where(WorkItem.project_id == project_id, WorkItem.created_at >= utcnow() - timedelta(hours=1))
        ).all()
        if len(recent) >= MAX_DISPATCHES_PER_HOUR:
            raise DispatchRefused(f"Dispatch limit reached ({MAX_DISPATCHES_PER_HOUR}/hour). Consolidate work or wait.")

    def start_worker(self, work_item_id: int) -> None:
        from .worker import run_worker

        t = self._worker_tasks.get(work_item_id)
        if t is not None and not t.done():
            if t is asyncio.current_task():
                self._restart.add(work_item_id)
            return  # running worker picks up its inbox on the next turn
        if not self.leases.acquire("worker", work_item_id):
            return  # another instance runs this worker; it reads the inbox from the DB
        events = self._worker_events.pop(work_item_id, [])
        task = asyncio.create_task(run_worker(self, work_item_id, events), name=f"worker-{work_item_id}")
        self._worker_tasks[work_item_id] = task
        task.add_done_callback(lambda _t, wid=work_item_id: self._on_worker_done(wid))

    def request_restart(self, work_item_id: int) -> None:
        self._restart.add(work_item_id)

    def _on_worker_done(self, work_item_id: int) -> None:
        self._worker_tasks.pop(work_item_id, None)
        if work_item_id not in self._restart:
            self.leases.release("worker", work_item_id)
        if work_item_id in self._restart:
            self._restart.discard(work_item_id)
            self.start_worker(work_item_id)

    def is_worker_running(self, work_item_id: int) -> bool:
        t = self._worker_tasks.get(work_item_id)
        return t is not None and not t.done()

    async def cancel_worker(self, work_item_id: int, reason: str = "") -> bool:
        with session_scope(self.engine) as s:
            wi = s.get(WorkItem, work_item_id)
            if wi is None or wi.status not in ("running", "queued"):
                return False
            wi.status = "cancelled"
            wi.result = f"Cancelled: {reason}"
            wi.updated_at = utcnow()
            s.add(wi)
        t = self._worker_tasks.get(work_item_id)
        if t and not t.done():
            t.cancel()
        return True

    # ------------------------------------------------------------------ lifecycle

    async def resume(self) -> None:
        """After a restart: restart interrupted workers, re-queue unprocessed router events."""
        with session_scope(self.engine) as s:
            wis = s.exec(select(WorkItem).where(col(WorkItem.status).in_(["running", "queued"]))).all()
            ids = [w.id for w in wis]
            evs = s.exec(select(Event).where(Event.status == "to_router").order_by(Event.id)).all()
            pending = [(e.project_id, e.id) for e in evs]
        for wid in ids:
            log.info("resuming work_item:%s", wid)
            self.start_worker(wid)
        for pid, eid in pending:
            self._enqueue_router(pid, eid)
        if self.leases.enabled and self._poller is None:
            self._poller = asyncio.create_task(self._poll(), name="lease-poller")

    async def shutdown(self) -> None:
        tasks = [*self._router_tasks.values(), *self._worker_tasks.values(), *([self._poller] if self._poller else [])]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.leases.release_all()

    def _busy(self) -> bool:
        return (
            any(not t.done() for t in self._worker_tasks.values())
            or any(not q.empty() for q in self._queues.values())
            or any(self._router_busy.values())
        )

    async def idle(self, timeout: float = 30.0) -> None:
        """Wait until no router batch or worker is in flight (tests/CLI)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        quiet = 0
        while loop.time() < deadline:
            quiet = 0 if self._busy() else quiet + 1
            if quiet >= 3:  # stable for a few ticks (callbacks may start new tasks)
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("runtime did not become idle")

    def active_projects(self) -> list[int]:
        with session_scope(self.engine) as s:
            return [p.id for p in s.exec(select(Project).where(Project.status == "active")).all()]
