import asyncio
import os
from datetime import timedelta

import pytest
from sqlmodel import select

from app.db import session_scope, utcnow
from app.harness import authz
from app.harness.health import run_health_check
from app.harness.llm import text_response, tool_response
from app.harness.runtime import Runtime
from app.harness.watchdog import watchdog_tick
from app.models import Activity, ActionLog, Alert, ChatMessage, Contractor, DailyUpdate, Event, Milestone, ProcurementItem, WorkItem

from .conftest import RoleLLM

pytestmark = pytest.mark.asyncio


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _post_update(rt, pid, contractor_id, text):
    with session_scope() as s:
        u = DailyUpdate(project_id=pid, contractor_id=contractor_id, report_date=utcnow().date(), raw_text=text)
        s.add(u)
        s.flush()
        uid = u.id
    return await rt.emit(pid, "contractor", "daily_update",
                         {"update": f"update:{uid}", "contractor_id": contractor_id, "text": text},
                         thread_key=f"contractor:{contractor_id}")


async def test_owner_message_gets_streamed_reply_citing_engine(db):
    pid = db[0]
    llm = RoleLLM({"router": [tool_response("get_health", {}), text_response("We are 51 days late; steel batch 2 is the driver.")]})
    rt = Runtime(llm)
    q = rt.open_stream(pid)
    await rt.emit(pid, "owner", "owner_message", {"text": "How are we doing?"})
    await rt.idle()
    with session_scope() as s:
        last = s.exec(select(ChatMessage).where(ChatMessage.project_id == pid).order_by(ChatMessage.id.desc())).first()
    assert last.role == "manager" and "51 days" in last.content
    # the get_health tool result reached the model
    second = llm.calls[1]["messages"][2]["content"][0]  # history is shared; index the tool_result turn
    assert second["type"] == "tool_result" and "forecast_end" in second["content"]
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "delta" in types and "reply_end" in types


async def test_router_batches_events_that_arrive_together(db):
    pid = db[0]
    llm = RoleLLM({"router": [text_response("noted")]})
    rt = Runtime(llm)
    for i in range(5):
        await rt.emit(pid, "engine", "rule_hit", {"rule_key": f"r{i}", "title": f"hit {i}"}, thread_key=f"rule:r{i}")
    await rt.idle()
    assert len(rt.router_batches) == 1 and len(rt.router_batches[0][1]) == 5
    router_calls = [c for c in llm.calls if c["role"] == "router"]
    assert len(router_calls) == 1
    assert router_calls[0]["messages"][0]["content"].count("kind=rule_hit") == 5


async def test_dispatch_then_direct_delivery_to_subscribed_worker(db):
    pid = db[0]
    with session_scope() as s:
        steel = s.exec(select(Contractor).where(Contractor.name == "Apex Steel Erectors")).first()
        m3 = s.exec(select(Milestone).where(Milestone.name == "Steel superstructure L1-L4")).first()
        a5 = s.exec(select(Activity).where(Activity.name == "Erect steel L1-L2")).first()
        cid, mid, aid = steel.id, m3.id, a5.id

    llm = RoleLLM({
        "router": [text_response("noted result"), text_response("noted result 2")],
        "site_updates": [
            tool_response("record_progress", {"activity_id": aid, "percent_complete": 64, "source_update_id": 99}),
            text_response("SUMMARY: progress 34%"),
            tool_response("record_progress", {"activity_id": aid, "percent_complete": 70, "source_update_id": 100}),
            text_response("SUMMARY: progress 37%"),
        ],
    })
    rt = Runtime(llm)
    await _post_update(rt, pid, cid, "Erected 6 more beams, level 3. 20 workers.")
    await rt.idle()

    with session_scope() as s:
        assert s.get(Activity, aid).percent_complete == 64
        before_ms = s.get(Milestone, mid).percent_complete  # milestone % is rolled up from activities
        wi = s.exec(select(WorkItem).where(WorkItem.worker == "site_updates")).one()
        assert wi.status == "done"
        assert s.exec(select(ActionLog).where(ActionLog.action == "progress.record")).first().status == "executed"
    # worker saw the original contractor text, fenced as untrusted
    first_worker_msg = next(c for c in llm.calls if c["role"] == "site_updates")["messages"][0]["content"]
    assert "<untrusted_contractor_report>" in first_worker_msg and "Erected 6 more beams" in first_worker_msg

    # Second update on the same thread goes straight to the worker (bypassing the router).
    with session_scope() as s:
        first = s.exec(select(Event).where(Event.kind == "daily_update")).first()
        assert first.status == "to_worker" and "fast path" in first.route_note  # no router call for raw updates
    eid2 = await _post_update(rt, pid, cid, "Erected 4 beams.")
    await rt.idle()
    with session_scope() as s:
        ev = s.get(Event, eid2)
        assert ev.status == "to_worker"
        assert s.get(Activity, aid).percent_complete == 70
        assert s.get(Milestone, mid).percent_complete > before_ms
        assert len(s.exec(select(WorkItem)).all()) == 1  # same worker resumed, not a new one
    # Router still hears about results (work_result events), never the raw update.
    router_snaps = [c["messages"][0]["content"] for c in llm.calls if c["role"] == "router"]
    assert any("work_result" in snap for snap in router_snaps)
    assert not any("Erected 4 beams" in snap for snap in router_snaps)


