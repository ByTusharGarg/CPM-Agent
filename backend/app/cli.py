"""Terminal smoke test for the harness (no web server needed).

  python -m app.cli health  --project 1
  python -m app.cli chat    --project 1 "How are we doing?"
  python -m app.cli check   --project 1          # run the daily health check through the Router
  python -m app.cli update  --project 1 --contractor 2 "Erected 8 beams on L3, 20 workers"
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlmodel import col, select

from .db import init_db, session_scope
from .harness.health import run_health_check
from .harness.llm import make_llm
from .harness.runtime import Runtime
from .models import ChatMessage, DailyUpdate, WorkItem
from .tracking.service import project_health


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["health", "chat", "check", "update"])
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--project", type=int, default=1)
    ap.add_argument("--contractor", type=int)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    init_db()

    if a.cmd == "health":
        with session_scope() as s:
            print(json.dumps(project_health(s, a.project).summary(), indent=2))
        return

    rt = Runtime(make_llm())
    q = rt.open_stream(a.project)

    async def printer():
        while True:
            m = await q.get()
            t, d = m["type"], m["data"]
            if t == "delta":
                print(d["text"], end="", flush=True)
            elif t == "activity":
                print(f"\n  · {d['actor']}: {d['tool']}", flush=True)
            elif t == "work_item":
                print(f"\n  [work_item:{d['id']} {d['worker']} -> {d['status']}]", flush=True)
            elif t == "chat" and d["role"] == "manager" and not d["meta"].get("reply_id"):
                print(f"\n[{d['kind']}] {d['content']}\n", flush=True)

    pt = asyncio.create_task(printer())
    if a.cmd == "chat":
        with session_scope() as s:
            m = ChatMessage(project_id=a.project, role="owner", content=a.text)
            s.add(m)
            s.flush()
            cid = m.id
        await rt.emit(a.project, "owner", "owner_message", {"chat_id": cid, "text": a.text})
    elif a.cmd == "check":
        print(await run_health_check(rt, a.project, reason="cli"))
    elif a.cmd == "update":
        from .db import today

        with session_scope() as s:
            from .models import Contractor

            cname = s.get(Contractor, a.contractor).name
            u = DailyUpdate(project_id=a.project, contractor_id=a.contractor, report_date=today(), raw_text=a.text)
            s.add(u)
            s.flush()
            uid = u.id
        await rt.emit(a.project, "contractor", "daily_update",
                      {"update": f"update:{uid}", "contractor_id": a.contractor, "contractor": cname, "text": a.text},
                      thread_key=f"contractor:{a.contractor}")
    await rt.idle(timeout=a.timeout)
    await asyncio.sleep(0.2)  # let the printer drain queued stream events
    pt.cancel()
    with session_scope() as s:
        wis = s.exec(select(WorkItem).where(WorkItem.project_id == a.project).order_by(col(WorkItem.id).desc()).limit(5)).all()
        for w in wis:
            print(f"\nwork_item:{w.id} [{w.worker}] {w.status}: {w.result[:600]}")
    await rt.shutdown()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252 (no "₹")
    asyncio.run(main())
