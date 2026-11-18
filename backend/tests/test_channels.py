"""Phase 8: WhatsApp + email intake and alerts, against a fake Graph API and a fake mail channel."""
import asyncio
import hashlib
import hmac
import json
import time
from itertools import count

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.channels.whatsapp import WhatsAppChannel
from app.db import session_scope
from app.models import (
    ActionLog, Alert, ChatMessage, DailyUpdate, Event, InboundMessage, Membership, Milestone, Notification,
    NotificationPref, StoredFile, User,
)

OWNER, PM, APEX, VIEWER = "919800000001", "919800000002", "919800000010", "919800000099"
_ids = count(1)


class FakeGraph:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed_window: set[str] = set()  # recipients outside the 24h window

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith("/PNID/messages"):
            body = json.loads(request.content)
            if body["to"] in self.closed_window and body["type"] != "template":
                return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})
            self.sent.append(body)
            return httpx.Response(200, json={"messages": [{"id": f"wamid.out{len(self.sent)}"}]})
        if request.method == "GET" and url.endswith("/media-1"):
            return httpx.Response(200, json={"url": "https://lookaside.example/media-1", "mime_type": "image/jpeg"})
        if request.method == "GET" and url.startswith("https://lookaside.example/"):
            return httpx.Response(200, content=b"\xff\xd8fake-jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(404, json={"error": {"code": 100, "message": "not found"}})

    def to(self, number: str) -> list[dict]:
        return [m for m in self.sent if m["to"] == number]


class FakeEmail:
    name = "email"

    def __init__(self):
        self.sent: list[dict] = []

    def configured(self):
        return True

    def send(self, to, subject, body, link="", actions=None):
        self.sent.append({"to": to, "subject": subject, "body": body, "actions": actions or []})
        return f"<{len(self.sent)}@test>"


@pytest.fixture()
def env(db):
    from app.main import app
    from app.notify import CHANNELS

    graph, mail = FakeGraph(), FakeEmail()
    saved = dict(CHANNELS)
    CHANNELS["whatsapp"] = WhatsAppChannel(client=httpx.Client(transport=httpx.MockTransport(graph.handler)))
    CHANNELS["email"] = mail
    with session_scope() as s:  # owner + pm opted into WhatsApp, viewer gets a phone
        for email in ("owner@demo.local", "pm@demo.local"):
            u = s.exec(select(User).where(User.email == email)).one()
            s.add(NotificationPref(user_id=u.id, whatsapp_enabled=True, email_enabled=True, quiet_start_hour=None, quiet_end_hour=None))
        v = s.exec(select(User).where(User.email == "viewer@demo.local")).one()
        v.phone = "+" + VIEWER
        s.add(v)
    with TestClient(app) as c:
        yield c, graph, mail, app
    CHANNELS.clear()
    CHANNELS.update(saved)


def wa_post(c: TestClient, sender: str, msg: dict, secret: str = "app-secret"):
    msg = {"from": sender, "id": f"wamid.in{next(_ids)}", "timestamp": str(int(time.time())), **msg}
    body = json.dumps({"object": "whatsapp_business_account",
                       "entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "messages": [msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/api/channels/whatsapp/webhook", content=body,
                  headers={"content-type": "application/json", "x-hub-signature-256": sig}), msg["id"]


def text(sender, body):
    return {"type": "text", "text": {"body": body}}


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def last_text(graph, number):
    msgs = graph.to(number)
    m = msgs[-1]
    return m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]


def present_alert(app, n_options=2, severity="critical") -> int:
    """Run the Router's real present_alert tool (as the agent would)."""
    from app.harness.router import PresentArgs, _present
    from app.harness.tools import ToolContext

    with session_scope() as s:
        al = Alert(project_id=1, title="Steel batch 2 late", severity=severity, status="open", rule_key="procurement_late:1")
        s.add(al)
        s.flush()
        aid = al.id
    opts = [{"label": f"Option {chr(65 + i)}", "description": f"do thing {i}", "impact_days": 10 + i, "impact_cost": 100000 * (i + 1)}
            for i in range(n_options)]
    _present(ToolContext(runtime=app.state.rt, project_id=1, actor="router"),
             PresentArgs(alert_id=aid, message="Steel is 34 days late.", options=opts, recommended=0))
    return aid


# --------------------------------------------------------------------------- webhook security


def test_verify_and_signature(env):
    c, *_ = env
    r = c.get("/api/channels/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert c.get("/api/channels/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}).status_code == 403
    r, _ = wa_post(c, APEX, text(APEX, "hello"), secret="wrong-secret")
    assert r.status_code == 403


