"""Phase 9: CPM engine maths."""
from datetime import date

from app.tracking.cpm import Calendar, CpmActivity, CpmLink, lookahead, run_cpm

MON = date(2026, 11, 16)  # a Monday
CAL5 = Calendar(range(5))  # Mon-Fri


def A(i, dur, **kw):
    return CpmActivity(id=i, name=f"A{i}", duration=dur, **kw)


def test_calendar_skips_weekends_and_holidays():
    cal = Calendar(range(5), holidays=[date(2026, 11, 18)])
    i = cal.index(MON)
    assert cal.day(i + 1) == date(2026, 11, 17)
    assert cal.day(i + 2) == date(2026, 11, 19)  # Wed is a holiday
    assert cal.index(date(2026, 11, 21)) == cal.index(date(2026, 11, 23))  # Sat -> next Monday


def test_fs_chain_dates_float_and_critical_path():
    acts = [A(1, 5), A(2, 3), A(3, 2), A(4, 1)]
    links = [CpmLink(1, 2), CpmLink(2, 4), CpmLink(1, 3), CpmLink(3, 4)]
    s = run_cpm(acts, links, CAL5, MON)
    r = s.by_id()
    assert r[1].early_start == MON and r[1].early_finish == date(2026, 11, 20)  # 5 working days Mon-Fri
    assert r[2].early_start == date(2026, 11, 23)  # next Monday
    assert r[4].early_start == date(2026, 11, 26)
    assert s.project_finish == date(2026, 11, 26)
    assert r[3].total_float == 1 and r[3].free_float == 1 and not r[3].critical
    assert s.critical_path == [1, 2, 4]
    assert r[2].driver == "activity:1 FS"


def test_relationship_types_and_lags():
    acts = [A(1, 10), A(2, 4), A(3, 4), A(4, 2)]
    links = [CpmLink(1, 2, "SS", 3), CpmLink(1, 3, "FF", 0), CpmLink(1, 4, "FS", -2)]
    r = run_cpm(acts, links, CAL5, MON).by_id()
    i0 = CAL5.index(MON)
    assert CAL5.index(r[2].early_start) == i0 + 3            # SS+3
    assert CAL5.index(r[3].early_finish) == i0 + 10 - 1      # FF: finishes with A1
    assert CAL5.index(r[4].early_start) == i0 + 10 - 2       # FS with 2-day lead


def test_negative_float_against_target():
    acts = [A(1, 10), A(2, 10)]
    s = run_cpm(acts, [CpmLink(1, 2)], CAL5, MON, target_finish=date(2026, 11, 27))  # 10 days available, 20 needed
    r = s.by_id()
    assert r[1].total_float == -10 and r[2].total_float == -10
    assert set(s.critical_path) == {1, 2}


def test_progress_and_data_date():
    # A1 started 5 days ago, 50% of 10 days done -> 5 remaining from data date
    acts = [A(1, 10, actual_start=date(2026, 11, 9), pct=50), A(2, 2), A(3, 3, actual_start=date(2026, 11, 2), actual_finish=date(2026, 11, 4), pct=100)]
    r = run_cpm(acts, [CpmLink(1, 2), CpmLink(3, 1)], CAL5, MON).by_id()
    assert r[1].status == "in_progress" and r[1].remaining == 5
    assert r[1].early_start == date(2026, 11, 9) and r[1].early_finish == date(2026, 11, 20)
    assert r[2].early_start == date(2026, 11, 23)
    assert r[3].status == "done" and r[3].early_finish == date(2026, 11, 4)
    # unstarted work never starts in the past
    late = run_cpm([A(9, 2)], [], CAL5, MON).by_id()[9]
    assert late.early_start == MON


def test_material_constraint_and_what_if():
    steel = (date(2026, 12, 7), "procurement:3")
    acts = [A(1, 5, snet=[steel]), A(2, 5)]
    s = run_cpm(acts, [CpmLink(1, 2)], CAL5, MON)
    assert s.by_id()[1].early_start == date(2026, 12, 7) and s.by_id()[1].driver == "procurement:3"
    faster = run_cpm(acts, [CpmLink(1, 2)], CAL5, MON, overrides={1: {"material_date": "2026-11-23"}, 2: {"duration_factor": 0.6}})
    assert faster.by_id()[1].early_start == date(2026, 11, 23)
    assert faster.project_finish < s.project_finish
    assert faster.by_id()[2].remaining == 3


def test_baseline_variance_and_lookahead():
    acts = [A(1, 5, baseline_start=MON, baseline_finish=date(2026, 11, 20), snet=[(date(2026, 11, 23), "design:1")]), A(2, 5)]
    s = run_cpm(acts, [CpmLink(1, 2)], CAL5, MON)
    assert s.by_id()[1].finish_variance_days == 7
    la = lookahead(s, MON, weeks=1)
    assert [a.id for a in la] == [1]


def test_cycles_do_not_hang():
    s = run_cpm([A(1, 2), A(2, 2)], [CpmLink(1, 2), CpmLink(2, 1)], CAL5, MON)
    assert set(s.cycles) == {1, 2}
