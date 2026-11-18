"""Phase 11: contracts, change orders, pay applications, overbilling, cash flow."""
import asyncio

from sqlmodel import select

from app.db import session_scope
from app.models import ActionLog, ChangeOrder, CostEntry, Event, PayApplication, PayAppLine, WorkItem

from .test_api_auth import client, login  # noqa: F401  (fixture)


def test_engine_figures_from_seed(db):
    from app.tracking.service import project_health

    with session_scope() as s:
        h = project_health(s, 1)
    c = h.commercial
    assert c.committed == 418_600_000  # contracts 416M + approved dewatering CO 2.6M
    assert c.pending_exposure == 6_300_000 and c.uncommitted == 64_000_000  # facade not awarded
    assert c.forecast_final == c.committed + c.pending_exposure + c.uncommitted
    ob = {o.description: o for o in c.overbilling}
    assert ob["Erect steel L1-L2"].claimed_pct == 85 and ob["Erect steel L1-L2"].recorded_pct == 60
    assert ob["Erect steel L1-L2"].excess_value == 7_500_000
    keys = {x.rule_key for x in h.rule_hits}
    assert any(k.startswith("overbilling:") for k in keys)
    flow = c.cashflow
    assert flow[-1]["planned_cum"] == 480_000_000 and flow[-1]["forecast_cum"] == c.forecast_final
    assert all(a["planned_cum"] <= b["planned_cum"] for a, b in zip(flow, flow[1:]))  # cumulative
    assert h.to_dict()["commercial_summary"]["overbilling_items"] == 2
    g = None
    with session_scope() as s:
        g = project_health(s, 2)
    assert g.commercial is None  # no contracts -> no commercial block


def test_contractor_sees_only_own_commercial(client):  # noqa: F811
    hc = login(client, "site@apexsteel.example")
    r = client.get("/api/projects/1/commercial", headers=hc).json()
    assert [c["title"] for c in r["contracts"]] == ["Structural steel supply and erection"]
    assert "budget" not in r["summary"] and {o["contract_id"] for o in r["summary"]["overbilling"]} == {r["contracts"][0]["id"]}
    assert all(co["contract_id"] == r["contracts"][0]["id"] for co in r["change_orders"])
    ho = login(client, "owner@demo.local")
    full = client.get("/api/projects/1/commercial", headers=ho).json()
    assert len(full["contracts"]) == 3 and full["summary"]["forecast_final"] == 488_900_000


def test_change_order_flow_and_permissions(client):  # noqa: F811
    hc = login(client, "site@apexsteel.example")
    ho = login(client, "owner@demo.local")
    steel_id = client.get("/api/projects/1/commercial", headers=hc).json()["contracts"][0]["id"]
    civil_id = next(c["id"] for c in client.get("/api/projects/1/commercial", headers=ho).json()["contracts"] if c["title"].startswith("Civil"))
    assert client.post("/api/projects/1/change-orders", headers=hc, json={"title": "Not mine", "proposed_amount": 1, "contract_id": civil_id}).status_code == 403
    r = client.post("/api/projects/1/change-orders", headers=hc, json={
        "title": "Extra bracing bay at grid H", "proposed_amount": 900_000, "time_impact_days": 2,
        "contract_id": steel_id, "reason": "design_change", "source_ref": "rfi:1"})
    assert r.status_code == 200, r.text
    co = r.json()
    assert co["stage"] == "submitted" and co["number"] == 4
    with session_scope() as s:  # commercial manager assesses it automatically
        ev = s.exec(select(Event).where(Event.kind == "change_order_submitted")).one()
        assert ev.status == "to_worker"
        assert s.exec(select(WorkItem).where(WorkItem.worker == "cost")).first() is not None
    hp = login(client, "pm@demo.local")
    assert client.post(f"/api/change-orders/{co['id']}/decide", headers=hp, json={"approve": True}).status_code == 403
    before = client.get("/api/projects/1/commercial", headers=ho).json()["summary"]["committed"]
    d = client.post(f"/api/change-orders/{co['id']}/decide", headers=ho, json={"approve": True, "amount": 750_000, "note": "agreed rate"})
    assert d.status_code == 200 and d.json()["stage"] == "approved" and d.json()["approved_amount"] == 750_000
    after = client.get("/api/projects/1/commercial", headers=ho).json()["summary"]
    assert after["committed"] == before + 750_000
    assert client.post(f"/api/change-orders/{co['id']}/decide", headers=ho, json={"approve": False}).status_code == 409


