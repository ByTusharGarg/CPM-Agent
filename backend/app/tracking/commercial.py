"""Commercial engine (pure): commitments, change-order exposure, forecast final
cost, cash-flow S-curve and the pay-application overbilling check.

Every money figure the Commercial agent quotes comes from here.

* Committed      = contract values + approved change orders.
* Exposure       = proposed value of change orders not yet decided (PCO + submitted).
* Uncommitted    = budget of milestones not covered by any contract line.
* Forecast final = committed + exposure + uncommitted.
* Overbilling    = a pay-app line claiming more % complete than the progress
                   recorded on site for the linked activity/milestone (beyond a tolerance).
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .engine import RuleHit

OVERBILL_TOLERANCE_PCT = 5.0
CERTIFY_WITHIN_DAYS = 14
OPEN_CO = ("pco", "submitted")


@dataclass
class ContractPosition:
    id: int
    title: str
    contractor_id: int | None
    original: float
    approved_changes: float
    revised: float
    earned: float  # revised value x recorded progress
    claimed_to_date: float
    certified_to_date: float
    retention_held: float
    overbilled: float


@dataclass
class OverbillItem:
    pay_app_id: int
    pay_app_number: int
    contract_id: int
    line_id: int
    description: str
    claimed_pct: float
    recorded_pct: float
    excess_value: float
    ref: str  # activity:x or milestone:y


@dataclass
class CommercialSummary:
    budget: float
    contracts_original: float
    approved_changes: float
    committed: float
    pending_exposure: float
    uncommitted: float
    forecast_final: float
    variance: float  # forecast_final - budget (+ = over)
    certified_to_date: float
    retention_held: float
    contracts: list[ContractPosition]
    overbilling: list[OverbillItem]
    cashflow: list[dict[str, Any]]
    rule_hits: list[RuleHit] = field(default_factory=list)


def _line_progress(line, act_pct: dict[int, float], ms_pct: dict[int, float]) -> tuple[float | None, str]:
    if line.activity_id and line.activity_id in act_pct:
        return act_pct[line.activity_id], f"activity:{line.activity_id}"
    if line.milestone_id and line.milestone_id in ms_pct:
        return ms_pct[line.milestone_id], f"milestone:{line.milestone_id}"
    return None, ""


def _month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _months(start: date, end: date) -> list[date]:
    out, d = [], _month_end(start)
    while d <= _month_end(end):
        out.append(d)
        d = _month_end(d + timedelta(days=1))
    return out


def _spread(value: float, start: date, end: date, months: list[date]) -> dict[date, float]:
    """Spread value linearly per day between start and end (inclusive) into month buckets."""
    days = max((end - start).days + 1, 1)
    per_day = value / days
    out: dict[date, float] = {}
    d = start
    while d <= end:
        m = _month_end(d)
        nxt = min(_month_end(d), end)
        n = (nxt - d).days + 1
        out[m] = out.get(m, 0.0) + per_day * n
        d = nxt + timedelta(days=1)
    return out


def commercial_summary(project, milestones, activities, contracts, lines, change_orders, pay_apps, pay_lines,
                       costs, milestone_forecasts, as_of: date, thresholds: dict[str, Any]) -> CommercialSummary:
    budget = float(project.budget or 0)
    act_pct = {a.id: float(a.percent_complete or 0) for a in activities}
    ms_pct = {m.id: float(m.percent_complete or 0) for m in milestones}
    lines_by_contract: dict[int, list[Any]] = {}
    for ln in lines:
        lines_by_contract.setdefault(ln.contract_id, []).append(ln)
    approved_by_contract: dict[int | None, float] = {}
    for co in change_orders:
        if co.stage == "approved":
            approved_by_contract[co.contract_id] = approved_by_contract.get(co.contract_id, 0.0) + float(co.approved_amount or 0)
    pay_lines_by_app: dict[int, list[Any]] = {}
    for pl in pay_lines:
        pay_lines_by_app.setdefault(pl.pay_app_id, []).append(pl)

    positions: list[ContractPosition] = []
    overbilling: list[OverbillItem] = []
    for c in contracts:
        cl = lines_by_contract.get(c.id, [])
        original = sum(float(x.value) for x in cl)
        approved = approved_by_contract.get(c.id, 0.0)
        earned = 0.0
        for ln in cl:
            pct, _ = _line_progress(ln, act_pct, ms_pct)
            earned += float(ln.value) * (pct or 0) / 100
        apps = sorted([p for p in pay_apps if p.contract_id == c.id and p.status != "rejected"], key=lambda p: p.number)
        latest = apps[-1] if apps else None
        certified = max((float(p.certified_to_date or 0) for p in apps if p.status in ("certified", "paid")), default=0.0)
        overbilled = 0.0
        if latest is not None:
            for pl in pay_lines_by_app.get(latest.id, []):
                ln = next((x for x in cl if x.id == pl.contract_line_id), None)
                if ln is None:
                    continue
                rec, ref = _line_progress(ln, act_pct, ms_pct)
                pct_used = pl.certified_pct if (latest.status in ("certified", "paid") and pl.certified_pct is not None) else pl.claimed_pct
                if rec is not None and pct_used - rec > OVERBILL_TOLERANCE_PCT:
                    excess = float(ln.value) * (pct_used - rec) / 100
                    overbilled += excess
                    overbilling.append(OverbillItem(latest.id, latest.number, c.id, ln.id, ln.description,
                                                    round(pct_used, 1), round(rec, 1), round(excess, 2), ref))
        positions.append(ContractPosition(
            id=c.id, title=c.title, contractor_id=c.contractor_id, original=round(original, 2), approved_changes=round(approved, 2),
            revised=round(original + approved, 2), earned=round(earned, 2),
            claimed_to_date=round(float(latest.gross_to_date) if latest else 0.0, 2), certified_to_date=round(certified, 2),
            retention_held=round(certified * float(c.retention_pct or 0) / 100, 2), overbilled=round(overbilled, 2),
        ))

    contracts_original = sum(p.original for p in positions)
    approved_total = sum(float(co.approved_amount or 0) for co in change_orders if co.stage == "approved")
    pending = sum(float(co.proposed_amount or 0) for co in change_orders if co.stage in OPEN_CO)
    covered = {ln.milestone_id for ln in lines if ln.milestone_id}
    covered |= {a.milestone_id for a in activities if a.id in {ln.activity_id for ln in lines if ln.activity_id}}
    uncommitted = sum(float(m.budget or 0) for m in milestones if m.id not in covered)
    committed = contracts_original + approved_total
    forecast_final = committed + pending + uncommitted
    certified_total = sum(p.certified_to_date for p in positions)

    cashflow = _cashflow(project, milestones, milestone_forecasts, costs, forecast_final, as_of)
    s = CommercialSummary(
        budget=round(budget, 2), contracts_original=round(contracts_original, 2), approved_changes=round(approved_total, 2),
        committed=round(committed, 2), pending_exposure=round(pending, 2), uncommitted=round(uncommitted, 2),
        forecast_final=round(forecast_final, 2), variance=round(forecast_final - budget, 2),
        certified_to_date=round(certified_total, 2), retention_held=round(sum(p.retention_held for p in positions), 2),
        contracts=positions, overbilling=overbilling, cashflow=cashflow,
    )
    s.rule_hits = _rules(s, change_orders, pay_apps, project, thresholds, as_of)
    return s


def _cashflow(project, milestones, forecasts, costs, forecast_final: float, as_of: date) -> list[dict[str, Any]]:
    """Monthly cumulative planned / actual / forecast spend."""
    fc_by_ms = {f.id: f for f in forecasts}
    end = max([project.target_end_date] + [f.forecast_end for f in forecasts] + [as_of])
    months = _months(project.start_date, end)
    planned: dict[date, float] = {}
    for m in milestones:
        for k, v in _spread(float(m.budget or 0), m.planned_start, m.planned_end, months).items():
            planned[k] = planned.get(k, 0.0) + v
    actual: dict[date, float] = {}
    for c in costs:
        if c.kind in ("actual", "change_order") and c.entry_date <= as_of:
            k = _month_end(c.entry_date)
            actual[k] = actual.get(k, 0.0) + float(c.amount)
    spent = sum(actual.values())
    remaining = max(forecast_final - spent, 0.0)
    # remaining value per milestone, spread over its forecast remaining window
    weights = []
    for m in milestones:
        f = fc_by_ms.get(m.id)
        left = float(m.budget or 0) * (1 - float(m.percent_complete or 0) / 100)
        if left <= 0:
            continue
        start = max(as_of + timedelta(days=1), f.forecast_start if f else m.planned_start)
        finish = max(start, f.forecast_end if f else m.planned_end)
        weights.append((left, start, finish))
    total_left = sum(w for w, *_ in weights) or 1.0
    future: dict[date, float] = {}
    for w, st, fi in weights:
        for k, v in _spread(remaining * w / total_left, st, fi, months).items():
            future[k] = future.get(k, 0.0) + v
    out, cp, ca, cf = [], 0.0, 0.0, 0.0
    for m in months:
        cp += planned.get(m, 0.0)
        if m <= _month_end(as_of):
            ca += actual.get(m, 0.0)
            cf = ca
        cf += future.get(m, 0.0)
        out.append({"month": m.isoformat()[:7], "planned_cum": round(cp, 2),
                    "actual_cum": round(ca, 2) if m <= _month_end(as_of) else None, "forecast_cum": round(cf, 2)})
    return out


def _rules(s: CommercialSummary, change_orders, pay_apps, project, th: dict[str, Any], as_of: date) -> list[RuleHit]:
    hits: list[RuleHit] = []
    budget = s.budget or 1.0
    for o in s.overbilling:
        sev = "critical" if o.excess_value > 0.01 * budget else "warning"
        hits.append(RuleHit("overbilling", f"overbilling:{o.pay_app_id}:{o.line_id}", sev,
                            f"Pay app #{o.pay_app_number} claims {o.claimed_pct:.0f}% on '{o.description}' but site progress is {o.recorded_pct:.0f}%",
                            f"About {o.excess_value:,.0f} {project.currency} claimed ahead of recorded progress ({o.ref}).",
                            [f"payapp:{o.pay_app_id}", o.ref]))
    if s.pending_exposure > 0.03 * budget:
        hits.append(RuleHit("co_exposure", "co_exposure", "warning",
                            f"Undecided change orders worth {s.pending_exposure / budget * 100:.1f}% of budget",
                            f"{sum(1 for c in change_orders if c.stage in OPEN_CO)} change orders pending, total {s.pending_exposure:,.0f} {project.currency}.",
                            [f"change_order:{c.id}" for c in change_orders if c.stage in OPEN_CO][:8]))
    if s.variance > budget * float(th.get("cost_overrun_pct", 5.0)) / 100:
        hits.append(RuleHit("forecast_over_budget", "forecast_over_budget", "critical" if s.variance > 0.1 * budget else "warning",
                            f"Forecast final cost {s.variance / budget * 100:+.1f}% over budget",
                            f"Committed {s.committed:,.0f} + pending changes {s.pending_exposure:,.0f} + uncommitted {s.uncommitted:,.0f}.",
                            [f"project:{project.id}"]))
    for p in pay_apps:
        if p.status == "submitted" and (as_of - p.period_end).days > CERTIFY_WITHIN_DAYS:
            hits.append(RuleHit("payapp_uncertified", f"payapp_uncertified:{p.id}", "warning",
                                f"Pay app #{p.number} waiting {(as_of - p.period_end).days} days for certification",
                                "Late certification risks contractor cash-flow problems and slow-downs.", [f"payapp:{p.id}"]))
    return hits
