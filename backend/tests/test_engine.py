from datetime import date, timedelta
from types import SimpleNamespace as NS

from app.db import session_scope
from app.harness.tools import Args, Tool
from app.tracking.engine import compute_health, forecast_milestones, planned_pct
from app.tracking.service import project_health

TODAY = date(2026, 11, 18)


def ms(id, start, end, pct=0, deps=(), budget=100, critical=True, actual_start=None, contractor_id=None):
    return NS(id=id, name=f"M{id}", planned_start=TODAY + timedelta(days=start), planned_end=TODAY + timedelta(days=end),
              percent_complete=pct, depends_on=list(deps), budget=budget, is_critical=critical,
              actual_start=actual_start, actual_end=None, contractor_id=contractor_id)


def test_planned_pct_linear():
    m = ms(1, -10, 10)
    assert planned_pct(m, TODAY) == 50
    assert planned_pct(m, TODAY - timedelta(days=20)) == 0
    assert planned_pct(m, TODAY + timedelta(days=20)) == 100


def test_slip_propagates_to_successor_but_respects_planned_overlap():
    a = ms(1, -10, 10, pct=25, actual_start=TODAY - timedelta(days=10))  # 2.5%/day -> 30 more days
    b = ms(2, 5, 40, deps=[1])  # planned overlap with A
    f = {x.id: x for x in forecast_milestones([a, b], [], [], TODAY)}
    assert f[1].slip_days == 20
    assert f[2].slip_days == 20 and f[2].drivers == ["milestone:1"]

    on_time = ms(1, -10, 10, pct=50, actual_start=TODAY - timedelta(days=10))
    f = {x.id: x for x in forecast_milestones([on_time, b], [], [], TODAY)}
    assert f[2].slip_days == 0  # overlap in the baseline is not treated as a delay


def test_material_constraint_drives_forecast():
    a = ms(1, 10, 40)
    late_steel = NS(id=7, milestone_id=1, status="ordered", expected_delivery=TODAY + timedelta(days=25),
                    lead_time_days=30, po_date=None, required_on_site=TODAY + timedelta(days=10))
    f = forecast_milestones([a], [late_steel], [], TODAY)[0]
    assert f.forecast_start == TODAY + timedelta(days=25)
    assert f.slip_days == 15 and f.drivers == ["procurement:7"]
    # what-if: material earlier
    f2 = forecast_milestones([a], [late_steel], [], TODAY, {1: {"material_date": (TODAY + timedelta(days=12)).isoformat()}})[0]
    assert f2.slip_days == 2


def test_seeded_projects(db):
    late, ok = db
    with session_scope() as s:
        h = project_health(s, late)
        keys = {x.rule_key for x in h.rule_hits}
        assert h.status == "red"
        assert h.project_slip_days > 0
        assert {"project_slip", "procurement_late:1", "design_overdue:1", "cost_overrun", "spi_low"} <= keys
        assert "milestone_slip:3" in keys
        assert "milestone_slip:4" not in keys  # pure knock-on, de-duplicated
        assert any(k.startswith("stale_updates:") for k in keys)
        assert h.cpi < 1 and h.spi < 0.9

        g = project_health(s, ok)
        assert g.status == "green" and g.project_slip_days <= 0

        # Riverside has an activity network: the CPM says glazing (procurement:3) drives the finish.
        assert h.schedule is not None
        crit = {a.name for a in h.schedule.activities if a.critical}
        assert "Unitised panel installation" in crit and "Erect steel L3-L4" not in crit
        assert "negative_float" in keys
        # Speeding up steel alone does not move the finish (it is not on the critical path)...
        steel_only = project_health(s, late, overrides={3: {"material_date": "2026-11-25", "productivity_factor": 1.3}})
        assert steel_only.forecast_end == h.forecast_end
        # ...bringing the glazing forward does.
        glazing = project_health(s, late, overrides={6: {"material_date": "2026-12-20"}})
        assert glazing.forecast_end < h.forecast_end


def test_tool_schema_keeps_property_named_title():
    class A(Args):
        title: str
        n: int = 1

    spec = Tool("t", "d", A, lambda c, a: None).spec()
    assert "title" in spec["input_schema"]["properties"]
    assert "title" not in spec["input_schema"]  # pydantic's model title stripped
    assert spec["input_schema"]["additionalProperties"] is False


def test_compute_health_no_costs_no_crash():
    p = NS(id=1, budget=0, currency="USD", thresholds={}, target_end_date=TODAY + timedelta(days=50))
    h = compute_health(p, [ms(1, -5, 50)], [], [], [], [], [], TODAY)
    assert h.cpi is None and h.status in ("green", "amber", "red")
