"""Phase 7: login, RBAC, notifications, storage, migrations."""
import asyncio
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import session_scope
from app.models import ActionLog, LoginCode, Milestone, Notification, NotificationPref, User


@pytest.fixture()
def client(db):
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(c: TestClient, email: str) -> dict:
    r = c.post("/api/auth/request-code", json={"email": email})
    assert r.status_code == 200
    code = r.json()["dev_code"]
    r = c.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    c.cookies.clear()  # explicit bearer headers so one client can act as many users
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_flow_and_me(client):
    r = client.post("/api/auth/request-code", json={"email": "nobody@x.com"})
    assert r.json() == {"sent": True}  # no account enumeration, no code
    r = client.post("/api/auth/request-code", json={"email": "PM@demo.local"})
    code = r.json()["dev_code"]
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/api/auth/verify", json={"email": "pm@demo.local", "code": wrong}).status_code == 401
    r = client.post("/api/auth/verify", json={"email": "pm@demo.local", "code": code})
    assert r.status_code == 200 and "cpm_session" in r.cookies
    me = client.get("/api/me").json()  # cookie auth
    assert me["email"] == "pm@demo.local"
    assert {p["role"] for p in me["projects"]} == {"owner_rep"}
    assert client.post("/api/auth/verify", json={"email": "pm@demo.local", "code": code}).status_code == 401  # single use
    assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_code_attempts_are_limited(client):
    r = client.post("/api/auth/request-code", json={"email": "viewer@demo.local"})
    code = r.json()["dev_code"]
    for _ in range(5):
        client.post("/api/auth/verify", json={"email": "viewer@demo.local", "code": "999999" if code != "999999" else "888888"})
    # even the right code is refused after too many attempts
    assert client.post("/api/auth/verify", json={"email": "viewer@demo.local", "code": code}).status_code == 401
    with session_scope() as s:
        assert s.exec(select(LoginCode).where(LoginCode.email == "viewer@demo.local")).one().attempts >= 5


MATRIX = {
    "owner@demo.local": {("GET", "/api/projects/1/health"): 200, ("POST", "/api/projects/1/chat"): 202,
                         ("PUT", "/api/projects/1/policies/procurement.expedite"): 200, ("GET", "/api/projects/2/health"): 200},
    "pm@demo.local": {("GET", "/api/projects/1/health"): 200, ("POST", "/api/projects/1/chat"): 202,
                      ("PUT", "/api/projects/1/policies/procurement.expedite"): 403, ("POST", "/api/projects/1/members"): 403},
    "consultant@demo.local": {("GET", "/api/projects/1/health"): 200, ("POST", "/api/projects/1/chat"): 403,
                              ("POST", "/api/projects/1/health-check"): 200, ("GET", "/api/projects/2/health"): 404},
    "viewer@demo.local": {("GET", "/api/projects/1/health"): 200, ("POST", "/api/projects/1/chat"): 403,
                          ("POST", "/api/projects/1/health-check"): 403, ("GET", "/api/projects/1/events"): 403},
    "site@apexsteel.example": {("GET", "/api/projects/1/health"): 403, ("GET", "/api/projects/1/chat"): 403,
                               ("POST", "/api/projects/1/chat"): 403, ("GET", "/api/projects/2/health"): 404,
                               ("GET", "/api/projects/1/stream"): 403},
}
BODIES = {"/api/projects/1/chat": {"text": "hi"}, "/api/projects/1/policies/procurement.expedite": {"mode": "auto"},
          "/api/projects/1/members": {"email": "x@y.z", "role": "viewer"}}


@pytest.mark.parametrize("email", list(MATRIX))
def test_rbac_matrix(client, email):
    h = login(client, email)
    for (method, path), expected in MATRIX[email].items():
        r = client.request(method, path, headers=h, json=BODIES.get(path))
        assert r.status_code == expected, f"{email} {method} {path} -> {r.status_code} {r.text[:200]}"


def test_contractor_scoping(client):
    h = login(client, "site@apexsteel.example")
    assert [c["name"] for c in client.get("/api/projects/1/contractors", headers=h).json()] == ["Apex Steel Erectors"]
    r = client.post("/api/projects/1/daily-updates", headers=h, json={"text": "Erected 6 beams L3"})
    assert r.status_code == 202, r.text
    r = client.post("/api/projects/1/daily-updates", headers=h, json={"text": "pretend to be civil", "contractor_id": 1})
    assert r.status_code == 403
    ho = login(client, "owner@demo.local")
    assert client.post("/api/projects/1/daily-updates", headers=ho, json={"text": "no contractor"}).status_code == 400
    assert client.post("/api/projects/1/daily-updates", headers=ho, json={"text": "civil ok", "contractor_id": 1}).status_code == 202


