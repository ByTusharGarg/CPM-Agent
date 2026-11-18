"""Phase 10: document register, search, RFIs, submittals."""
import asyncio

from sqlmodel import select

from app.db import session_scope
from app.models import RFI, Activity, DesignDeliverable, Document, DocumentVersion, Event, Notification, Submittal, User, WorkItem

from .test_api_auth import client, login  # noqa: F401  (fixture)
from .test_channels import env, last_text, wa_post, wait_for  # noqa: F401  (fixture)


def make_pdf(lines: list[str]) -> bytes:
    """Minimal valid one-page PDF with real text (no extra dependencies)."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode()))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode())
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode()


# --------------------------------------------------------------------------- register + search


def test_pdf_upload_revisions_and_search(client):  # noqa: F811
    ho = login(client, "owner@demo.local")
    r0 = client.post("/api/projects/1/documents", headers=ho,
                     files={"file": ("A-101_R0 ground floor plan.pdf", make_pdf(["A-101 Ground floor plan", "Lobby ceiling height 4200 mm"]), "application/pdf")})
    assert r0.status_code == 200, r0.text
    d0 = r0.json()
    assert d0["number"] == "A-101" and d0["revision"] == "R0" and d0["new_document"] and d0["searchable"]
    r1 = client.post("/api/projects/1/documents", headers=ho,
                     files={"file": ("A-101_R1 ground floor plan.pdf", make_pdf(["A-101 Ground floor plan", "Lobby ceiling height 3900 mm"]), "application/pdf")})
    d1 = r1.json()
    assert d1["id"] == d0["id"] and d1["revision"] == "R1" and d1["supersedes"] == "R0" and not d1["new_document"]
    full = client.get(f"/api/documents/{d0['id']}", headers=ho).json()
    assert [v["revision"] for v in full["versions"]] == ["R1", "R0"] and full["versions"][0]["current"]

    hits = client.get("/api/projects/1/documents/search", params={"q": "lobby ceiling height"}, headers=ho).json()
    assert hits[0]["number"] == "A-101" and hits[0]["revision"] == "R1" and "3900" in hits[0]["excerpt"]
    assert hits[0]["cite"] == f"document:{d0['id']}#p1"
    assert all(h["current"] for h in hits)  # superseded R0 not returned by default
    old = client.get("/api/projects/1/documents/search", params={"q": "4200", "all_revisions": True}, headers=ho).json()
    assert old and old[0]["revision"] == "R0" and not old[0]["current"]

    spec = client.get("/api/projects/1/documents/search", params={"q": "U-value curtain wall"}, headers=ho).json()
    assert spec[0]["number"] == "SPEC-084413"  # seeded specification


def test_document_permissions(client):  # noqa: F811
    hv = login(client, "viewer@demo.local")
    assert client.post("/api/projects/1/documents", headers=hv, files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 403
    hc = login(client, "site@apexsteel.example")
    up = client.post("/api/projects/1/documents", headers=hc, files={"file": ("method statement.txt", b"Erection sequence L3", "text/plain")})
    assert up.status_code == 200
    seen = {d["title"] for d in client.get("/api/projects/1/documents", headers=hc).json()}
    assert "method statement" in seen  # own upload
    assert "Spec 08 44 13 Glazed aluminium curtain walls" in seen  # issued for construction
    assert "Unitised facade - bracket and stack joint details" not in seen  # still for review
    dwg_id = next(d["id"] for d in client.get("/api/projects/1/documents", headers=login(client, "owner@demo.local")).json() if d["number"] == "F-201")
    assert client.get(f"/api/documents/{dwg_id}", headers=hc).status_code == 404
    assert client.patch(f"/api/documents/{dwg_id}", headers=hc, json={"status": "for_construction"}).status_code == 403
    hcons = login(client, "consultant@demo.local")
    r = client.patch(f"/api/documents/{dwg_id}", headers=hcons, json={"status": "for_construction"})
    assert r.status_code == 200 and r.json()["status"] == "for_construction"
    with session_scope() as s:  # issuing the drawing fulfils its design deliverable
        d = s.get(Document, dwg_id)
        assert s.get(DesignDeliverable, d.design_id).status == "approved"
    assert any(x["number"] == "F-201" for x in client.get("/api/projects/1/documents", headers=hc).json())


def test_upload_dispatches_document_controller(db):
    from app.doc_services import upload_document
    from app.harness.llm import text_response
    from app.harness.runtime import Runtime

    from .conftest import RoleLLM

    RoleLLM.ROLE_MARKERS.setdefault("document_controller", "DOCUMENT CONTROLLER")
    llm = RoleLLM({"document_controller": [text_response("SUMMARY: classified")], "router": [text_response("ok")]})
    rt = Runtime(llm)

    async def go():
        await upload_document(rt, 1, b"E-501 Electrical riser diagram", "E-501 riser.txt", "text/plain", user_id=None, by="test")
        await rt.idle()

    asyncio.run(go())
    with session_scope() as s:
        ev = s.exec(select(Event).where(Event.kind == "document_uploaded")).one()
        assert ev.status == "to_worker" and "fast path" in ev.route_note
        wi = s.exec(select(WorkItem).where(WorkItem.worker == "document_controller")).one()
        assert "E-501" in wi.title and wi.status == "done"


def test_search_tool_for_agents(db):
    from app.harness.common_tools import SEARCH_DOCS
    from app.harness.llm import StubLLM
    from app.harness.runtime import Runtime
    from app.harness.tools import ToolContext

    ctx = ToolContext(runtime=Runtime(StubLLM()), project_id=1, actor="worker:design#1")
    out, err = asyncio.run(SEARCH_DOCS.run(ctx, {"query": "embed plate size steel beam"}))
    assert not err and "document:" in out and "#p1" in out and "200 x 150 x 12" in out


# --------------------------------------------------------------------------- RFIs


def test_rfi_lifecycle_and_scoping(env):  # noqa: F811
    c, graph, mail, app = env
    hc = login(c, "site@apexsteel.example")
    r = c.post("/api/projects/1/rfis", headers=hc, json={"subject": "Bolt grade at L3 splice", "question": "Can we use 10.9 bolts at the L3 column splice?"})
    assert r.status_code == 200, r.text
    rfi = r.json()
    assert rfi["number"] == 3 and rfi["status"] == "open" and rfi["contractor_id"] == 2
    with session_scope() as s:
        ev = s.exec(select(Event).where(Event.kind == "rfi_raised")).one()
        assert ev.status == "to_worker"  # fast path -> design coordinator assesses impact
        assert s.exec(select(WorkItem).where(WorkItem.worker == "design")).first() is not None
    def rfi_notices():
        with session_scope() as s:
            rows = s.exec(select(Notification).where(Notification.title.contains("RFI #3"))).all()
            return {s.get(User, n.user_id).email: n.status for n in rows}

    # Answerers (owner, owner's rep, consultant) are told; a warning goes to their digest under default prefs.
    assert wait_for(lambda: {"owner@demo.local", "pm@demo.local", "consultant@demo.local"} <= set(rfi_notices()))
    assert "site@apexsteel.example" not in rfi_notices() and set(rfi_notices().values()) == {"digest"}
    # contractor only sees own company RFIs; viewer cannot raise
    assert {x["number"] for x in c.get("/api/projects/1/rfis", headers=hc).json()} == {1, 2, 3}
    assert c.post("/api/projects/1/rfis", headers=login(c, "viewer@demo.local"), json={"subject": "abc", "question": "def"}).status_code == 403
    assert c.post(f"/api/rfis/{rfi['id']}/answer", headers=hc, json={"answer": "self answer"}).status_code == 403
    ans = c.post(f"/api/rfis/{rfi['id']}/answer", headers=login(c, "consultant@demo.local"),
                 json={"answer": "No - use 8.8 per S-001 note 1."})
    assert ans.status_code == 200 and ans.json()["status"] == "answered"
    assert wait_for(lambda: any("8.8 per S-001" in (m.get("text") or {}).get("body", "") for m in graph.sent))  # raiser told on WhatsApp
    assert c.post(f"/api/rfis/{rfi['id']}/close", headers=hc).json()["status"] == "closed"


def test_rfi_rules_from_seed(db):
    from app.tracking.service import project_health

    with session_scope() as s:
        keys = {h.rule_key for h in project_health(s, 1).rule_hits}
    assert "rfi_overdue:1" in keys  # seeded RFI #1 is 3 days past due
    assert "rfi_overdue:2" not in keys  # answered


def test_whatsapp_rfi_keyword(env):  # noqa: F811
    c, graph, *_ = env
    from .test_channels import APEX, text

    wa_post(c, APEX, text(APEX, "RFI: column base plate grout gap at grid F\nShop drawing shows 50 mm, site has 75 mm. OK to proceed?"))
    assert "RFI #3 raised" in last_text(graph, APEX)
    with session_scope() as s:
        r = s.exec(select(RFI).where(RFI.number == 3)).one()
        assert r.subject.startswith("column base plate grout gap") and "75 mm" in r.question and r.contractor_id == 2


def test_email_pdf_attachment_registered_as_document(env):  # noqa: F811
    c, *_ = env
    r = c.post("/api/channels/email/inbound/inbound-secret",
               data={"from": "site@apexsteel.example", "subject": "DPR + method statement", "text": "Erected 4 beams L4. Method statement attached."},
               files={"attachment1": ("MS-014 L4 erection.pdf", make_pdf(["MS-014 Method statement", "Tandem lift procedure"]), "application/pdf")})
    assert r.status_code == 200
    with session_scope() as s:
        d = s.exec(select(Document).where(Document.number == "MS-014")).one()
        v = s.get(DocumentVersion, d.current_version_id)
        assert v.source == "email" and v.text_chars > 0


# --------------------------------------------------------------------------- submittals


def test_submittal_review_drives_schedule(client):  # noqa: F811
    from app.tracking.service import project_health

    with session_scope() as s:
        deck = s.exec(select(Activity).where(Activity.code == "A7")).one()
        base = project_health(s, 1).schedule.by_id()[deck.id]
    hc = login(client, "site@apexsteel.example")
    r = client.post("/api/projects/1/submittals", headers=hc,
                    data={"title": "Metal deck profile and shear studs", "spec_section": "05 31 00", "activity_id": str(deck.id)},
                    files={"file": ("deck submittal.pdf", make_pdf(["Deck 1.0 mm", "Studs 19 mm"]), "application/pdf")})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["status"] == "submitted" and sub["contractor_id"] == 2 and sub["document_id"]
    with session_scope() as s:  # submittal pushed into the CPM as a start constraint
        x = s.get(Submittal, sub["id"])
        x.due_date = base.early_start.fromordinal(base.early_start.toordinal() + 30)
        s.add(x)
    with session_scope() as s:
        h = project_health(s, 1)
        a = h.schedule.by_id()[deck.id]
        assert a.driver == f"submittal:{sub['id']}"
        assert f"submittal_drives_schedule:{sub['id']}" in {k.rule_key for k in h.rule_hits}
    hp = login(client, "pm@demo.local")
    assert client.post(f"/api/submittals/{sub['id']}/review", headers=hp, json={"status": "revise_resubmit"}).status_code == 400  # needs comments
    rv = client.post(f"/api/submittals/{sub['id']}/review", headers=hp, json={"status": "revise_resubmit", "comments": "Show stud layout at beam splices"})
    assert rv.json()["status"] == "revise_resubmit"
    assert client.post(f"/api/submittals/{sub['id']}/review", headers=hc, json={"status": "approved"}).status_code == 403
    rs = client.post(f"/api/submittals/{sub['id']}/resubmit", headers=hc, data={"notes": "stud layout added"},
                     files={"file": ("deck submittal r1.pdf", make_pdf(["Deck 1.0 mm", "Stud layout at splices"]), "application/pdf")})
    assert rs.status_code == 200 and rs.json()["revision"] == 1 and rs.json()["status"] == "submitted"
    ok = client.post(f"/api/submittals/{sub['id']}/review", headers=hp, json={"status": "approved_as_noted", "comments": "OK"})
    assert ok.json()["status"] == "approved_as_noted"
    with session_scope() as s:
        a = project_health(s, 1).schedule.by_id()[deck.id]
        assert not a.driver.startswith("submittal")  # constraint released on approval
        ver = s.exec(select(DocumentVersion).where(DocumentVersion.document_id == sub["document_id"])).all()
        assert sorted(v.revision for v in ver) == ["R0", "R1"]
    with session_scope() as s:  # the contractor was told about each decision
        apex = s.exec(select(User).where(User.email == "site@apexsteel.example")).one()
        assert len(s.exec(select(Notification).where(Notification.user_id == apex.id, Notification.title.contains("Submittal"))).all()) >= 2