async def test_authz_observe_and_approval(db):
    pid = db[0]
    rt = Runtime(RoleLLM({"router": [text_response("ok")]}))
    with session_scope() as s:
        item = s.exec(select(ProcurementItem).where(ProcurementItem.project_id == pid)).first()
        notes_before = item.notes
        m = s.exec(select(Milestone).where(Milestone.project_id == pid)).all()[3]
    msg = await authz.perform(rt, pid, "worker:procurement#1", "procurement.expedite", {"item_id": item.id, "target_date": "2026-12-01", "est_cost": 500000})
    assert msg.startswith("OBSERVE MODE")
    with session_scope() as s:
        assert s.get(ProcurementItem, item.id).notes == notes_before
        assert s.exec(select(ActionLog).where(ActionLog.status == "observed")).first() is not None

    msg = await authz.perform(rt, pid, "worker:planner#2", "schedule.change", {"milestone_id": m.id, "planned_end": "2027-06-30"})
    assert "approval" in msg
    with session_scope() as s:
        pending = s.exec(select(ActionLog).where(ActionLog.status == "pending_approval")).one()
        assert str(s.get(Milestone, m.id).planned_end) != "2027-06-30"
        card = s.exec(select(ChatMessage).where(ChatMessage.kind == "approval")).one()
        assert card.meta["action_id"] == pending.id
    await authz.decide_approval(rt, pending.id, approve=True)
    await rt.idle()
    with session_scope() as s:
        assert str(s.get(Milestone, m.id).planned_end) == "2027-06-30"
        assert s.exec(select(Event).where(Event.kind == "approval_result")).one().status == "processed"

    # Baseline changes cannot be promoted to auto.
    with session_scope() as s, pytest.raises(ValueError):
        authz.set_policy(s, pid, "schedule.change", "auto")
    with session_scope() as s:
        authz.set_policy(s, pid, "procurement.expedite", "auto")
    msg = await authz.perform(rt, pid, "worker:procurement#1", "procurement.expedite", {"item_id": item.id, "target_date": "2026-12-01"})
    assert msg.startswith("executed")


async def test_fast_path_drops_non_allowlisted_and_duplicates(db):
    pid = db[0]
    rt = Runtime(RoleLLM({"router": [text_response("ok")]}))
    with session_scope() as s:
        c = s.exec(select(Contractor).where(Contractor.project_id == pid)).first()
        c.allowlisted = False
        s.add(c)
        cid = c.id
    assert await rt.emit(pid, "contractor", "daily_update", {"contractor_id": cid, "text": "x"}) is None
    a = await rt.emit(pid, "engine", "rule_hit", {"rule_key": "x"})
    b = await rt.emit(pid, "engine", "rule_hit", {"rule_key": "x"})
    assert a is not None and b is None
    await rt.idle()


