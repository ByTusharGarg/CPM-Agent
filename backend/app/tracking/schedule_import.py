"""Schedule import: MS Project XML (MSPDI) and Primavera P6 XER.

Both are parsed into one neutral structure, then `apply_import` replaces the
project's activity network and records a new current Baseline. Top-level WBS
nodes (MSP outline level 1 summary tasks / P6 WBS directly under the project)
become Milestones; leaf tasks become Activities.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, select

from ..models import (
    Activity,
    Baseline,
    BaselineActivity,
    Dependency,
    DesignDeliverable,
    Milestone,
    ProcurementItem,
    Project,
)


class ImportError_(ValueError):
    pass


@dataclass
class ImpActivity:
    key: str
    name: str
    wbs_key: str | None
    duration_days: int
    start: date | None
    finish: date | None
    actual_start: date | None = None
    actual_finish: date | None = None
    pct: float = 0.0


@dataclass
class ImportedSchedule:
    source: str  # msp_import | p6_import
    wbs: dict[str, str] = field(default_factory=dict)  # key -> name (top-level WBS only)
    activities: list[ImpActivity] = field(default_factory=list)
    links: list[tuple[str, str, str, int]] = field(default_factory=list)  # pred, succ, type, lag_days
    warnings: list[str] = field(default_factory=list)


def _d(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2].split(".")[0], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


# --------------------------------------------------------------------------- MS Project XML


def _iso_hours(dur: str | None) -> float:
    """'PT40H0M0S' -> 40.0 hours (MSPDI durations)."""
    if not dur:
        return 0.0
    m = re.fullmatch(r"-?P(?:(\d+)D)?T?(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", dur.strip())
    if not m:
        return 0.0
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 24 + h + mi / 60 + s / 3600


def parse_msp_xml(data: bytes) -> ImportedSchedule:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ImportError_(f"not a valid MS Project XML file: {e}") from e
    ns = {"p": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    q = (lambda t: f"p:{t}") if ns else (lambda t: t)

    def text(el, tag):
        x = el.find(q(tag), ns)
        return x.text if x is not None else None

    minutes_per_day = float(text(root, "MinutesPerDay") or 480)
    hours_per_day = minutes_per_day / 60
    out = ImportedSchedule("msp_import")
    tasks = root.findall(f"{q('Tasks')}/{q('Task')}", ns)
    if not tasks:
        raise ImportError_("no tasks found in the MS Project XML")
    stack: list[tuple[int, str]] = []  # (outline level, uid) of summary tasks
    for t in tasks:
        uid = text(t, "UID")
        if uid in (None, "0") or text(t, "IsNull") == "1":
            continue
        level = int(text(t, "OutlineLevel") or 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        name = (text(t, "Name") or f"Task {uid}").strip()
        if text(t, "Summary") == "1":
            if level == 1:
                out.wbs[uid] = name
            stack.append((level, uid))
            continue
        top = stack[0][1] if stack else None
        is_ms = text(t, "Milestone") == "1"
        dur = 0 if is_ms else max(int(math.ceil(_iso_hours(text(t, "Duration")) / hours_per_day - 1e-9)), 1)
        out.activities.append(ImpActivity(
            key=uid, name=name, wbs_key=top, duration_days=dur,
            start=_d(text(t, "Start")), finish=_d(text(t, "Finish")),
            actual_start=_d(text(t, "ActualStart")), actual_finish=_d(text(t, "ActualFinish")),
            pct=float(text(t, "PercentComplete") or 0),
        ))
        for link in t.findall(q("PredecessorLink"), ns):
            pred = text(link, "PredecessorUID")
            typ = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}.get(text(link, "Type") or "1", "FS")
            lag_tenth_min = float(text(link, "LinkLag") or 0)
            lag_days = int(round(lag_tenth_min / 10 / 60 / hours_per_day))
            if pred:
                out.links.append((pred, uid, typ, lag_days))
    return out


# --------------------------------------------------------------------------- Primavera P6 XER


def _xer_tables(data: bytes) -> dict[str, list[dict[str, str]]]:
    text = data.decode("utf-8", errors="replace") if b"\x00" not in data[:100] else data.decode("utf-16", errors="replace")
    tables: dict[str, list[dict[str, str]]] = {}
    current, fields = None, []
    for line in text.splitlines():
        parts = line.rstrip("\r").split("\t")
        if parts[0] == "%T":
            current = parts[1].strip()
            tables[current] = []
        elif parts[0] == "%F" and current:
            fields = [f.strip() for f in parts[1:]]
        elif parts[0] == "%R" and current:
            tables[current].append(dict(zip(fields, parts[1:])))
    if not tables:
        raise ImportError_("not a valid P6 XER file (no %T tables)")
    return tables


def parse_xer(data: bytes) -> ImportedSchedule:
    t = _xer_tables(data)
    if "TASK" not in t:
        raise ImportError_("XER has no TASK table")
    out = ImportedSchedule("p6_import")
    hours_per_day = 8.0
    for cal in t.get("CALENDAR", []):
        if cal.get("default_flag") == "Y" and cal.get("day_hr_cnt"):
            hours_per_day = float(cal["day_hr_cnt"]) or 8.0
    wbs = {w["wbs_id"]: w for w in t.get("PROJWBS", [])}
    roots = {k for k, w in wbs.items() if w.get("proj_node_flag") == "Y"}

    def top_wbs(wid: str | None) -> str | None:
        seen = set()
        while wid and wid in wbs and wid not in seen:
            seen.add(wid)
            parent = wbs[wid].get("parent_wbs_id")
            if parent in roots or parent not in wbs:
                return wid
            wid = parent
        return None

    for task in t["TASK"]:
        if task.get("task_type") == "TT_LOE":
            continue  # level-of-effort activities have no logic of their own
        tw = top_wbs(task.get("wbs_id"))
        if tw and tw not in out.wbs:
            out.wbs[tw] = wbs[tw].get("wbs_name", f"WBS {tw}")
        is_ms = task.get("task_type") in ("TT_Mile", "TT_FinMile")
        hours = float(task.get("target_drtn_hr_cnt") or 0)
        dur = 0 if is_ms else max(int(math.ceil(hours / hours_per_day - 1e-9)), 1)
        pct = float(task.get("phys_complete_pct") or 0)
        if task.get("status_code") == "TK_Complete":
            pct = 100.0
        out.activities.append(ImpActivity(
            key=task["task_id"], name=(task.get("task_name") or task.get("task_code") or task["task_id"]).strip(),
            wbs_key=tw, duration_days=dur,
            start=_d(task.get("target_start_date")), finish=_d(task.get("target_end_date")),
            actual_start=_d(task.get("act_start_date")), actual_finish=_d(task.get("act_end_date")), pct=pct,
        ))
    keys = {a.key for a in out.activities}
    for p in t.get("TASKPRED", []):
        s, pr = p.get("task_id"), p.get("pred_task_id")
        if s in keys and pr in keys:
            typ = (p.get("pred_type") or "PR_FS").replace("PR_", "")
            lag = int(round(float(p.get("lag_hr_cnt") or 0) / hours_per_day))
            out.links.append((pr, s, typ if typ in ("FS", "SS", "FF", "SF") else "FS", lag))
    return out


def parse_schedule(filename: str, data: bytes) -> ImportedSchedule:
    name = filename.lower()
    if name.endswith(".xer"):
        return parse_xer(data)
    if name.endswith(".xml"):
        return parse_msp_xml(data)
    raise ImportError_("upload an MS Project XML (.xml) or Primavera P6 (.xer) file")


# --------------------------------------------------------------------------- apply


def apply_import(s: Session, project: Project, imp: ImportedSchedule, label: str) -> dict:
    """Replace the activity network and make the imported dates the current baseline."""
    if not imp.activities:
        raise ImportError_("the file has no activities")
    pid = project.id
    # detach and remove the old network
    for model in (ProcurementItem, DesignDeliverable):
        for row in s.exec(select(model).where(model.project_id == pid, model.activity_id != None)).all():  # noqa: E711
            row.activity_id = None
            s.add(row)
    old_ids = [a.id for a in s.exec(select(Activity).where(Activity.project_id == pid)).all()]
    for d in s.exec(select(Dependency).where(Dependency.project_id == pid)).all():
        s.delete(d)
    for b in s.exec(select(Baseline).where(Baseline.project_id == pid)).all():
        for ba in s.exec(select(BaselineActivity).where(BaselineActivity.baseline_id == b.id)).all():
            s.delete(ba)
        b.is_current = False
        s.add(b)
    s.flush()
    for aid in old_ids:
        s.delete(s.get(Activity, aid))
    s.flush()

    # milestones from top-level WBS (match by name, else create)
    existing = {m.name.strip().lower(): m for m in s.exec(select(Milestone).where(Milestone.project_id == pid)).all()}
    ms_for_key: dict[str | None, Milestone] = {}
    for key, name in imp.wbs.items():
        children = [a for a in imp.activities if a.wbs_key == key]
        starts = [a.start for a in children if a.start]
        finishes = [a.finish for a in children if a.finish]
        m = existing.get(name.strip().lower())
        if m is None:
            m = Milestone(project_id=pid, name=name, planned_start=min(starts) if starts else project.start_date,
                          planned_end=max(finishes) if finishes else project.target_end_date)
        elif starts and finishes:
            m.planned_start, m.planned_end = min(starts), max(finishes)
        s.add(m)
        s.flush()
        ms_for_key[key] = m
    if any(a.wbs_key not in ms_for_key for a in imp.activities):
        m = existing.get("unassigned activities") or Milestone(
            project_id=pid, name="Unassigned activities", planned_start=project.start_date, planned_end=project.target_end_date)
        s.add(m)
        s.flush()
        ms_for_key.setdefault(None, m)

    baseline = Baseline(project_id=pid, name=label, source=imp.source, is_current=True)
    s.add(baseline)
    s.flush()
    id_for: dict[str, int] = {}
    for i, a in enumerate(imp.activities):
        m = ms_for_key.get(a.wbs_key) or ms_for_key[None]
        row = Activity(project_id=pid, milestone_id=m.id, code=a.key, name=a.name, duration_days=a.duration_days,
                       planned_start=a.start, planned_finish=a.finish, actual_start=a.actual_start,
                       actual_finish=a.actual_finish, percent_complete=a.pct, sort_order=i)
        s.add(row)
        s.flush()
        id_for[a.key] = row.id
        s.add(BaselineActivity(baseline_id=baseline.id, activity_id=row.id, start=a.start, finish=a.finish, duration_days=a.duration_days))
    n_links = 0
    for pred, succ, typ, lag in imp.links:
        if pred in id_for and succ in id_for and pred != succ:
            s.add(Dependency(project_id=pid, predecessor_id=id_for[pred], successor_id=id_for[succ], type=typ, lag_days=lag))
            n_links += 1
    sync_milestone_progress(s, pid)
    finishes = [a.finish for a in imp.activities if a.finish]
    return {"baseline_id": baseline.id, "activities": len(id_for), "links": n_links, "milestones": len(set(m.id for m in ms_for_key.values())),
            "baseline_finish": max(finishes).isoformat() if finishes else None, "warnings": imp.warnings}


def sync_milestone_progress(s: Session, project_id: int) -> None:
    """Milestone % complete = duration-weighted % of its activities (keeps EVM consistent)."""
    acts = s.exec(select(Activity).where(Activity.project_id == project_id)).all()
    by_ms: dict[int, list[Activity]] = {}
    for a in acts:
        if a.milestone_id:
            by_ms.setdefault(a.milestone_id, []).append(a)
    for mid, rows in by_ms.items():
        m = s.get(Milestone, mid)
        w = sum(max(a.duration_days, 1) for a in rows)
        m.percent_complete = round(sum(max(a.duration_days, 1) * a.percent_complete for a in rows) / w, 1)
        starts = [a.actual_start for a in rows if a.actual_start]
        m.actual_start = min(starts) if starts else None
        m.actual_end = max(a.actual_finish for a in rows) if all(a.actual_finish for a in rows) else None
        s.add(m)
