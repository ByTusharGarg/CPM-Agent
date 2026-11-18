"""DB-facing wrapper around the pure tracking engine."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..db import today
from ..models import (
    RFI,
    ChangeOrder,
    Contract,
    ContractLine,
    PayApplication,
    PayAppLine,
    Activity,
    Submittal,
    Baseline,
    BaselineActivity,
    Contractor,
    Dependency,
    CostEntry,
    DailyUpdate,
    DesignDeliverable,
    Milestone,
    ProcurementItem,
    Project,
)
from .engine import Health, compute_health


def load_project_data(s: Session, project_id: int) -> dict[str, Any]:
    project = s.get(Project, project_id)
    if project is None:
        raise KeyError(f"project:{project_id} not found")

    def q(model):
        return list(s.exec(select(model).where(model.project_id == project_id)).all())

    return {
        "project": project,
        "milestones": q(Milestone),
        "procurement": q(ProcurementItem),
        "design": q(DesignDeliverable),
        "costs": q(CostEntry),
        "updates": q(DailyUpdate),
        "contractors": q(Contractor),
        "activities": q(Activity),
        "dependencies": q(Dependency),
        "baseline": current_baseline(s, project_id),
        "rfis": q(RFI),
        "submittals": q(Submittal),
        "contracts": q(Contract),
        "change_orders": q(ChangeOrder),
        "pay_apps": q(PayApplication),
    }


def current_baseline(s: Session, project_id: int) -> dict[int, BaselineActivity]:
    b = s.exec(select(Baseline).where(Baseline.project_id == project_id, Baseline.is_current == True)).first()  # noqa: E712
    if b is None:
        return {}
    return {r.activity_id: r for r in s.exec(select(BaselineActivity).where(BaselineActivity.baseline_id == b.id)).all()}


def project_health(
    s: Session, project_id: int, as_of: date | None = None, overrides: dict | None = None,
    activity_overrides: dict | None = None,
) -> Health:
    d = load_project_data(s, project_id)
    h = compute_health(
        d["project"],
        d["milestones"],
        d["procurement"],
        d["design"],
        d["costs"],
        d["updates"],
        d["contractors"],
        as_of or today(),
        overrides=overrides,
        activities=d["activities"],
        dependencies=d["dependencies"],
        baseline=d["baseline"],
        activity_overrides=activity_overrides,
        rfis=d["rfis"],
        submittals=d["submittals"],
    )
    if d["contracts"]:
        from .commercial import commercial_summary

        cids = [c.id for c in d["contracts"]]
        lines = list(s.exec(select(ContractLine).where(ContractLine.contract_id.in_(cids))).all())
        app_ids = [p.id for p in d["pay_apps"]]
        pay_lines = list(s.exec(select(PayAppLine).where(PayAppLine.pay_app_id.in_(app_ids))).all()) if app_ids else []
        h.commercial = commercial_summary(
            d["project"], d["milestones"], d["activities"], d["contracts"], lines, d["change_orders"], d["pay_apps"],
            pay_lines, d["costs"], h.milestones, h.as_of, {**(d["project"].thresholds or {})},
        )
        h.rule_hits.extend(h.commercial.rule_hits)
        if any(x.severity == "critical" for x in h.rule_hits):
            h.status = "red"
        elif h.status == "green" and any(x.severity == "warning" for x in h.rule_hits):
            h.status = "amber"
    return h