async def test_health_check_emits_new_hits_once(db):
    pid = db[0]
    llm = RoleLLM({"router": [text_response("triaged")]})
    rt = Runtime(llm)
    r1 = await run_health_check(rt, pid)
    await rt.idle()
    assert r1["new_hits"] >= 5
    assert len(rt.router_batches) == 1  # all hits handled in one batched router call
    r2 = await run_health_check(rt, pid)
    assert r2["new_hits"] == 0  # already reported recently
    # an open alert suppresses re-alerting its rule
    with session_scope() as s:
        s.add(Alert(project_id=pid, title="steel", rule_key="procurement_late:1", status="awaiting_owner"))


async def test_watchdog_cancels_stuck_worker_and_tells_router(db):
    pid = db[0]
    llm = RoleLLM({"router": [text_response("ack")]})
    llm.gates["planner"] = asyncio.Event()  # planner hangs
    rt = Runtime(llm)
    wi = await rt.dispatch(pid, "planner", "Recovery options", "find options")
    await asyncio.sleep(0.05)
    assert rt.is_worker_running(wi.id)
    with session_scope() as s:
        w = s.get(WorkItem, wi.id)
        w.last_progress_at = utcnow() - timedelta(hours=1)
        s.add(w)
    findings = await watchdog_tick(rt, use_llm=False)
    assert findings and findings[0]["work_item"] == f"work_item:{wi.id}"
    await rt.idle()
    with session_scope() as s:
        assert s.get(WorkItem, wi.id).status == "cancelled"
        assert s.exec(select(Event).where(Event.kind == "watchdog")).one().status == "processed"


