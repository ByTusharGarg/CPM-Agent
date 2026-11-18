"""Demo data: two projects, dates relative to today so the scenario always looks 'live'.

* Riverside Commercial Complex - behind: steel batch late, facade drawings overdue,
  glazing not ordered in time, steel erector gone quiet, cost running hot.
* Greenfield Villas - roughly on track.

Run:  python -m app.seed  (add --reset to wipe the DB first)
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlmodel import SQLModel

from .db import engine, session_scope, today, utcnow
from .models import (
    ChatMessage,
    Membership,
    Organization,
    User,
    Contractor,
    CostEntry,
    DailyUpdate,
    DesignDeliverable,
    Milestone,
    ProcurementItem,
    Project,
)


def d(offset: int) -> date:
    return today() + timedelta(days=offset)


def seed(eng=None) -> list[int]:
    eng = eng or engine
    ids: list[int] = []
    with session_scope(eng) as s:
        org = Organization(name="Riverside Developers")
        s.add(org)
        s.flush()
        # ------------------------------------------------------------ project 1 (behind)
        p = Project(
            org_id=org.id,
            name="Riverside Commercial Complex",
            scope=(
                "G+8 commercial office building, 2 basements, ~32,000 m2 BUA. Steel-framed superstructure "
                "on RCC raft; unitised glass facade; central chilled-water HVAC; 6 passenger lifts. "
                "Contract: design-bid-build with separate packages for civil, steel, MEP and facade."
            ),
            location="Pune, India",
            owner_name="Riverside Developers",
            currency="INR",
            budget=480_000_000,
            start_date=d(-170),
            target_end_date=d(316),
        )
        s.add(p)
        s.flush()
        civil = Contractor(project_id=p.id, name="BuildRight Civil", trade="Civil & structure", contact="site@buildright.example")
        steel = Contractor(project_id=p.id, name="Apex Steel Erectors", trade="Structural steel", contact="pm@apexsteel.example")
        mep = Contractor(project_id=p.id, name="Volt MEP Services", trade="MEP", contact="ops@voltmep.example")
        facade = Contractor(project_id=p.id, name="ClearView Facades", trade="Facade", contact="hello@clearview.example")
        s.add_all([civil, steel, mep, facade])
        s.flush()

        m1 = Milestone(project_id=p.id, name="Mobilisation & excavation", discipline="civil", planned_start=d(-170), planned_end=d(-125), budget=22_000_000, percent_complete=100, actual_start=d(-170), actual_end=d(-122), contractor_id=civil.id, is_critical=True)
        s.add(m1); s.flush()
        m2 = Milestone(project_id=p.id, name="Foundations & basement raft", discipline="civil", planned_start=d(-130), planned_end=d(-64), budget=68_000_000, percent_complete=100, actual_start=d(-128), actual_end=d(-54), depends_on=[m1.id], contractor_id=civil.id, is_critical=True)
        s.add(m2); s.flush()
        m3 = Milestone(project_id=p.id, name="Steel superstructure L1-L4", discipline="structure", planned_start=d(-59), planned_end=d(27), budget=72_000_000, percent_complete=30, actual_start=d(-50), depends_on=[m2.id], contractor_id=steel.id, is_critical=True)
        s.add(m3); s.flush()
        m4 = Milestone(project_id=p.id, name="Steel superstructure L5-L8", discipline="structure", planned_start=d(22), planned_end=d(117), budget=70_000_000, depends_on=[m3.id], contractor_id=steel.id, is_critical=True)
        s.add(m4); s.flush()
        m5 = Milestone(project_id=p.id, name="MEP rough-in", discipline="mep", planned_start=d(58), planned_end=d(193), budget=86_000_000, depends_on=[m3.id], contractor_id=mep.id)
        s.add(m5); s.flush()
        m6 = Milestone(project_id=p.id, name="Unitised facade installation", discipline="facade", planned_start=d(103), planned_end=d(224), budget=64_000_000, depends_on=[m4.id], contractor_id=facade.id, is_critical=True)
        s.add(m6); s.flush()
        m7 = Milestone(project_id=p.id, name="Finishes & fit-out", discipline="finishes", planned_start=d(164), planned_end=d(286), budget=72_000_000, depends_on=[m5.id, m6.id], contractor_id=civil.id, is_critical=True)
        s.add(m7); s.flush()
        m8 = Milestone(project_id=p.id, name="Testing, commissioning & handover", discipline="mep", planned_start=d(270), planned_end=d(316), budget=26_000_000, depends_on=[m7.id], contractor_id=mep.id, is_critical=True)
        s.add(m8); s.flush()

        proc1 = [
            ProcurementItem(project_id=p.id, item="Structural steel - batch 2 (L3-L4 members)", vendor="Tata Structura (via JSW stockist)", quantity=420, unit="t", cost=29_400_000, lead_time_days=60, po_date=d(-75), required_on_site=d(-17), expected_delivery=d(17), status="delayed", milestone_id=m3.id, notes="Mill rolling slot slipped; vendor says 5 weeks late."),
            ProcurementItem(project_id=p.id, item="Structural steel - batch 3 (L5-L8)", vendor="Tata Structura (via JSW stockist)", quantity=510, unit="t", cost=35_700_000, lead_time_days=60, po_date=d(-20), required_on_site=d(20), expected_delivery=d(40), status="ordered", milestone_id=m4.id),
            ProcurementItem(project_id=p.id, item="Unitised glazing panels (DGU)", vendor="(not awarded)", quantity=6400, unit="m2", cost=52_000_000, lead_time_days=120, required_on_site=d(90), status="not_ordered", milestone_id=m6.id, notes="Waiting on approved facade shop drawings before PO."),
            ProcurementItem(project_id=p.id, item="Passenger lifts x6", vendor="KONE India", quantity=6, unit="nos", cost=31_000_000, lead_time_days=150, po_date=d(-60), required_on_site=d(150), expected_delivery=d(130), status="ordered", milestone_id=m7.id),
            ProcurementItem(project_id=p.id, item="Water-cooled chillers 2x400TR", vendor="Daikin", quantity=2, unit="nos", cost=18_500_000, lead_time_days=110, po_date=d(-30), required_on_site=d(120), expected_delivery=d(85), status="ordered", milestone_id=m5.id),
        ]
        s.add_all(proc1)
        design1 = [
            DesignDeliverable(project_id=p.id, title="Facade shop drawings & structural calcs", discipline="facade", due_date=d(-8), status="submitted", revision="R1", milestone_id=m6.id, notes="R0 rejected (bracket loads). R1 under consultant review."),
            DesignDeliverable(project_id=p.id, title="MEP coordinated services drawings (L1-L4)", discipline="mep", due_date=d(27), status="in_progress", milestone_id=m5.id),
            DesignDeliverable(project_id=p.id, title="Steel connection design L5-L8", discipline="structure", due_date=d(-30), status="approved", milestone_id=m4.id),
            DesignDeliverable(project_id=p.id, title="Interior finishes schedule", discipline="architecture", due_date=d(110), status="not_started", milestone_id=m7.id),
        ]
        s.add_all(design1)
        s.add_all([
            CostEntry(project_id=p.id, milestone_id=m1.id, kind="actual", amount=23_100_000, description="Excavation & shoring final account", entry_date=d(-120)),
            CostEntry(project_id=p.id, milestone_id=m2.id, kind="actual", amount=73_400_000, description="Raft & basement final account", entry_date=d(-50)),
            CostEntry(project_id=p.id, milestone_id=m2.id, kind="change_order", amount=2_600_000, description="Dewatering - unexpected water table", entry_date=d(-90)),
            CostEntry(project_id=p.id, milestone_id=m3.id, kind="actual", amount=25_800_000, description="Steel batch 1 supply + erection RA bills 1-2", entry_date=d(-6)),
            CostEntry(project_id=p.id, milestone_id=m3.id, kind="committed", amount=72_000_000, description="Steel L1-L4 package", entry_date=d(-80)),
            CostEntry(project_id=p.id, milestone_id=m5.id, kind="committed", amount=86_000_000, description="MEP package", entry_date=d(-40)),
        ])
        s.add_all([
            DailyUpdate(project_id=p.id, contractor_id=civil.id, report_date=d(-1), raw_text="Retaining wall waterproofing 80% done. 34 workers. Backfilling north side tomorrow.", processed=True),
            DailyUpdate(project_id=p.id, contractor_id=steel.id, report_date=d(-4), raw_text="Erected 12 columns + 18 beams grid C-F level 2. Waiting for batch 2 steel. 22 workers, will reduce crew to 14 from Monday.", processed=True),
            DailyUpdate(project_id=p.id, contractor_id=mep.id, report_date=d(-1), raw_text="Sleeves and embeds for L2 slab done. Shop drawings L1-L4 at 60%.", processed=True),
        ])
        s.add(ChatMessage(project_id=p.id, role="manager", content=(
            "Hi - I'm your project manager agent for Riverside Commercial Complex. I watch design, procurement, "
            "site progress and cost through specialist agents and will only interrupt you when something needs a "
            "decision. Ask me anything, e.g. \"How are we doing?\" or \"What's blocking the steel?\""
        )))
        s.flush()
        _seed_network(s, p, [m1, m2, m3, m4, m5, m6, m7, m8], proc1, design1, steel)
        ids.append(p.id)

        # ------------------------------------------------------------ project 2 (on track)
        q = Project(
            org_id=org.id,
            name="Greenfield Villas - Phase 1",
            scope="12 G+1 RCC villas with landscaping, internal roads, STP and clubhouse.",
            location="Nashik, India",
            owner_name="Greenfield Homes",
            currency="INR",
            budget=145_000_000,
            start_date=d(-60),
            target_end_date=d(300),
        )
        s.add(q)
        s.flush()
        gc = Contractor(project_id=q.id, name="Shree Constructions", trade="General contractor")
        s.add(gc)
        s.flush()
        a1 = Milestone(project_id=q.id, name="Site works & foundations (villas 1-12)", discipline="civil", planned_start=d(-60), planned_end=d(20), budget=30_000_000, percent_complete=78, actual_start=d(-58), contractor_id=gc.id, is_critical=True)
        s.add(a1); s.flush()
        a2 = Milestone(project_id=q.id, name="RCC frame & masonry", discipline="civil", planned_start=d(10), planned_end=d(130), budget=55_000_000, depends_on=[a1.id], contractor_id=gc.id, is_critical=True)
        s.add(a2); s.flush()
        a3 = Milestone(project_id=q.id, name="Services, finishes & landscaping", discipline="finishes", planned_start=d(120), planned_end=d(300), budget=60_000_000, depends_on=[a2.id], contractor_id=gc.id, is_critical=True)
        s.add(a3); s.flush()
        s.add(ProcurementItem(project_id=q.id, item="TMT rebar Fe550D", vendor="SAIL dealer", quantity=380, unit="t", cost=24_000_000, lead_time_days=14, po_date=d(-10), required_on_site=d(12), expected_delivery=d(4), status="ordered", milestone_id=a2.id))
        s.add(CostEntry(project_id=q.id, milestone_id=a1.id, kind="actual", amount=22_600_000, description="Foundations RA bills 1-3", entry_date=d(-3)))
        s.add(DailyUpdate(project_id=q.id, contractor_id=gc.id, report_date=d(-1), raw_text="Villa 9 and 10 footings cast. 41 workers.", processed=True))
        s.add(ChatMessage(project_id=q.id, role="manager", content="Hi - I'm tracking Greenfield Villas Phase 1. Things look on plan; ask me anything."))
        ids.append(q.id)

        # ------------------------------------------------------------ people (one per role)
        def user(email, name, admin=False, phone=""):
            u = User(org_id=org.id, email=email, name=name, is_org_admin=admin, phone=phone)
            s.add(u)
            s.flush()
            return u

        user("owner@demo.local", "Riya Mehta (Owner)", admin=True, phone="+919800000001")
        pm = user("pm@demo.local", "Arjun Rao (Owner's rep)", phone="+919800000002")
        cons = user("consultant@demo.local", "Neha Kulkarni (Cost consultant)")
        view = user("viewer@demo.local", "Board Viewer")
        apex = user("site@apexsteel.example", "Vikram (Apex site engineer)", phone="+919800000010")
        shree = user("site@shree.example", "Shree site office")
        s.add_all([
            Membership(user_id=pm.id, project_id=p.id, role="owner_rep"),
            Membership(user_id=pm.id, project_id=q.id, role="owner_rep"),
            Membership(user_id=cons.id, project_id=p.id, role="consultant"),
            Membership(user_id=view.id, project_id=p.id, role="viewer"),
            Membership(user_id=view.id, project_id=q.id, role="viewer"),
            Membership(user_id=apex.id, project_id=p.id, role="contractor", contractor_id=steel.id),
            Membership(user_id=shree.id, project_id=q.id, role="contractor", contractor_id=gc.id),
        ])
        s.flush()
        _seed_documents(s, p, design1, steel, facade, apex, cons)
    return ids


SPEC_0844 = """SECTION 08 44 13 - GLAZED ALUMINIUM CURTAIN WALLS (UNITISED)
Riverside Commercial Complex - Issued for Construction