def test_pay_app_certify_with_reduction_books_actual_cost(client):  # noqa: F811
    ho = login(client, "owner@demo.local")
    hcons = login(client, "consultant@demo.local")
    with session_scope() as s:
        app = s.exec(select(PayApplication).where(PayApplication.status == "submitted")).one()
        lines = {pl.contract_line_id: pl for pl in s.exec(select(PayAppLine).where(PayAppLine.pay_app_id == app.id)).all()}
        l1, l2 = sorted(lines)
        actual_before = sum(c.amount for c in s.exec(select(CostEntry).where(CostEntry.kind == "actual")).all())
    hc = login(client, "site@apexsteel.example")
    assert client.post(f"/api/pay-apps/{app.id}/certify", headers=hc, json={}).status_code == 403
    assert client.post(f"/api/pay-apps/{app.id}/reject", headers=hcons, json={"note": ""}).status_code == 422
    # certify at recorded progress: 60% on L1-L2, 0% on L3-L4 (can never exceed the claim)
    r = client.post(f"/api/pay-apps/{app.id}/certify", headers=hcons, json={"line_pcts": {str(l1): 60, str(l2): 99}, "note": "per site progress"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "certified"
    cert = {x["contract_line_id"]: x["certified_pct"] for x in out["lines"]}
    assert cert[l1] == 60 and cert[l2] == 15  # 99 capped at the 15% claimed
    assert out["certified_to_date"] == 30_000_000 * 0.60 + 30_000_000 * 0.15
    assert out["amount_due"] == round((out["certified_to_date"] - 12_000_000) * 0.95, 2)
    with session_scope() as s:
        actual_after = sum(c.amount for c in s.exec(select(CostEntry).where(CostEntry.kind == "actual")).all())
    assert round(actual_after - actual_before) == round(out["certified_to_date"] - 12_000_000)  # period value booked
    s2 = client.get("/api/projects/1/commercial", headers=ho).json()["summary"]
    assert not any(o["description"] == "Erect steel L1-L2" for o in s2["overbilling"])  # certified at progress


def test_one_open_pay_app_per_contract(client):  # noqa: F811
    hc = login(client, "site@apexsteel.example")
    steel = client.get("/api/projects/1/commercial", headers=hc).json()["contracts"][0]
    r = client.post("/api/projects/1/pay-apps", headers=hc, json={"contract_id": steel["id"], "claims": [{"contract_line_id": steel["lines"][0]["id"], "claimed_pct": 90}]})
    assert r.status_code == 409  # RA bill 2 is still awaiting certification


def test_agent_certification_needs_owner_approval(db):
    from app.harness import authz
    from app.harness.llm import StubLLM
    from app.harness.runtime import Runtime

    with session_scope() as s:
        app = s.exec(select(PayApplication).where(PayApplication.status == "submitted")).one()
        line = min(pl.contract_line_id for pl in s.exec(select(PayAppLine).where(PayAppLine.pay_app_id == app.id)).all())
    rt = Runtime(StubLLM())
    msg = asyncio.run(authz.perform(rt, 1, "worker:cost#1", "payapp.certify",
                                    {"pay_app_id": app.id, "line_pcts": {str(line): 60}, "note": "claims ahead of progress"}))
    assert "approval" in msg
    with session_scope() as s:
        assert s.get(PayApplication, app.id).status == "submitted"  # nothing paid by the agent
        entry = s.exec(select(ActionLog).where(ActionLog.action == "payapp.certify")).one()
        assert entry.status == "pending_approval"
    with session_scope() as s, __import__("pytest").raises(ValueError):
        authz.set_policy(s, 1, "payapp.certify", "auto")  # money stays locked to approval
    asyncio.run(authz.decide_approval(rt, entry.id, True, actor="owner:owner@demo.local"))
    with session_scope() as s:
        assert s.get(PayApplication, app.id).status == "certified"


def test_co_assessment_action_is_auto(db):
    from app.harness import authz
    from app.harness.llm import StubLLM
    from app.harness.runtime import Runtime

    with session_scope() as s:
        co = s.exec(select(ChangeOrder).where(ChangeOrder.number == 3)).one()
    msg = asyncio.run(authz.perform(Runtime(StubLLM()), 1, "worker:cost#1", "change_order.assess",
                                    {"change_order_id": co.id, "assessment": "Delay of batch 2 is a supplier risk under the contract; partial entitlement.",
                                     "recommended_amount": 2_000_000, "time_impact_days": 0}))
    assert msg.startswith("executed")
    with session_scope() as s:
        co = s.get(ChangeOrder, co.id)
        assert co.recommended_amount == 2_000_000 and co.stage == "submitted"  # assessed, not decided
