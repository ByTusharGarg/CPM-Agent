"""Critical Path Method on a working-day calendar (pure, deterministic).

Conventions (as in P6):
* Time is measured in working-day indices. An activity occupies [ES, EF) with
  EF = ES + duration; its finish *date* is the last working day, i.e. day(EF - 1).
  Zero-duration activities are milestone events at day(ES).
* Relationship constraints on the successor (lag in working days, may be negative):
      FS: ES_s >= EF_p + lag          SS: ES_s >= ES_p + lag
      FF: EF_s >= EF_p + lag          SF: EF_s >= ES_p + lag
* Data date (as_of): unstarted work cannot start before it; started work keeps
  its actual start and finishes after its remaining duration from the data
  date; finished work keeps its actuals.
* Start-no-earlier-than constraints come from material deliveries and design
  approvals linked to an activity.
* Total float is measured against the project target finish, so negative float
  means "this path finishes late". The critical path is the longest path
  (activities with the minimum total float).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable


class Calendar:
    def __init__(self, work_days: Iterable[int] = (0, 1, 2, 3, 4, 5), holidays: Iterable[date] = (), anchor: date | None = None):
        self.work_days = frozenset(work_days) or frozenset(range(7))
        self.holidays = frozenset(holidays)
        self.anchor = anchor or date(2000, 1, 3)
        self._days: list[date] = []
        self._index: dict[date, int] = {}
        self._extend_to(self.anchor + timedelta(days=366 * 30))

    @classmethod
    def for_project(cls, project: Any) -> "Calendar":
        wd = [int(x) for x in str(getattr(project, "work_days", "") or "0,1,2,3,4,5").split(",") if x.strip().isdigit()]
        hol = [date.fromisoformat(h) for h in (getattr(project, "holidays", None) or [])]
        return cls(wd, hol)

    def _extend_to(self, until: date) -> None:
        d = self._days[-1] + timedelta(days=1) if self._days else self.anchor
        while d <= until:
            if d.weekday() in self.work_days and d not in self.holidays:
                self._index[d] = len(self._days)
                self._days.append(d)
            d += timedelta(days=1)

    def is_workday(self, d: date) -> bool:
        return d.weekday() in self.work_days and d not in self.holidays

    def index(self, d: date) -> int:
        """Index of d, or of the next working day if d is not one."""
        while d not in self._index:
            if d > self._days[-1]:
                self._extend_to(d + timedelta(days=366))
                continue
            d += timedelta(days=1)
        return self._index[d]

    def day(self, i: int) -> date:
        if i >= len(self._days):
            self._extend_to(self._days[-1] + timedelta(days=(i - len(self._days) + 400) * 2))
        return self._days[max(i, 0)]

    def finish_index(self, d: date) -> int:
        """EF index for an activity that finishes (inclusive) on date d."""
        return self.index(d) + 1 if self.is_workday(d) else self.index(d)


@dataclass
class CpmActivity:
    id: int
    name: str
    duration: int
    milestone_id: int | None = None
    pct: float = 0.0
    actual_start: date | None = None
    actual_finish: date | None = None
    baseline_start: date | None = None
    baseline_finish: date | None = None
    snet: list[tuple[date, str]] = field(default_factory=list)  # (no-earlier-than date, ref)


@dataclass
class CpmLink:
    pred: int
    succ: int
    type: str = "FS"
    lag: int = 0


@dataclass
class CpmResult:
    id: int
    name: str
    milestone_id: int | None
    duration: int
    remaining: int
    status: str  # done | in_progress | not_started
    early_start: date
    early_finish: date
    late_start: date
    late_finish: date
    total_float: int
    free_float: int
    critical: bool
    baseline_start: date | None
    baseline_finish: date | None
    finish_variance_days: int | None  # calendar days vs baseline finish (+ = late)
    driver: str = ""  # what set the early start: "activity:12 FS", "procurement:3", "data_date", "actual"
    pct: float = 0.0


@dataclass
class CpmSchedule:
    activities: list[CpmResult]
    project_finish: date
    target_finish: date | None
    critical_path: list[int]
    cycles: list[int]

    def by_id(self) -> dict[int, CpmResult]:
        return {a.id: a for a in self.activities}


def _topo(ids: list[int], links: list[CpmLink]) -> tuple[list[int], list[int]]:
    succs: dict[int, list[int]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for l in links:
        if l.pred in succs and l.succ in indeg:
            succs[l.pred].append(l.succ)
            indeg[l.succ] += 1
    order = [i for i in ids if indeg[i] == 0]
    k = 0
    while k < len(order):
        for s in succs[order[k]]:
            indeg[s] -= 1
            if indeg[s] == 0:
                order.append(s)
        k += 1
    cycles = [i for i in ids if indeg[i] > 0]
    return order + cycles, cycles


def run_cpm(
    acts: list[CpmActivity],
    links: list[CpmLink],
    cal: Calendar,
    as_of: date,
    target_finish: date | None = None,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> CpmSchedule:
    overrides = overrides or {}
    by = {a.id: a for a in acts}
    links = [l for l in links if l.pred in by and l.succ in by and l.pred != l.succ]
    order, cycles = _topo([a.id for a in acts], links)
    incoming: dict[int, list[CpmLink]] = {a.id: [] for a in acts}
    outgoing: dict[int, list[CpmLink]] = {a.id: [] for a in acts}
    for l in links:
        incoming[l.succ].append(l)
        outgoing[l.pred].append(l)
    dd = cal.index(as_of)

    es: dict[int, int] = {}
    ef: dict[int, int] = {}
    rem: dict[int, int] = {}
    driver: dict[int, str] = {}
    status: dict[int, str] = {}

    for aid in order:
        a, ov = by[aid], overrides.get(aid, {})
        factor = float(ov.get("duration_factor", 1.0)) / float(ov.get("productivity_factor", 1.0))
        if a.actual_finish or a.pct >= 100:
            s_i = cal.index(a.actual_start) if a.actual_start else cal.finish_index(a.actual_finish) - a.duration
            f_i = cal.finish_index(a.actual_finish) if a.actual_finish else s_i + a.duration
            es[aid], ef[aid], rem[aid], driver[aid], status[aid] = s_i, max(f_i, s_i), 0, "actual", "done"
            continue
        if a.actual_start or a.pct > 0:
            s_i = cal.index(a.actual_start) if a.actual_start else dd
            remaining = max(math.ceil(a.duration * (1 - a.pct / 100) * factor), 1 if a.duration else 0)
            resume, why = dd, "data_date"
            for d, ref in _snets(a, ov):  # outstanding material/design still blocks the rest of the work
                if cal.index(d) > resume:
                    resume, why = cal.index(d), ref
            es[aid], ef[aid], rem[aid], driver[aid], status[aid] = s_i, max(resume + remaining, s_i + 1 if a.duration else s_i), remaining, why, "in_progress"
            continue
        dur = max(math.ceil(a.duration * factor), 0) if a.duration else 0
        start, why = dd, "data_date"
        shift = int(ov.get("start_shift_days", 0))
        for l in incoming[aid]:
            if l.pred not in es:  # predecessor is part of a logic loop - ignore that link
                continue
            lag = l.lag + shift
            if l.type == "SS":
                cand = es[l.pred] + lag
            elif l.type == "FF":
                cand = ef[l.pred] + lag - dur
            elif l.type == "SF":
                cand = es[l.pred] + lag - dur
            else:  # FS
                cand = ef[l.pred] + lag
            if cand > start or (cand == start and why == "data_date"):
                start, why = cand, f"activity:{l.pred} {l.type}"
        for d, ref in _snets(a, ov):
            if cal.index(d) > start:
                start, why = cal.index(d), ref
        es[aid], ef[aid], rem[aid], driver[aid], status[aid] = start, start + dur, dur, why, "not_started"

    project_ef = max(ef.values()) if ef else dd
    lf_init = max(cal.finish_index(target_finish), 0) if target_finish else project_ef
    ls: dict[int, int] = {}
    lf: dict[int, int] = {}
    for aid in reversed(order):
        dur = ef[aid] - es[aid]
        finish = lf_init
        for l in outgoing[aid]:
            s_ = l.succ
            if s_ not in ls:
                continue
            if l.type == "SS":
                cand = ls[s_] - l.lag + dur
            elif l.type == "FF":
                cand = lf[s_] - l.lag
            elif l.type == "SF":
                cand = lf[s_] - l.lag + dur
            else:
                cand = ls[s_] - l.lag
            finish = min(finish, cand)
        if status[aid] == "done":
            finish = max(finish, ef[aid])  # completed work has no float to lose
        lf[aid], ls[aid] = finish, finish - dur

    tf = {aid: (ls[aid] - es[aid]) if status[aid] == "not_started" else (lf[aid] - ef[aid]) for aid in order}
    ff: dict[int, int] = {}
    for aid in order:
        slack = [lf_init - ef[aid]]
        for l in outgoing[aid]:
            s_ = l.succ
            if l.type == "SS":
                slack.append(es[s_] - l.lag - es[aid])
            elif l.type == "FF":
                slack.append(ef[s_] - l.lag - ef[aid])
            elif l.type == "SF":
                slack.append(ef[s_] - l.lag - es[aid])
            else:
                slack.append(es[s_] - l.lag - ef[aid])
        ff[aid] = max(min(slack), 0) if status[aid] != "done" else 0

    open_ids = [a for a in order if status[a] != "done"]
    min_tf = min((tf[a] for a in open_ids), default=0)
    critical = {a for a in open_ids if tf[a] <= min(min_tf, 0) or tf[a] == min_tf}

    def fdate(i_es: int, i_ef: int) -> date:
        return cal.day(i_ef - 1) if i_ef > i_es else cal.day(i_es)

    results: list[CpmResult] = []
    for a in acts:
        aid = a.id
        early_finish = fdate(es[aid], ef[aid])
        var = (early_finish - a.baseline_finish).days if a.baseline_finish else None
        results.append(CpmResult(
            id=aid, name=a.name, milestone_id=a.milestone_id, duration=a.duration, remaining=rem[aid], status=status[aid],
            early_start=cal.day(es[aid]), early_finish=early_finish,
            late_start=cal.day(ls[aid]), late_finish=fdate(ls[aid], lf[aid]),
            total_float=tf[aid], free_float=ff[aid], critical=aid in critical,
            baseline_start=a.baseline_start, baseline_finish=a.baseline_finish, finish_variance_days=var,
            driver=driver[aid], pct=a.pct,
        ))
    # critical path in schedule order
    path = sorted(critical, key=lambda i: (es[i], ef[i]))
    project_finish = fdate(min(es.values(), default=dd), project_ef) if ef else as_of
    return CpmSchedule(results, project_finish, target_finish, path, cycles)


def _snets(a: CpmActivity, ov: dict[str, Any]) -> list[tuple[date, str]]:
    out = []
    for d, ref in a.snet:
        if ref.startswith("procurement") and "material_date" in ov:
            d = min(d, date.fromisoformat(ov["material_date"]))
        if ref.startswith("design") and "design_date" in ov:
            d = date.fromisoformat(ov["design_date"])
        out.append((d, ref))
    return out


def lookahead(schedule: CpmSchedule, as_of: date, weeks: int = 3) -> list[CpmResult]:
    """Activities in progress or due to start within the window (the site's short-term plan)."""
    end = as_of + timedelta(weeks=weeks)
    return sorted(
        [a for a in schedule.activities if a.status == "in_progress" or (a.status == "not_started" and a.early_start <= end)],
        key=lambda a: (a.early_start, -int(a.critical)),
    )
