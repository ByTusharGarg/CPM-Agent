"""Phase 9: schedule API, lookahead scoping, import permission, full re-baseline approval."""
import asyncio

from sqlmodel import select

from app.db import session_scope
from app.models import ActionLog, Baseline, BaselineActivity

from .test_api_auth import client, login  # noqa: F401  (fixture)
from .test_schedule_import import XER


def test_schedule_endpoint(client):  # noqa: F811
    h = login(client, "viewer@demo.local")
    r = client.get("/api/projects/1/schedule", headers=h).json()
    assert r["has_network"] and len(r["activities"]) == 17 and len(r["dependencies"]) >= 17
    crit = [a for a in r["activities"] if a["critical"]]
    assert {a["name"] for a in crit} >= {"Unitised panel installation", "Handover"}
    assert all(a["total_float"] < 0 for a in crit)
    assert r["baselines"][0]["name"] == "Contract baseline" and r["baselines"][0]["current"]
    g = client.get("/api/projects/2/schedule", headers=h).json()
    assert g["has_network"] is False  # milestone-only project still works


def test_lookahead_scoped_for_contractors(client):  # noqa: F811
    ho = login(client, "owner@demo.local")
    allw = client.get("/api/projects/1/lookahead?weeks=4", headers=ho).json()["activities"]
    hc = login(client, "site@apexsteel.example")
    mine = client.get("/api/projects/1/lookahead?weeks=4", headers=hc).json()["activities"]
    assert {a["name"] for a in mine} <= {a["name"] for a in allw}
    assert mine and all(a["name"].startswith(("Erect steel", "Metal deck")) for a in mine)  # Apex's own work only


def test_import_is_owner_only_and_replaces_network(client):  # noqa: F811
    hp = login(client, "pm@demo.local")
    files = {"file": ("plan.xer", XER, "application/octet-stream")}
    assert client.post("/api/projects/2/schedule/import", headers=hp, files=files).status_code == 403
    ho = login(client, "owner@demo.local")
    bad = client.post("/api/projects/2/schedule/import", headers=ho, files={"file": ("plan.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 400
    r = client.post("/api/projects/2/schedule/import", headers=ho, files={"file": ("plan.xer", XER, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["activities"] == 4
    sched = client.get("/api/projects/2/schedule", headers=ho).json()
    assert sched["has_network"] and {a["name"] for a in sched["activities"]} == {"Piles", "Pile caps", "MEP first fix", "MEP done"}


def test_full_rebaseline_needs_owner_approval(client):  # noqa: F811
    from app.harness import authz
    from app.harness.llm import StubLLM
    from app.harness.runtime import Runtime

    msg = asyncio.run(authz.perform(Runtime(StubLLM()), 1, "worker:planner#1", "schedule.rebaseline",
                                    {"name": "Rev 2", "move_target": True}, "glazing re-awarded"))
    assert "approval" in msg
    with session_scope() as s:
        aid = s.exec(select(ActionLog).where(ActionLog.action == "schedule.rebaseline")).one().id
    hp = login(client, "pm@demo.local")
    assert client.post(f"/api/actions/{aid}/decide", headers=hp, json={"approve": True}).status_code == 403
    ho = login(client, "owner@demo.local")
    before = client.get("/api/projects/1/schedule", headers=ho).json()
    assert client.post(f"/api/actions/{aid}/decide", headers=ho, json={"approve": True}).json()["status"] == "approved"
    after = client.get("/api/projects/1/schedule", headers=ho).json()
    assert after["baselines"][0]["name"] == "Rev 2" and after["baselines"][0]["current"]
    assert after["target_finish"] == before["forecast_finish"]  # target moved to the forecast
    assert all(a["finish_variance_days"] == 0 for a in after["activities"] if a["status"] != "done")
    with session_scope() as s:
        cur = s.exec(select(Baseline).where(Baseline.project_id == 1, Baseline.is_current == True)).one()  # noqa: E712
        assert len(s.exec(select(BaselineActivity).where(BaselineActivity.baseline_id == cur.id)).all()) == 17