1.4 PERFORMANCE REQUIREMENTS
A. Thermal: centre-of-glass U-value not more than 1.6 W/m2K; system U-value not more than 2.2 W/m2K.
B. Solar: solar heat gain coefficient (SHGC) not more than 0.25; visible light transmission at least 0.40.
C. Structural: design wind pressure 2.1 kPa; deflection of framing members limited to span/175 or 19 mm.
D. Water penetration: no uncontrolled water at 600 Pa test pressure.

1.6 SUBMITTALS
A. Shop drawings showing unit layout, bracket and anchor details, stack joints and movement capacity.
B. Structural calculations sealed by a chartered engineer.
C. Samples: 300 x 300 mm glass and frame finish samples.
D. Performance test report for a mock-up tested to clause 1.4.

2.3 ANCHORS AND BRACKETS
A. Cast-in channels in slab edges at maximum 1200 mm centres; embedment depth not less than 150 mm.
B. Brackets: hot-dip galvanised steel, three-way adjustable (+/- 25 mm).
C. Where slab edges are steel beams, weld-on embed plates 200 x 150 x 12 mm to be provided by the steel contractor.

3.2 INSTALLATION
A. Install units only after slab edge survey; tolerance +/- 10 mm.
"""

STRUCT_NOTES = """S-001 STRUCTURAL GENERAL NOTES - Revision C - For Construction
1. Structural steel to IS 2062 grade E350. Bolts property class 8.8, pre-tensioned at moment connections.
2. Composite metal deck 1.0 mm, concrete grade M30 with 75 mm topping.
3. Facade embed plates on perimeter beams per architectural facade drawings; coordinate before fabrication.
4. Fire protection: 2 hour rating to columns, 1.5 hour to beams (intumescent coating).
"""

FACADE_DWG = """F-201 UNITISED FACADE - TYPICAL BRACKET AND STACK JOINT DETAILS - Revision R1
Bracket spacing 1200 mm maximum along slab edge. Cast-in channel embedment 150 mm.
At steel perimeter beams (levels 1-8) use weld-on embed plate 200 x 150 x 12 mm at each bracket.
Stack joint movement capacity +/- 20 mm vertical. Unit width 1500 mm typical.
"""


def _seed_documents(s, p, design1, steel_contractor, facade_contractor, apex_user, consultant):
    """Documents, an open (overdue) RFI, an answered RFI and a submittal under review."""
    from sqlmodel import select as _select

    from .docs import register_document
    from .models import RFI, Activity, Submittal

    acts = {a.code: a for a in s.exec(_select(Activity).where(Activity.project_id == p.id)).all()}
    facade_dwg = design1[0]
    spec = register_document(s, p.id, SPEC_0844.encode(), "S-0844 curtain wall specification.txt", "text/plain",
                             number="SPEC-084413", title="Spec 08 44 13 Glazed aluminium curtain walls", revision="R0",
                             discipline="facade", doc_type="specification")
    spec.document.status, spec.document.classified = "for_construction", True
    notes = register_document(s, p.id, STRUCT_NOTES.encode(), "S-001 structural general notes RevC.txt", "text/plain",
                              title="Structural general notes", discipline="structure", doc_type="drawing")
    notes.document.status, notes.document.classified = "for_construction", True
    dwg = register_document(s, p.id, FACADE_DWG.encode(), "F-201_R1 facade bracket details.txt", "text/plain",
                            title="Unitised facade - bracket and stack joint details", discipline="facade", doc_type="drawing")
    dwg.document.design_id, dwg.document.activity_id, dwg.document.classified = facade_dwg.id, acts["A12"].id, True
    for r in (spec, notes, dwg):
        s.add(r.document)

    s.add(RFI(project_id=p.id, number=1, subject="Facade embed plates on L3-L4 perimeter beams",
              question="F-201 R1 shows weld-on embed plates at each facade bracket on steel perimeter beams. "
                       "Batch 2 beams for L3-L4 are being fabricated now. Confirm plate size and spacing so the fabricator "
                       "can weld them in the shop instead of on site?",
              raised_by=apex_user.id, contractor_id=steel_contractor.id, ball_in_court="consultant",
              due_date=d(-3), time_impact=True, activity_id=acts["A6"].id, document_ids=[dwg.document.id],
              created_at=utcnow() - timedelta(days=10)))
    s.add(RFI(project_id=p.id, number=2, subject="Deck topping thickness at L2 corridor",
              question="Is the 75 mm topping maintained over the services trench at grid D/L2?",
              raised_by=apex_user.id, contractor_id=steel_contractor.id, due_date=d(-20), status="answered",
              answer="Yes - 75 mm minimum; add T10 @ 200 top mesh over the trench (see S-001 note 2).",
              answered_by=consultant.id, answered_at=utcnow() - timedelta(days=21), activity_id=acts["A7"].id))
    s.add(Submittal(project_id=p.id, number=1, title="Unitised panel system - glass and frame", spec_section="08 44 13",
                    contractor_id=facade_contractor.id, status="under_review", due_date=d(2),
                    required_by=acts["A13"].planned_start, activity_id=acts["A13"].id))


def _seed_network(s, p, ms, proc, design, steel_contractor):
    """Riverside activity network: mixed FS/SS/FF logic with leads and lags, material and
    design constraints, a baseline computed by CPM, and progress to date."""
    from .models import Activity, Baseline, BaselineActivity, Dependency
    from .tracking.cpm import Calendar, CpmActivity, CpmLink, run_cpm

    m1, m2, m3, m4, m5, m6, m7, m8 = ms
    steel2, steel3, glazing, lifts, chillers = proc
    facade_dwg, mep_dwg, steel_conn, finishes_sched = design
    # key, name, milestone, working days, [(pred, type, lag)]
    plan = [
        ("A1", "Site mobilisation", m1, 8, []),
        ("A2", "Bulk excavation & shoring", m1, 30, [("A1", "FS", 0)]),
        ("A3", "PCC & raft reinforcement", m2, 25, [("A2", "FS", -3)]),
        ("A4", "Raft & basement walls", m2, 30, [("A3", "SS", 12)]),
        ("A5", "Erect steel L1-L2", m3, 30, [("A4", "FS", 0)]),
        ("A6", "Erect steel L3-L4", m3, 30, [("A5", "SS", 20)]),
        ("A7", "Metal deck & slabs L1-L4", m3, 25, [("A5", "SS", 15), ("A6", "FF", 5)]),
        ("A8", "Erect steel L5-L8", m4, 50, [("A6", "FS", 0)]),
        ("A9", "Metal deck & slabs L5-L8", m4, 30, [("A8", "SS", 15), ("A8", "FF", 5)]),
        ("A10", "MEP rough-in L1-L4", m5, 45, [("A7", "FS", 0)]),
        ("A11", "MEP rough-in L5-L8", m5, 45, [("A9", "FS", 0), ("A10", "SS", 20)]),
        ("A12", "Facade brackets & anchors", m6, 15, [("A8", "SS", 25)]),
        ("A13", "Unitised panel installation", m6, 60, [("A12", "SS", 8), ("A9", "FS", -10)]),
        ("A14", "Finishes L1-L4", m7, 50, [("A10", "FS", 0)]),
        ("A15", "Finishes L5-L8", m7, 50, [("A11", "FS", 0), ("A13", "FS", -15)]),
        ("A16", "Testing & commissioning", m8, 30, [("A14", "FS", 0), ("A15", "FS", 0)]),
        ("A17", "Handover", m8, 0, [("A16", "FS", 0)]),
    ]
    rows: dict[str, Activity] = {}
    for i, (key, name, m, dur, _) in enumerate(plan):
        a = Activity(project_id=p.id, milestone_id=m.id, code=key, name=name, duration_days=dur, sort_order=i,
                     contractor_id=m.contractor_id)
        s.add(a)
        s.flush()
        rows[key] = a
    for key, _, _, _, preds in plan:
        for pk, typ, lag in preds:
            s.add(Dependency(project_id=p.id, predecessor_id=rows[pk].id, successor_id=rows[key].id, type=typ, lag_days=lag))
    # constraints link to the activity that needs the material / drawing
    for item, key in ((steel2, "A6"), (steel3, "A8"), (glazing, "A13"), (lifts, "A15"), (chillers, "A11")):
        item.activity_id = rows[key].id
        s.add(item)
    for item, key in ((facade_dwg, "A12"), (mep_dwg, "A10"), (steel_conn, "A8"), (finishes_sched, "A14")):
        item.activity_id = rows[key].id
        s.add(item)

    # Baseline = CPM of the plan as at project start (no progress, no constraints)
    cal = Calendar.for_project(p)
    base = run_cpm([CpmActivity(id=a.id, name=a.name, duration=a.duration_days, milestone_id=a.milestone_id) for a in rows.values()],
                   [CpmLink(rows[pk].id, rows[k].id, t, l) for k, _, _, _, pr in plan for pk, t, l in pr], cal, p.start_date)
    bl = Baseline(project_id=p.id, name="Contract baseline", source="manual", is_current=True)
    s.add(bl)
    s.flush()
    for r in base.activities:
        a = s.get(Activity, r.id)
        a.planned_start, a.planned_finish = r.early_start, r.early_finish
        s.add(a)
        s.add(BaselineActivity(baseline_id=bl.id, activity_id=r.id, start=r.early_start, finish=r.early_finish, duration_days=r.duration))
    for m in ms:  # milestone baseline = roll-up of its activities
        mine = [r for r in base.activities if r.milestone_id == m.id]
        m.planned_start, m.planned_end = min(r.early_start for r in mine), max(r.early_finish for r in mine)
        s.add(m)
    p.target_end_date = base.project_finish
    s.add(p)

    # progress to date (substructure done a little late; steel L1-L2 under way, starved of steel)
    actuals = {"A1": (-170, -160, 100), "A2": (-159, -122, 100), "A3": (-125, -96, 100), "A4": (-112, -54, 100), "A5": (-50, None, 60)}
    for key, (st, fin, pct) in actuals.items():
        a = rows[key]
        a.actual_start = d(st)
        a.actual_finish = d(fin) if fin is not None else None
        a.percent_complete = pct
        s.add(a)
    s.flush()
    from .tracking.schedule_import import sync_milestone_progress

    sync_milestone_progress(s, p.id)


if __name__ == "__main__":
    if "--reset" in sys.argv:
        from sqlalchemy import text

        SQLModel.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    from .db import migrate

    migrate()
    print("seeded projects:", seed())
    print("sign in as: owner@demo.local | pm@demo.local | consultant@demo.local | viewer@demo.local | site@apexsteel.example")
