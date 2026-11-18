"""Deterministic tracking engine.

All schedule/cost numbers the agents talk about come from here - the LLM never
does the arithmetic. Pure functions over plain objects so it is easy to test.

Method (kept intentionally simple and explainable):
* Planned % of a milestone grows linearly between planned_start and planned_end.
* Earned value EV = sum(budget_i * actual%_i); planned value PV = sum(budget_i * planned%_i);
  actual cost AC = sum of "actual" cost entries. SPI = EV/PV, CPI = EV/AC, EAC = BAC/CPI.
* Forecast dates walk the dependency graph in topological order:
    - finished milestones keep their actual end;
    - in-progress milestones are projected at their observed production rate;
    - not-started milestones start at the latest of: planned start, today,
      predecessor forecast ends, material delivery dates and design approval dates.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

DONE_DESIGN = {"approved"}
DONE_PROCUREMENT = {"delivered"}


@dataclass
class MilestoneForecast:
    id: int
    name: str
    planned_start: date
    planned_end: date
    planned_pct: float
    actual_pct: float
    forecast_start: date
    forecast_end: date
    slip_days: int
    is_critical: bool
    status: str  # done | in_progress | not_started | late_start
    drivers: list[str] = field(default_factory=list)  # why the forecast moved (ref ids)


@dataclass
class RuleHit:
    rule: str
    rule_key: str
    severity: str  # info | warning | critical
    title: str
    details: str
    refs: list[str]


@dataclass
class Health:
    as_of: date
    budget: float
    currency: str
    planned_pct: float
    actual_pct: float
    earned_value: float
    planned_value: float
    actual_cost: float
    committed_cost: float
    spi: float | None
    cpi: float | None
    eac: float
    cost_variance_pct: float
    target_end: date
    forecast_end: date
    project_slip_days: int
    status: str  # green | amber | red
    milestones: list[MilestoneForecast]
    rule_hits: list[RuleHit]
    schedule: Any = None  # CpmSchedule when the project has an activity network
    commercial: Any = None  # CommercialSummary when the project has contracts

    def to_dict(self) -> dict[str, Any]:
        sched, com = self.schedule, self.commercial
        self.schedule = self.commercial = None  # served by /schedule and /commercial, not the health payload
        try:
            d = asdict(self)
        finally:
            self.schedule, self.commercial = sched, com
        if com is not None:
            d["commercial_summary"] = {k: getattr(com, k) for k in (
                "committed", "pending_exposure", "forecast_final", "variance", "certified_to_date")}
            d["commercial_summary"]["overbilling_items"] = len(com.overbilling)
        if sched is not None:
            d["schedule_summary"] = {
                "activities": len(sched.activities),
                "project_finish": sched.project_finish,
                "critical_path": [f"activity:{i}" for i in sched.critical_path],
                "min_float": min((a.total_float for a in sched.activities if a.status != "done"), default=0),
            }
        d["display"] = {
            k: fmt_money(getattr(self, k), self.currency)
            for k in ("budget", "eac", "actual_cost", "committed_cost", "earned_value", "planned_value")
        }
        return _jsonable(d)

    def summary(self) -> dict[str, Any]:
        """Compact version for agent context (inline) - details are a lookup away."""
        return _jsonable(
            {
                "as_of": self.as_of,
                "status": self.status,
                "progress": {"planned_pct": self.planned_pct, "actual_pct": self.actual_pct},
                "spi": self.spi,
                "cpi": self.cpi,
                "budget": fmt_money(self.budget, self.currency),
                "eac": fmt_money(self.eac, self.currency),
                "cost_variance_pct": self.cost_variance_pct,
                "actual_cost": fmt_money(self.actual_cost, self.currency),
                "committed_cost": fmt_money(self.committed_cost, self.currency),
                "target_end": self.target_end,
                "forecast_end": self.forecast_end,
                "project_slip_days": self.project_slip_days,
                "late_milestones": [
                    {"ref": f"milestone:{m.id}", "name": m.name, "slip_days": m.slip_days, "drivers": m.drivers}
                    for m in self.milestones
                    if m.slip_days > 0 and m.status != "done"
                ],
                "rule_hits": [
                    {"rule_key": h.rule_key, "severity": h.severity, "title": h.title} for h in self.rule_hits
                ],
            }
        )


def fmt_money(v: float | None, currency: str) -> str | None:
    """Human-readable money so agents never convert units themselves."""
    if v is None:
        return None
    if currency == "INR":
        if abs(v) >= 1e7:
            return f"₹{v / 1e7:,.2f} Cr"
        if abs(v) >= 1e5:
            return f"₹{v / 1e5:,.2f} L"
        return f"₹{v:,.0f}"
    if abs(v) >= 1e6:
        return f"{currency} {v / 1e6:,.2f}M"
    return f"{currency} {v:,.0f}"


def _jsonable(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float):
        return round(o, 3) if math.isfinite(o) else None
    return o


# --------------------------------------------------------------------------- helpers


def planned_pct(m: Any, as_of: date) -> float:
    if as_of <= m.planned_start:
        return 0.0
    if as_of >= m.planned_end:
        return 100.0
    total = (m.planned_end - m.planned_start).days or 1
    return 100.0 * (as_of - m.planned_start).days / total


def _duration(m: Any) -> int:
    return max((m.planned_end - m.planned_start).days, 1)


def _toposort(milestones: list[Any]) -> list[Any]:
    by_id = {m.id: m for m in milestones}
    seen: set[int] = set()
    out: list[Any] = []

    def visit(m: Any, stack: set[int]) -> None:
        if m.id in seen:
            return
        if m.id in stack:  # cycle - ignore the back edge
            return
        stack.add(m.id)
        for dep in m.depends_on or []:
            if dep in by_id:
                visit(by_id[dep], stack)
        stack.discard(m.id)
        seen.add(m.id)
        out.append(m)

    for m in sorted(milestones, key=lambda x: (x.planned_start, x.id)):
        visit(m, set())
    return out


# --------------------------------------------------------------------------- forecasting


def forecast_milestones(
    milestones: list[Any],
    procurement: Iterable[Any],
    design: Iterable[Any],
    as_of: date,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> list[MilestoneForecast]:
    """Forecast every milestone. `overrides` lets the Planner run what-if scenarios:
    {milestone_id: {"duration_factor": 0.8, "start_shift_days": -5, "productivity_factor": 1.25,
                    "material_date": "2026-12-01", "design_date": "..."}}"""
    overrides = overrides or {}
    proc_by_ms: dict[int, list[Any]] = {}
    for p in procurement:
        if p.milestone_id:
            proc_by_ms.setdefault(p.milestone_id, []).append(p)
    des_by_ms: dict[int, list[Any]] = {}
    for d in design:
        if d.milestone_id:
            des_by_ms.setdefault(d.milestone_id, []).append(d)

    results: dict[int, MilestoneForecast] = {}
    for m in _toposort(milestones):
        ov = overrides.get(m.id, {})
        dur = max(int(round(_duration(m) * float(ov.get("duration_factor", 1.0)))), 1)
        drivers: list[str] = []
        actual = float(m.percent_complete or 0)
        p_pct = planned_pct(m, as_of)

        # Constraints on the start date
        start_candidates: list[tuple[date, str]] = [
            (m.planned_start + timedelta(days=int(ov.get("start_shift_days", 0))), "")
        ]
        for dep in m.depends_on or []:
            if dep in results and results[dep].slip_days > 0:
                # Planned overlaps (start-to-start lags) are part of the baseline, so a
                # predecessor pushes its successor by its *slip*, not by a strict finish-to-start.
                start_candidates.append(
                    (m.planned_start + timedelta(days=results[dep].slip_days), f"milestone:{dep}")
                )
        for p in proc_by_ms.get(m.id, []):
            if p.status in DONE_PROCUREMENT:
                continue
            arrival = _material_arrival(p, as_of)
            if "material_date" in ov:
                arrival = min(arrival, date.fromisoformat(ov["material_date"]))
            start_candidates.append((arrival, f"procurement:{p.id}"))
        for d in des_by_ms.get(m.id, []):
            if d.status in DONE_DESIGN:
                continue
            ready = max(d.due_date, as_of + timedelta(days=7))  # approval cycle after today
            if "design_date" in ov:
                ready = date.fromisoformat(ov["design_date"])
            start_candidates.append((ready, f"design:{d.id}"))

        if actual >= 100:
            f_start = m.actual_start or m.planned_start
            f_end = m.actual_end or m.planned_end
            status = "done"
        elif actual > 0 or m.actual_start:
            f_start = m.actual_start or m.planned_start
            elapsed = max((as_of - f_start).days, 1)
            rate = actual / elapsed if actual > 0 else 0  # % per day observed
            rate *= float(ov.get("productivity_factor", 1.0))
            if ov.get("duration_factor") and rate:
                rate /= float(ov["duration_factor"])
            # Remaining work may be blocked by an outstanding material/design constraint.
            blockers = [c for c in start_candidates[1:] if c[1].startswith(("procurement", "design")) and c[0] > as_of]
            resume = max([as_of] + [c[0] for c in blockers])
            if blockers:
                # The observed rate is depressed by the blockage itself; once the
                # constraint clears assume the planned production rate.
                rate = max(rate, (100.0 / dur) * float(ov.get("productivity_factor", 1.0)))
            if rate <= 0:
                remaining_days = dur
            else:
                remaining_days = math.ceil((100 - actual) / rate)
            if blockers:
                drivers.extend(c[1] for c in blockers if c[0] == resume)
            f_end = resume + timedelta(days=remaining_days)
            status = "in_progress"
            if rate > 0 and f_end > m.planned_end and not blockers:
                drivers.append("production_rate")
        else:
            best = max(start_candidates, key=lambda c: c[0])
            f_start = max(best[0], as_of if m.planned_start < as_of else best[0])
            if best[1] and best[0] > m.planned_start:
                drivers.append(best[1])
            elif f_start > m.planned_start:
                drivers.append("not_started_on_time")
            f_end = f_start + timedelta(days=dur)
            status = "late_start" if m.planned_start < as_of else "not_started"

        slip = (f_end - m.planned_end).days
        results[m.id] = MilestoneForecast(
            id=m.id,
            name=m.name,
            planned_start=m.planned_start,
            planned_end=m.planned_end,
            planned_pct=round(p_pct, 1),
            actual_pct=round(actual, 1),
            forecast_start=f_start,
            forecast_end=f_end,
            slip_days=slip,
            is_critical=bool(m.is_critical),
            status=status,
            drivers=drivers,
        )
    return [results[m.id] for m in sorted(milestones, key=lambda x: (x.planned_start, x.id))]


def _material_arrival(p: Any, as_of: date) -> date:
    if p.expected_delivery:
        return max(p.expected_delivery, as_of)
    if p.status == "not_ordered":
        return as_of + timedelta(days=int(p.lead_time_days or 0))
    base = p.po_date or as_of
    return max(base + timedelta(days=int(p.lead_time_days or 0)), as_of)


# --------------------------------------------------------------------------- health


def compute_health(
    project: Any,
    milestones: list[Any],
    procurement: list[Any],
    design: list[Any],
    costs: list[Any],
    updates: list[Any],
    contractors: list[Any],
    as_of: date,
    overrides: dict[int, dict[str, Any]] | None = None,
    activities: list[Any] | None = None,
    dependencies: list[Any] | None = None,
    baseline: dict[int, Any] | None = None,
    activity_overrides: dict[int, dict[str, Any]] | None = None,
    rfis: list[Any] | None = None,
    submittals: list[Any] | None = None,
) -> Health:
    th = {**(project.thresholds or {})}
    bac = float(project.budget or 0) or sum(float(m.budget or 0) for m in milestones)
    weights = {m.id: float(m.budget or 0) for m in milestones}
    if sum(weights.values()) == 0:
        weights = {m.id: 1.0 for m in milestones}
    wsum = sum(weights.values()) or 1.0
    ms_budget_total = sum(float(m.budget or 0) for m in milestones) or bac

    planned = sum(weights[m.id] * planned_pct(m, as_of) for m in milestones) / wsum
    actual = sum(weights[m.id] * float(m.percent_complete or 0) for m in milestones) / wsum
    ev = ms_budget_total * actual / 100
    pv = ms_budget_total * planned / 100
    ac = sum(float(c.amount) for c in costs if c.kind in ("actual", "change_order") and c.entry_date <= as_of)
    committed = sum(float(c.amount) for c in costs if c.kind == "committed")
    spi = ev / pv if pv > 0 else None
    cpi = ev / ac if ac > 0 else None
    eac = bac / cpi if cpi else bac
    cv_pct = (eac - bac) / bac * 100 if bac else 0.0

    schedule = None
    if activities:
        from .cpm import Calendar, run_cpm

        cpm_acts, cpm_links = build_network(activities, dependencies or [], procurement, design, baseline or {}, as_of,
                                            submittals or [])
        act_ov = expand_overrides(activities, overrides or {}, activity_overrides or {})
        schedule = run_cpm(cpm_acts, cpm_links, Calendar.for_project(project), as_of, project.target_end_date, act_ov)
        forecasts = milestones_from_schedule(milestones, schedule, as_of)
    else:
        forecasts = forecast_milestones(milestones, procurement, design, as_of, overrides)
    forecast_end = max(f.forecast_end for f in forecasts) if forecasts else project.target_end_date
    if schedule is not None:
        forecast_end = max(forecast_end, schedule.project_finish)
    slip = (forecast_end - project.target_end_date).days

    hits = evaluate_rules(project, th, forecasts, procurement, design, updates, contractors, milestones, spi, cpi, cv_pct, slip, as_of)
    if schedule is not None:
        hits.extend(schedule_rules(schedule, th))
    hits.extend(document_rules(rfis or [], submittals or [], schedule, as_of))
    status = "green"
    if any(h.severity == "critical" for h in hits):
        status = "red"
    elif any(h.severity == "warning" for h in hits):
        status = "amber"

    return Health(
        as_of=as_of,
        budget=bac,
        currency=project.currency,
        planned_pct=round(planned, 1),
        actual_pct=round(actual, 1),
        earned_value=round(ev, 2),
        planned_value=round(pv, 2),
        actual_cost=round(ac, 2),
        committed_cost=round(committed, 2),
        spi=round(spi, 3) if spi is not None else None,
        cpi=round(cpi, 3) if cpi is not None else None,
        eac=round(eac, 2),
        cost_variance_pct=round(cv_pct, 2),
        target_end=project.target_end_date,
        forecast_end=forecast_end,
        project_slip_days=slip,
        status=status,
        milestones=forecasts,
        rule_hits=hits,
        schedule=schedule,
    )


def evaluate_rules(
    project: Any,
    th: dict[str, Any],
    forecasts: list[MilestoneForecast],
    procurement: list[Any],
    design: list[Any],
    updates: list[Any],
    contractors: list[Any],
    milestones: list[Any],
    spi: float | None,
    cpi: float | None,
    cv_pct: float,
    project_slip: int,
    as_of: date,
) -> list[RuleHit]:
    hits: list[RuleHit] = []
    ms_slip = int(th.get("milestone_slip_days", 7))

    if project_slip > int(th.get("project_slip_days", 0)):
        hits.append(
            RuleHit(
                "project_slip",
                "project_slip",
                "critical" if project_slip > 2 * max(ms_slip, 7) else "warning",
                f"Project completion forecast {project_slip} days late",
                f"Target completion {project.target_end_date}, forecast "
                f"{project.target_end_date + timedelta(days=project_slip)}.",
                ["project:%d" % project.id],
            )
        )

    for f in forecasts:
        if f.status == "done" or f.slip_days <= ms_slip:
            continue
        if f.drivers and all(d.startswith("milestone:") for d in f.drivers):
            continue  # pure knock-on from a predecessor: covered by that milestone + project_slip
        sev = "critical" if f.is_critical and f.slip_days > 2 * ms_slip else "warning"
        hits.append(
            RuleHit(
                "milestone_slip",
                f"milestone_slip:{f.id}",
                sev,
                f"{f.name} forecast {f.slip_days} days late",
                f"Planned end {f.planned_end}, forecast end {f.forecast_end}. "
                f"Planned {f.planned_pct}% vs actual {f.actual_pct}%. Drivers: {', '.join(f.drivers) or 'n/a'}.",
                [f"milestone:{f.id}", *[d for d in f.drivers if ":" in d]],
            )
        )

    if spi is not None and spi < float(th.get("spi_min", 0.9)):
        hits.append(
            RuleHit(
                "spi_low",
                "spi_low",
                "critical" if spi < float(th.get("spi_min", 0.9)) - 0.1 else "warning",
                f"Schedule performance index {spi:.2f} below {th.get('spi_min', 0.9)}",
                "Less work earned than planned to date.",
                [f"project:{project.id}"],
            )
        )

    if cv_pct > float(th.get("cost_overrun_pct", 5.0)) or (cpi is not None and cpi < float(th.get("cpi_min", 0.95))):
        hits.append(
            RuleHit(
                "cost_overrun",
                "cost_overrun",
                "critical" if cv_pct > 2 * float(th.get("cost_overrun_pct", 5.0)) else "warning",
                f"Cost forecast {cv_pct:+.1f}% vs budget (CPI {cpi:.2f})" if cpi else f"Cost forecast {cv_pct:+.1f}% vs budget",
                "Estimate at completion exceeds budget threshold.",
                [f"project:{project.id}"],
            )
        )

    for p in procurement:
        if p.status in DONE_PROCUREMENT:
            continue
        arrival = _material_arrival(p, as_of)
        if arrival > p.required_on_site:
            late = (arrival - p.required_on_site).days
            hits.append(
                RuleHit(
                    "procurement_late",
                    f"procurement_late:{p.id}",
                    "critical" if late > 14 else "warning",
                    f"{p.item} arriving {late} days after required-on-site date",
                    f"Required on site {p.required_on_site}, expected {arrival} (status {p.status}, vendor {p.vendor or 'n/a'}).",
                    [f"procurement:{p.id}"] + ([f"milestone:{p.milestone_id}"] if p.milestone_id else []),
                )
            )

    for d in design:
        if d.status in DONE_DESIGN or d.due_date >= as_of:
            continue
        overdue = (as_of - d.due_date).days
        hits.append(
            RuleHit(
                "design_overdue",
                f"design_overdue:{d.id}",
                "warning" if overdue <= 14 else "critical",
                f"Design deliverable '{d.title}' overdue by {overdue} days",
                f"Due {d.due_date}, status {d.status}.",
                [f"design:{d.id}"] + ([f"milestone:{d.milestone_id}"] if d.milestone_id else []),
            )
        )

    # Contractors with active work who have gone quiet.
    stale_h = int(th.get("stale_update_hours", 48))
    active_contractors = {
        m.contractor_id
        for m in milestones
        if m.contractor_id
        and float(m.percent_complete or 0) < 100
        and (float(m.percent_complete or 0) > 0 or m.planned_start <= as_of)
    }
    last_by_contractor: dict[int, date] = {}
    for u in updates:
        if u.contractor_id not in last_by_contractor or u.report_date > last_by_contractor[u.contractor_id]:
            last_by_contractor[u.contractor_id] = u.report_date
    for c in contractors:
        if c.id not in active_contractors:
            continue
        last = last_by_contractor.get(c.id)
        silent_days = (as_of - last).days if last else None
        if last is None or silent_days * 24 > stale_h:
            hits.append(
                RuleHit(
                    "stale_updates",
                    f"stale_updates:{c.id}",
                    "info" if (silent_days or 99) < 5 else "warning",
                    f"No daily update from {c.name} for {silent_days if silent_days is not None else 'any'} days",
                    f"Last update: {last or 'never'}.",
                    [f"contractor:{c.id}"],
                )
            )
    return hits


# --------------------------------------------------------------------------- activity network (CPM)


def build_network(activities, dependencies, procurement, design, baseline: dict[int, Any], as_of: date, submittals=()):
    """Turn DB rows into CPM inputs. Material deliveries and design approvals become
    start-no-earlier-than constraints on the activity they are linked to (or, if only
    linked to a milestone, on the first activities of that milestone)."""
    from .cpm import CpmActivity, CpmLink

    ms_of = {a.id: a.milestone_id for a in activities}
    by_ms: dict[int, list[Any]] = {}
    for a in activities:
        if a.milestone_id:
            by_ms.setdefault(a.milestone_id, []).append(a)
    has_pred_in_ms = {d.successor_id for d in dependencies
                      if ms_of.get(d.predecessor_id) and ms_of.get(d.predecessor_id) == ms_of.get(d.successor_id)}
    snet: dict[int, list[tuple[date, str]]] = {a.id: [] for a in activities}

    def attach(item, when: date, ref: str):
        if getattr(item, "activity_id", None) and item.activity_id in snet:
            snet[item.activity_id].append((when, ref))
        elif item.milestone_id:
            for a in by_ms.get(item.milestone_id, []):
                if a.id not in has_pred_in_ms:
                    snet[a.id].append((when, ref))

    for p in procurement:
        if p.status not in DONE_PROCUREMENT:
            attach(p, _material_arrival(p, as_of), f"procurement:{p.id}")
    for d in design:
        if d.status not in DONE_DESIGN:
            attach(d, max(d.due_date, as_of + timedelta(days=7)), f"design:{d.id}")
    for x in submittals:  # work that needs an approved submittal cannot start before approval
        if x.activity_id in snet and x.status not in ("approved", "approved_as_noted", "rejected"):
            ready = as_of + timedelta(days=10) if x.status == "revise_resubmit" else max(x.due_date, as_of + timedelta(days=1))
            snet[x.activity_id].append((ready, f"submittal:{x.id}"))

    acts = []
    for a in sorted(activities, key=lambda x: (x.sort_order, x.id)):
        b = baseline.get(a.id)
        acts.append(CpmActivity(
            id=a.id, name=a.name, duration=int(a.duration_days or 0), milestone_id=a.milestone_id,
            pct=float(a.percent_complete or 0), actual_start=a.actual_start, actual_finish=a.actual_finish,
            baseline_start=(b.start if b else a.planned_start), baseline_finish=(b.finish if b else a.planned_finish),
            snet=snet[a.id],
        ))
    links = [CpmLink(d.predecessor_id, d.successor_id, d.type, int(d.lag_days or 0)) for d in dependencies]
    return acts, links


def expand_overrides(activities, ms_overrides: dict[int, dict[str, Any]], act_overrides: dict[int, dict[str, Any]]):
    """Milestone-level what-ifs apply to every activity of that milestone; activity-level ones win."""
    out: dict[int, dict[str, Any]] = {}
    for a in activities:
        if a.milestone_id in ms_overrides:
            out[a.id] = dict(ms_overrides[a.milestone_id])
    for aid, ov in act_overrides.items():
        out.setdefault(aid, {}).update(ov)
    return out


def milestones_from_schedule(milestones, schedule, as_of: date) -> list[MilestoneForecast]:
    """Milestones as WBS roll-ups of their activities."""
    by_id = schedule.by_id()
    acts_by_ms: dict[int, list[Any]] = {}
    for r in schedule.activities:
        if r.milestone_id:
            acts_by_ms.setdefault(r.milestone_id, []).append(r)
    out = []
    for m in sorted(milestones, key=lambda x: (x.planned_start, x.id)):
        rs = acts_by_ms.get(m.id, [])
        if not rs:
            continue
        f_start = min(r.early_start for r in rs)
        f_end = max(r.early_finish for r in rs)
        weight = sum(max(r.duration, 1) for r in rs)
        actual = sum(max(r.duration, 1) * r.pct for r in rs) / weight
        last = max(rs, key=lambda r: r.early_finish)
        drivers = [_root_driver(last, by_id, m.id)] if last.driver and last.driver not in ("data_date", "actual") else []
        drivers = [d for d in drivers if d]
        status = "done" if all(r.status == "done" for r in rs) else (
            "in_progress" if any(r.status != "not_started" for r in rs) else ("late_start" if m.planned_start < as_of else "not_started"))
        out.append(MilestoneForecast(
            id=m.id, name=m.name, planned_start=m.planned_start, planned_end=m.planned_end,
            planned_pct=round(planned_pct(m, as_of), 1), actual_pct=round(actual, 1),
            forecast_start=f_start, forecast_end=f_end, slip_days=(f_end - m.planned_end).days,
            is_critical=any(r.critical for r in rs), status=status, drivers=drivers,
        ))
    return out


def _root_driver(r, by_id: dict[int, Any], milestone_id: int) -> str:
    """Follow driving logic back inside this milestone until it leaves it. Returns
    'milestone:<other>' (knock-on from a predecessor milestone), 'procurement:x',
    'design:x', or 'activity:x' (own production delay)."""
    seen: set[int] = set()
    cur = r
    while cur.id not in seen:
        seen.add(cur.id)
        drv = (cur.driver or "").split(" ")[0]
        if not drv.startswith("activity:"):
            return drv if drv not in ("data_date", "actual") else f"activity:{cur.id}"
        pred = by_id.get(int(drv.split(":")[1]))
        if pred is None:
            return drv
        if pred.milestone_id != milestone_id:
            return f"milestone:{pred.milestone_id}" if pred.milestone_id else drv
        cur = pred
    return f"activity:{cur.id}"


def schedule_rules(schedule, th: dict[str, Any]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    neg = [a for a in schedule.activities if a.status != "done" and a.total_float < 0]
    if neg:
        worst = min(a.total_float for a in neg)
        path = [a for a in schedule.activities if a.id in schedule.critical_path][:6]
        hits.append(RuleHit(
            "negative_float", "negative_float",
            "critical" if worst < -2 * int(th.get("milestone_slip_days", 7)) else "warning",
            f"Critical path has {worst} working days of float",
            "Longest path: " + " -> ".join(f"{a.name} (activity:{a.id})" for a in path),
            [f"activity:{a.id}" for a in path],
        ))
    if schedule.cycles:
        hits.append(RuleHit("logic_loop", "logic_loop", "warning", f"Schedule logic loop between {len(schedule.cycles)} activities",
                            "Fix the dependencies; looping links are ignored in the forecast.",
                            [f"activity:{i}" for i in schedule.cycles[:10]]))
    return hits


def document_rules(rfis, submittals, schedule, as_of: date) -> list[RuleHit]:
    hits: list[RuleHit] = []
    acts = schedule.by_id() if schedule is not None else {}
    for r in rfis:
        if r.status != "open":
            continue
        a = acts.get(r.activity_id) if r.activity_id else None
        if r.due_date < as_of:
            over = (as_of - r.due_date).days
            hits.append(RuleHit("rfi_overdue", f"rfi_overdue:{r.id}", "critical" if (r.time_impact and over > 7) else "warning",
                                f"RFI #{r.number} '{r.subject}' unanswered {over} days past due",
                                f"Ball in court: {r.ball_in_court}. Due {r.due_date}.",
                                [f"rfi:{r.id}"] + ([f"activity:{r.activity_id}"] if r.activity_id else [])))
        if a is not None and a.status != "done" and (a.critical or (a.early_start - as_of).days <= 14):
            hits.append(RuleHit("rfi_blocks_work", f"rfi_blocks_work:{r.id}", "critical" if a.critical else "warning",
                                f"Open RFI #{r.number} affects {'critical ' if a.critical else ''}activity '{a.name}'",
                                f"Activity starts {a.early_start} (float {a.total_float}d) while RFI #{r.number} is still open.",
                                [f"rfi:{r.id}", f"activity:{a.id}"]))
    for x in submittals:
        if x.status in ("submitted", "under_review") and x.due_date < as_of:
            hits.append(RuleHit("submittal_overdue", f"submittal_overdue:{x.id}", "warning",
                                f"Submittal #{x.number} '{x.title}' review {(as_of - x.due_date).days} days overdue",
                                f"Status {x.status}, review was due {x.due_date}.", [f"submittal:{x.id}"]))
        a = acts.get(x.activity_id) if x.activity_id else None
        if a is not None and x.status not in ("approved", "approved_as_noted") and a.driver == f"submittal:{x.id}" and a.status != "done":
            hits.append(RuleHit("submittal_drives_schedule", f"submittal_drives_schedule:{x.id}", "critical" if a.critical else "warning",
                                f"Submittal #{x.number} approval is holding up '{a.name}'",
                                f"'{a.name}' cannot start before approval (status {x.status}); float {a.total_float}d.",
                                [f"submittal:{x.id}", f"activity:{a.id}"]))
    return hits