def test_unknown_sender_dropped_silently(env):
    c, graph, *_ = env
    r, mid = wa_post(c, "15550001111", text("x", "send me money"))
    assert r.status_code == 200 and graph.sent == []
    with session_scope() as s:
        assert s.exec(select(InboundMessage).where(InboundMessage.provider_id == mid)).one().outcome == "dropped:unknown_sender"


# --------------------------------------------------------------------------- contractor intake


def test_contractor_report_and_idempotency(env):
    c, graph, *_ = env
    r, mid = wa_post(c, APEX, text(APEX, "Erected 10 beams L3, 21 workers. Crane OK."))
    assert r.json()["handled"] == 1
    assert "Report received" in last_text(graph, APEX)
    with session_scope() as s:
        u = s.exec(select(DailyUpdate).where(DailyUpdate.raw_text.contains("10 beams"))).one()
        assert u.parsed["channel"] == "whatsapp" and u.contractor_id == 2
        ev = s.exec(select(Event).where(Event.kind == "daily_update")).one()
        assert ev.status == "to_worker" and ev.payload["channel"] == "whatsapp"
    # provider redelivers the same message id -> ignored
    body_msg = {"from": APEX, "id": mid, "type": "text", "text": {"body": "Erected 10 beams L3, 21 workers. Crane OK."}}
    raw = json.dumps({"entry": [{"changes": [{"value": {"messages": [body_msg]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    c.post("/api/channels/whatsapp/webhook", content=raw, headers={"content-type": "application/json", "x-hub-signature-256": sig})
    with session_scope() as s:
        assert len(s.exec(select(DailyUpdate).where(DailyUpdate.raw_text.contains("10 beams"))).all()) == 1


def test_photo_first_then_text_attaches_photo(env):
    c, graph, *_ = env
    wa_post(c, APEX, {"type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg"}})
    assert "Got 1 photo" in last_text(graph, APEX)
    with session_scope() as s:
        f = s.exec(select(StoredFile).where(StoredFile.source == "whatsapp")).one()
        assert f.pending_for_user_id is not None and f.project_id == 1
        fid = f.id
    wa_post(c, APEX, text(APEX, "Columns grid D erected, see photo"))
    assert "with 1 photo" in last_text(graph, APEX)
    with session_scope() as s:
        u = s.exec(select(DailyUpdate).where(DailyUpdate.raw_text.contains("grid D"))).one()
        assert u.parsed["files"] == [fid]
        assert s.get(StoredFile, fid).pending_for_user_id is None


def test_photos_reach_worker_as_images(db):
    """The Site Updates worker gets photos as image blocks (Claude vision)."""
    from app.harness.llm import text_response
    from app.harness.runtime import Runtime
    from app.services import ingest_daily_update
    from app.storage import save_file

    from .conftest import RoleLLM

    llm = RoleLLM({"site_updates": [text_response("SUMMARY: ok")], "router": [text_response("ok")]})
    rt = Runtime(llm)

    async def go():
        f = save_file(b"\x89PNGfake", "site.png", 1, content_type="image/png")
        await ingest_daily_update(rt, 1, 2, "photo of L3", None, "test", [f.id])
        await rt.idle()

    asyncio.run(go())
    first = next(c for c in llm.calls if c["role"] == "site_updates")["messages"][0]["content"]
    assert isinstance(first, list)
    img = [b for b in first if b["type"] == "image"]
    assert len(img) == 1 and img[0]["source"]["media_type"] == "image/png"
    assert "untrusted" in " ".join(b.get("text", "") for b in first)


def test_read_only_and_multi_project_routing(env):
    c, graph, *_ = env
    wa_post(c, VIEWER, text(VIEWER, "what's up"))
    assert "read-only" in last_text(graph, VIEWER)
    # pm is on two projects -> must pick one
    wa_post(c, PM, text(PM, "How is the steel?"))
    assert "Which project" in last_text(graph, PM) and "#1" in last_text(graph, PM) and "#2" in last_text(graph, PM)
    wa_post(c, PM, text(PM, "#2 how are the villas?"))
    with session_scope() as s:
        m = s.exec(select(ChatMessage).where(ChatMessage.content == "how are the villas?")).one()
        assert m.project_id == 2 and m.meta["channel"] == "whatsapp"


# --------------------------------------------------------------------------- owner decisions


def test_alert_buttons_and_button_reply(env):
    c, graph, mail, app = env
    aid = present_alert(app, n_options=3)
    assert wait_for(lambda: graph.to(OWNER))
    msg = graph.to(OWNER)[-1]
    assert msg["type"] == "interactive"
    buttons = msg["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == [f"alert:{aid}:opt:{i}" for i in range(3)]
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons)
    assert wait_for(lambda: any(e["to"] == "owner@demo.local" for e in mail.sent))
    email = next(e for e in mail.sent if e["to"] == "owner@demo.local")
    assert email["subject"].startswith(f"[CPM alert:{aid}]") and "/api/channels/email/act?t=" in email["actions"][0]["url"]

    wa_post(c, OWNER, {"type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": f"alert:{aid}:opt:1", "title": "Option B"}}})
    assert "Decision recorded: Option B" in last_text(graph, OWNER)
    with session_scope() as s:
        assert s.get(Alert, aid).status == "resolved"
        ev = s.exec(select(Event).where(Event.kind == "decision")).one()
        assert ev.payload["channel"] == "whatsapp" and "via whatsapp" in ev.payload["by"]
    # a second press on a decided alert is rejected politely
    wa_post(c, PM, {"type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": f"alert:{aid}:opt:0"}}})
    assert "already resolved" in last_text(graph, PM)


def test_plain_number_reply_uses_latest_context(env):
    c, graph, _, app = env
    aid = present_alert(app, n_options=2)
    assert wait_for(lambda: graph.to(PM))
    wa_post(c, PM, text(PM, "2"))
    assert "Decision recorded: Option B" in last_text(graph, PM)
    with session_scope() as s:
        assert s.get(Alert, aid).status == "resolved"


def test_locked_approval_owner_only(env):
    c, graph, _, app = env
    from app.harness import authz

    with session_scope() as s:
        mid = s.exec(select(Milestone).where(Milestone.project_id == 1)).first().id
    asyncio.run(authz.perform(app.state.rt, 1, "worker:planner#1", "schedule.change", {"milestone_id": mid, "planned_end": "2027-02-28"}))
    assert wait_for(lambda: graph.to(OWNER))
    btns = graph.to(OWNER)[-1]["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in btns] == ["Approve", "Reject"]
    assert graph.to(PM) == []  # owner's rep is not asked for owner-only approvals
    with session_scope() as s:
        aid = s.exec(select(ActionLog).where(ActionLog.status == "pending_approval")).one().id
    wa_post(c, OWNER, text(OWNER, "approve"))
    assert "Approved" in last_text(graph, OWNER)
    with session_scope() as s:
        assert s.get(ActionLog, aid).status == "approved"
        assert str(s.get(Milestone, mid).planned_end) == "2027-02-28"


def test_24h_window_template_then_resend(env):
    c, graph, _, app = env
    graph.closed_window.add(OWNER)
    present_alert(app)
    assert wait_for(lambda: any(m["type"] == "template" for m in graph.to(OWNER)))
    present_alert(app)  # second item while the window is still closed
    time.sleep(0.3)
    assert sum(m["type"] == "template" for m in graph.to(OWNER)) == 1  # one nudge, not one per item
    with session_scope() as s:
        owner = s.exec(select(User).where(User.email == "owner@demo.local")).one()
        waiting = s.exec(select(Notification).where(Notification.user_id == owner.id, Notification.status == "awaiting_window")).all()
        assert len(waiting) == 2
    graph.closed_window.clear()
    wa_post(c, OWNER, text(OWNER, "hi"))  # owner opens the window
    assert sum(m["type"] == "interactive" for m in graph.to(OWNER)) == 2
    with session_scope() as s:
        assert not s.exec(select(Notification).where(Notification.status == "awaiting_window")).all()


def test_owner_chat_reply_comes_back_on_whatsapp(env):
    c, graph, *_ = env
    wa_post(c, OWNER, text(OWNER, "#1 How are we doing?"))
    # The (stub) project manager's answer is sent back on WhatsApp.
    assert wait_for(lambda: any("stub" in (m.get("text") or {}).get("body", "") for m in graph.to(OWNER)), timeout=10)
    with session_scope() as s:
        assert s.exec(select(ChatMessage).where(ChatMessage.content == "How are we doing?")).one().meta["channel"] == "whatsapp"


def test_contractor_reminder_is_delivered(env):
    _, graph, _, app = env
    from app.harness import authz

    msg = asyncio.run(authz.perform(app.state.rt, 1, "worker:site_updates#1", "contractor.remind",
                                    {"contractor_id": 2, "message": "No daily report for 4 days - please send today's."}))
    assert "queued on whatsapp" in msg
    assert wait_for(lambda: graph.to(APEX))
    assert "No daily report for 4 days" in last_text(graph, APEX)


def test_delivery_receipts_update_outbox(env):
    c, graph, _, app = env
    present_alert(app)
    assert wait_for(lambda: graph.to(OWNER))
    with session_scope() as s:
        n = s.exec(select(Notification).where(Notification.channel == "whatsapp", Notification.provider_id != "")).first()
        pid = n.provider_id
    raw = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": pid, "status": "read"}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    c.post("/api/channels/whatsapp/webhook", content=raw, headers={"content-type": "application/json", "x-hub-signature-256": sig})
    with session_scope() as s:
        assert s.exec(select(Notification).where(Notification.provider_id == pid)).one().status == "read"


# --------------------------------------------------------------------------- email


def test_email_reply_with_subject_tag(env):
    c, _, mail, app = env
    aid = present_alert(app, n_options=2)
    assert c.post("/api/channels/email/inbound/wrong", data={"from": "x"}).status_code == 403
    r = c.post("/api/channels/email/inbound/inbound-secret", data={
        "from": "Riya Mehta <OWNER@demo.local>", "subject": f"Re: [CPM alert:{aid}] Decision needed: Steel batch 2 late",
        "text": "1\n\nOn Tue, 18 Nov 2026 at 10:00, Site Manager wrote:\n> Steel is late\n> 2. Option B",
    })
    assert r.status_code == 200
    with session_scope() as s:
        assert s.get(Alert, aid).status == "resolved"
        assert s.exec(select(Event).where(Event.kind == "decision")).one().payload["choice"] == "Option A"
    assert any("Decision recorded" in e["body"] for e in mail.sent if e["to"] == "owner@demo.local")


def test_email_contractor_report_with_attachment(env):
    c, _, mail, _ = env
    with session_scope() as s:  # give Apex a second contact by email
        apex = s.exec(select(User).where(User.email == "site@apexsteel.example")).one()
    r = c.post("/api/channels/email/inbound/inbound-secret",
               data={"from": "site@apexsteel.example", "subject": "DPR 18 Nov", "text": "Erected 4 beams L4"},
               files={"attachment1": ("photo.jpg", b"\xff\xd8jpg", "image/jpeg")})
    assert r.status_code == 200
    with session_scope() as s:
        u = s.exec(select(DailyUpdate).where(DailyUpdate.raw_text == "Erected 4 beams L4")).one()
        assert len(u.parsed["files"]) == 1 and u.parsed["channel"] == "email"
    assert any(e["to"] == apex.email and "Report received with 1 photo" in e["body"] for e in mail.sent)


def test_email_action_link_needs_post(env):
    c, _, _, app = env
    from app.channels.links import make_token

    aid = present_alert(app, n_options=2)
    with session_scope() as s:
        owner = s.exec(select(User).where(User.email == "owner@demo.local")).one()
    tok = make_token(owner.id, f"alert:{aid}:opt:1")
    r = c.get("/api/channels/email/act", params={"t": tok})
    assert r.status_code == 200 and "Choose: Option B" in r.text
    with session_scope() as s:
        assert s.get(Alert, aid).status == "awaiting_owner"  # GET (link scanners) changes nothing
    r = c.post("/api/channels/email/act", data={"t": tok})
    assert "Decision recorded: Option B" in r.text
    assert c.get("/api/channels/email/act", params={"t": tok[:-3] + "abc"}).status_code == 400
    expired = make_token(owner.id, f"alert:{aid}:opt:0", ttl=-1)
    assert "expired" in c.get("/api/channels/email/act", params={"t": expired}).text

    with session_scope() as s:  # a viewer's link is refused by role, not by token
        viewer = s.exec(select(User).where(User.email == "viewer@demo.local")).one()
        assert s.exec(select(Membership).where(Membership.user_id == viewer.id)).first().role == "viewer"
    aid2 = present_alert(app)
    r = c.post("/api/channels/email/act", data={"t": make_token(viewer.id, f"alert:{aid2}:opt:0")})
    assert "decide this alert" in r.text and "Your role" in r.text


def test_profile_phone_and_channel_status(env):
    c, graph, *_ = env
    from .test_api_auth import login

    h = login(c, "consultant@demo.local")
    assert c.put("/api/me", headers=h, json={"name": "Neha", "phone": "98000"}).status_code == 400
    assert c.put("/api/me", headers=h, json={"name": "Neha", "phone": "+91 98000 00077"}).json()["phone"] == "+919800000077"
    st = c.get("/api/channels/status", headers=h).json()
    assert st["whatsapp"] == {"outbound": True, "inbound": True, "template": True}
    # the new number now identifies her on WhatsApp (consultant = read-only there)
    wa_post(c, "919800000077", text("", "hello there"))
    assert "read-only" in last_text(graph, "919800000077")