async def test_resume_after_restart(db):
    pid = db[0]
    with session_scope() as s:
        wi = WorkItem(project_id=pid, worker="cost", title="Explain CPI", instructions="why is CPI low",
                      status="running", transcript=[
                          {"role": "user", "content": "brief"},
                          {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_health", "input": {}}]},
                          {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
                      ], turns_used=1)
        s.add(wi)
        s.flush()
        wid = wi.id
    llm = RoleLLM({"cost": [text_response("SUMMARY: CPI low due to dewatering CO")], "router": [text_response("ok")]})
    rt = Runtime(llm)  # "new process"
    await rt.resume()
    await rt.idle()
    with session_scope() as s:
        w = s.get(WorkItem, wid)
        assert w.status == "done" and "dewatering" in w.result and w.turns_used == 2
    # it continued from the persisted transcript instead of starting over
    assert llm.calls[0]["role"] == "cost" and llm.calls[0]["n_messages"] == 3


async def test_router_present_alert_and_owner_decision_flow(db):
    pid = db[0]

    llm = RoleLLM({"router": [
        tool_response("open_alert", {"severity": "critical", "title": "Steel delay", "details": "batch 2 late", "refs": ["procurement:1"], "rule_key": "procurement_late:1"}),
        tool_response("present_alert", {"alert_id": 1, "message": "Steel is late; choose", "options": [
            {"label": "Expedite", "description": "pay vendor premium", "impact_days": 14, "impact_cost": 1500000},
            {"label": "Accept delay", "description": "re-baseline", "impact_days": 0, "impact_cost": 0}], "recommended": 0}),
        text_response("presented"),
        # after decision:
        tool_response("dispatch_work", {"worker": "procurement", "title": "Expedite steel", "instructions": "request expedite", "alert_id": 1}),
        text_response("on it"),
        text_response("noted"),
    ], "procurement": [
        tool_response("request_expedite", {"item_id": 1, "target_date": "2026-11-28", "est_cost": 1500000, "reason": "owner decision"}),
        text_response("SUMMARY: expedite logged in observe mode"),
    ]})
    rt = Runtime(llm)
    await rt.emit(pid, "engine", "rule_hit", {"rule_key": "procurement_late:1"})
    await rt.idle()
    with session_scope() as s:
        al = s.get(Alert, 1)
        assert al.status == "awaiting_owner" and len(al.options) == 2
        assert s.exec(select(ChatMessage).where(ChatMessage.kind == "alert")).one().meta["alert_id"] == 1

    # owner clicks option 0 via API path
    from app.services import record_alert_decision

    await record_alert_decision(rt, 1, 0, "", "owner:owner@demo.local")
    await rt.idle()
    with session_scope() as s:
        assert s.get(Alert, 1).status == "resolved"
        wi = s.exec(select(WorkItem).where(WorkItem.worker == "procurement")).one()
        assert wi.status == "done" and wi.alert_id == 1
        assert s.exec(select(ActionLog).where(ActionLog.action == "procurement.expedite")).one().status == "observed"


async def test_loop_guards_block_duplicate_dispatch_and_re_presenting(db):
    """Regression: on a local 8B model the Router re-dispatched planners and
    re-presented the same alert after every work_result. The harness must stop it."""
    from app.harness.router import _present, PresentArgs
    from app.harness.runtime import DispatchRefused
    from app.harness.tools import ToolContext, ToolError

    pid = db[0]
    llm = RoleLLM({"router": [text_response("ok")] * 10})
    llm.gates["planner"] = asyncio.Event()  # keep the first planner running
    rt = Runtime(llm)
    with session_scope() as s:
        al = Alert(project_id=pid, title="Steel", status="open")
        s.add(al)
        s.flush()
        aid = al.id
    await rt.dispatch(pid, "planner", "options", "x", alert_id=aid)
    with pytest.raises(DispatchRefused, match="already working"):
        await rt.dispatch(pid, "planner", "options again", "x", alert_id=aid)
    llm.gates["planner"].set()
    await rt.idle()
    await rt.dispatch(pid, "planner", "second opinion", "x", alert_id=aid)  # 2nd run allowed
    await rt.idle()
    with pytest.raises(DispatchRefused, match="already ran 2 times"):
        await rt.dispatch(pid, "planner", "third", "x", alert_id=aid)

    ctx = ToolContext(runtime=rt, project_id=pid, actor="router")
    _present(ctx, PresentArgs(alert_id=aid, message="choose", options=[]))
    with pytest.raises(ToolError, match="already in front of the owner"):
        _present(ctx, PresentArgs(alert_id=aid, message="choose again", options=[]))
    with session_scope() as s:
        assert len(s.exec(select(ChatMessage).where(ChatMessage.kind == "alert")).all()) == 1


@pytest.mark.skipif("postgresql" not in os.environ.get("CPM_DATABASE_URL", ""), reason="needs Postgres (CPM_TEST_DB_URL)")
async def test_multi_instance_only_one_router_per_project(db):
    """Two API instances share Postgres: only the lease holder runs a project's Router;
    when it goes away the other instance takes over."""
    pid = db[0]
    llm1 = RoleLLM({"router": [text_response("from instance 1")] * 3})
    llm2 = RoleLLM({"router": [text_response("from instance 2")] * 3})
    rt1, rt2 = Runtime(llm1), Runtime(llm2)
    rt1.leases.enabled = rt2.leases.enabled = True
    assert rt2.leases.acquire("router", pid)
    poll2 = asyncio.create_task(rt2._poll(interval=0.05))

    eid = await rt1.emit(pid, "owner", "owner_message", {"text": "who answers?"})
    for _ in range(100):
        await asyncio.sleep(0.05)
        with session_scope() as s:
            if s.get(Event, eid).status == "processed":
                break
    assert [c["role"] for c in llm2.calls] == ["router"] and llm1.calls == []

    poll2.cancel()
    await rt2.shutdown()  # releases its leases
    eid2 = await rt1.emit(pid, "owner", "owner_message", {"text": "and now?"})
    await rt1.idle()
    with session_scope() as s:
        assert s.get(Event, eid2).status == "processed"
    assert [c["role"] for c in llm1.calls] == ["router"]
    await rt1.shutdown()
