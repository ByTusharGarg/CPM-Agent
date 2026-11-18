"""DB-facing wrapper around the pure tracking engine."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from ..db import today
from ..models import (
    Activity,
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
    return compute_health(
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
    )