def test_owner_rep_cannot_approve_locked_actions(client):
    from app.harness import authz
    from app.harness.llm import StubLLM
    from app.harness.runtime import Runtime

    with session_scope() as s:
        mid = s.exec(select(Milestone).where(Milestone.project_id == 1)).first().id
    asyncio.run(authz.perform(Runtime(StubLLM()), 1, "worker:planner#1", "schedule.change", {"milestone_id": mid, "planned_end": "2027-01-31"}))
    with session_scope() as s:
        aid = s.exec(select(ActionLog).where(ActionLog.status == "pending_approval")).one().id
    hp = login(client, "pm@demo.local")
    assert client.post(f"/api/actions/{aid}/decide", headers=hp, json={"approve": True}).status_code == 403
    ho = login(client, "owner@demo.local")
    r = client.post(f"/api/actions/{aid}/decide", headers=ho, json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert "decided by owner:owner@demo.local" in r.json()["reason"]


def test_members_management(client):
    ho = login(client, "owner@demo.local")
    r = client.post("/api/projects/1/members", headers=ho, json={"email": "New.Guy@demo.local", "role": "contractor"})
    assert r.status_code == 400  # contractor members must be linked to a contractor
    r = client.post("/api/projects/1/members", headers=ho, json={"email": "new.guy@demo.local", "role": "contractor", "contractor_id": 1})
    assert r.status_code == 200
    h = login(client, "new.guy@demo.local")
    assert [c["id"] for c in client.get("/api/projects/1/contractors", headers=h).json()] == [1]
    members = client.get("/api/projects/1/members", headers=ho).json()
    mid = next(m["id"] for m in members if m["email"] == "new.guy@demo.local")
    assert client.delete(f"/api/projects/1/members/{mid}", headers=ho).status_code == 200
    assert client.get("/api/projects/1/contractors", headers=h).status_code == 404


def test_file_upload_and_scoped_download(client):
    hc = login(client, "site@apexsteel.example")
    r = client.post("/api/projects/1/files", headers=hc, files={"file": ("../../evil name.jpg", b"\xff\xd8jpegdata", "image/jpeg")})
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["filename"] == "evilname.jpg"  # path components stripped
    assert client.post("/api/projects/1/daily-updates", headers=hc, json={"text": "photo attached", "file_ids": [f["id"]]}).status_code == 202
    assert client.post("/api/projects/1/daily-updates", headers=hc, json={"text": "bad file", "file_ids": [9999]}).status_code == 400
    ho = login(client, "owner@demo.local")
    r = client.get(f"/api/files/{f['id']}", headers=ho)
    assert r.status_code == 200 and r.content == b"\xff\xd8jpegdata"
    hs = login(client, "site@shree.example")  # contractor on project 2 only
    assert client.get(f"/api/files/{f['id']}", headers=hs).status_code == 404


def test_notifications_routing(db):
    from app.notify import in_quiet_hours, notify, send_digests

    with session_scope() as s:
        owner = s.exec(select(User).where(User.email == "owner@demo.local")).one()
        pm = s.exec(select(User).where(User.email == "pm@demo.local")).one()
        s.add(NotificationPref(user_id=pm.id, immediate_min_severity="warning"))
        oid, pmid = owner.id, pm.id

    noon = datetime(2026, 11, 18, 12, 0)
    made = notify(1, "critical", "Steel late", "body", perm="alert.decide", now_local=noon)
    assert {n.user_id for n in made} == {oid, pmid}  # decision-makers only
    with session_scope() as s:
        rows = [s.get(Notification, n.id) for n in made]
    assert all(n.status == "failed" and "SMTP" in n.error for n in rows)  # attempted now; SMTP not configured in tests

    made = notify(1, "warning", "Design overdue", perm="alert.decide", now_local=noon)
    with session_scope() as s:
        by_user = {n.user_id: s.get(Notification, n.id).status for n in made}
    assert by_user[oid] == "digest" and by_user[pmid] == "failed"  # pm opted into immediate warnings

    night = datetime(2026, 11, 18, 23, 0)
    made = notify(1, "warning", "Night warning", perm="alert.decide", now_local=night)
    assert {n.user_id: n.status for n in made}[pmid] == "digest"  # quiet hours defer non-critical
    made = notify(1, "critical", "Night critical", perm="alert.decide", now_local=night)
    with session_scope() as s:
        assert all(s.get(Notification, n.id).status != "digest" for n in made)  # critical ignores quiet hours

    assert in_quiet_hours(NotificationPref(user_id=1, quiet_start_hour=21, quiet_end_hour=7), datetime(2026, 1, 1, 3))
    assert not in_quiet_hours(NotificationPref(user_id=1, quiet_start_hour=21, quiet_end_hour=7), datetime(2026, 1, 1, 12))

    send_digests()
    with session_scope() as s:
        assert not s.exec(select(Notification).where(Notification.status == "digest")).all()


def test_notify_prefs_api(client):
    h = login(client, "pm@demo.local")
    assert client.get("/api/me/notification-prefs", headers=h).json()["immediate_min_severity"] == "critical"
    r = client.put("/api/me/notification-prefs", headers=h, json={"immediate_min_severity": "warning", "quiet_start_hour": 22, "quiet_end_hour": 6})
    assert r.status_code == 200
    assert client.get("/api/me/notification-prefs", headers=h).json()["quiet_start_hour"] == 22


def test_migrations_build_schema_from_scratch(tmp_path):
    from sqlalchemy import create_engine, inspect

    from app.db import migrate

    eng = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    migrate(eng)
    names = set(inspect(eng).get_table_names())
    assert {"project", "user", "membership", "notification", "storedfile", "alembic_version"} <= names


@pytest.mark.skipif(not os.environ.get("CPM_TEST_PG_URL"), reason="set CPM_TEST_PG_URL to run against Postgres")
def test_postgres_migrate_seed_health():
    from sqlalchemy import create_engine, text
    from sqlmodel import SQLModel

    from app.db import migrate
    from app.db import session_scope as scope
    from app.seed import seed
    from app.tracking.service import project_health

    eng = create_engine(os.environ["CPM_TEST_PG_URL"])
    SQLModel.metadata.drop_all(eng)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
    migrate(eng)
    pids = seed(eng)
    with scope(eng) as s:
        h = project_health(s, pids[0])
    assert h.status == "red" and h.project_slip_days > 0
