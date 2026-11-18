"""Phase 9: MS Project XML + P6 XER import."""
from sqlmodel import select

from app.db import session_scope
from app.models import Activity, Baseline, Dependency, Milestone, Project
from app.tracking.schedule_import import apply_import, parse_msp_xml, parse_xer

MSP = b"""<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <MinutesPerDay>480</MinutesPerDay>
  <Tasks>
    <Task><UID>0</UID><Name>Project summary</Name><OutlineLevel>0</OutlineLevel><Summary>1</Summary></Task>
    <Task><UID>1</UID><Name>Substructure</Name><OutlineLevel>1</OutlineLevel><Summary>1</Summary></Task>
    <Task><UID>2</UID><Name>Excavation</Name><OutlineLevel>2</OutlineLevel><Summary>0</Summary>
      <Start>2026-12-01T08:00:00</Start><Finish>2026-12-10T17:00:00</Finish><Duration>PT64H0M0S</Duration>
      <PercentComplete>25</PercentComplete><ActualStart>2026-12-01T08:00:00</ActualStart></Task>
    <Task><UID>3</UID><Name>Raft</Name><OutlineLevel>2</OutlineLevel><Summary>0</Summary>
      <Start>2026-12-08T08:00:00</Start><Finish>2026-12-20T17:00:00</Finish><Duration>PT80H0M0S</Duration>
      <PredecessorLink><PredecessorUID>2</PredecessorUID><Type>3</Type><LinkLag>14400</LinkLag></PredecessorLink></Task>
    <Task><UID>4</UID><Name>Superstructure</Name><OutlineLevel>1</OutlineLevel><Summary>1</Summary></Task>
    <Task><UID>5</UID><Name>Frame</Name><OutlineLevel>2</OutlineLevel><Summary>0</Summary>
      <Start>2026-12-21T08:00:00</Start><Finish>2027-01-30T17:00:00</Finish><Duration>PT240H0M0S</Duration>
      <PredecessorLink><PredecessorUID>3</PredecessorUID><Type>1</Type><LinkLag>0</LinkLag></PredecessorLink></Task>
    <Task><UID>6</UID><Name>Topping out</Name><OutlineLevel>2</OutlineLevel><Summary>0</Summary><Milestone>1</Milestone>
      <Start>2027-01-30T17:00:00</Start><Finish>2027-01-30T17:00:00</Finish><Duration>PT0H0M0S</Duration>
      <PredecessorLink><PredecessorUID>5</PredecessorUID><Type>0</Type></PredecessorLink></Task>
  </Tasks>
</Project>"""

XER = "\n".join([
    "ERMHDR\t19.12",
    "%T\tCALENDAR", "%F\tclndr_id\tdefault_flag\tday_hr_cnt", "%R\t1\tY\t8",
    "%T\tPROJWBS", "%F\twbs_id\tparent_wbs_id\twbs_name\tproj_node_flag",
    "%R\t100\t\tTower\tY", "%R\t101\t100\tCivil\tN", "%R\t102\t101\tPiling\tN", "%R\t103\t100\tMEP\tN",
    "%T\tTASK", "%F\ttask_id\twbs_id\ttask_code\ttask_name\ttask_type\ttarget_drtn_hr_cnt\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\tphys_complete_pct\tstatus_code",
    "%R\t1\t102\tA100\tPiles\tTT_Task\t80\t2026-12-01 08:00\t2026-12-12 17:00\t2026-12-01 08:00\t2026-12-11 17:00\t100\tTK_Complete",
    "%R\t2\t101\tA110\tPile caps\tTT_Task\t40\t2026-12-14 08:00\t2026-12-19 17:00\t\t\t0\tTK_NotStart",
    "%R\t3\t103\tA200\tMEP first fix\tTT_Task\t120\t2026-12-21 08:00\t2027-01-08 17:00\t\t\t0\tTK_NotStart",
    "%R\t4\t103\tA210\tMEP done\tTT_FinMile\t0\t2027-01-08 17:00\t2027-01-08 17:00\t\t\t0\tTK_NotStart",
    "%T\tTASKPRED", "%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt",
    "%R\t1\t2\t1\tPR_FS\t0", "%R\t2\t3\t2\tPR_SS\t16", "%R\t3\t4\t3\tPR_FF\t0",
    "%E",
]).encode()


def test_parse_msp():
    imp = parse_msp_xml(MSP)
    assert imp.wbs == {"1": "Substructure", "4": "Superstructure"}
    acts = {a.key: a for a in imp.activities}
    assert acts["2"].duration_days == 8 and acts["2"].pct == 25 and acts["2"].wbs_key == "1"
    assert acts["6"].duration_days == 0 and acts["5"].wbs_key == "4"
    assert ("2", "3", "SS", 3) in imp.links  # 14400 tenths of a minute = 24h = 3 days of 8h
    assert ("5", "6", "FF", 0) in imp.links


def test_parse_xer():
    imp = parse_xer(XER)
    assert imp.wbs == {"101": "Civil", "103": "MEP"}  # Piling rolls up into Civil
    acts = {a.key: a for a in imp.activities}
    assert acts["1"].pct == 100 and acts["1"].wbs_key == "101" and acts["1"].duration_days == 10
    assert acts["4"].duration_days == 0
    assert ("2", "3", "SS", 2) in imp.links and ("3", "4", "FF", 0) in imp.links


def test_apply_import_replaces_network_and_baselines(db):
    with session_scope() as s:
        p = s.get(Project, 2)
        r1 = apply_import(s, p, parse_msp_xml(MSP), "MSP v1")
        assert r1["activities"] == 4 and r1["links"] == 3 and r1["milestones"] == 2
        r2 = apply_import(s, p, parse_xer(XER), "P6 v2")
        assert r2["activities"] == 4
        acts = s.exec(select(Activity).where(Activity.project_id == 2)).all()
        assert {a.name for a in acts} == {"Piles", "Pile caps", "MEP first fix", "MEP done"}
        assert len(s.exec(select(Dependency).where(Dependency.project_id == 2)).all()) == 3
        bs = s.exec(select(Baseline).where(Baseline.project_id == 2)).all()
        assert [b.is_current for b in sorted(bs, key=lambda b: b.id)] == [False, True]
        civil = s.exec(select(Milestone).where(Milestone.project_id == 2, Milestone.name == "Civil")).one()
        assert civil.percent_complete == round(10 * 100 / 15, 1)  # duration-weighted

    from app.tracking.service import project_health

    with session_scope() as s:
        h = project_health(s, 2)
        assert h.schedule is not None and len(h.schedule.activities) == 4
        assert h.schedule.critical_path  # a longest path always exists
