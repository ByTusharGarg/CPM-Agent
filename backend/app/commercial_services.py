"""Contracts, change orders and pay applications (phase 11).

Money decisions are made by people with the right role (API) or by agents only
through approval-locked actions (change_order.approve, payapp.certify), which
call the same *_core functions below once the owner approves.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, col, func, select

from .config import settings
from .db import session_scope, today, utcnow
from .models import (
    Activity,
    ChangeOrder,
    Contract,
    ContractLine,
    CostEntry,
    Membership,
    Milestone,
    PayApplication,
    PayAppLine,
    User,
)
from .notify import notify_background, send_direct
from .services import ServiceError

if TYPE_CHECKING:
    from .harness.runtime import Runtime


# --------------------------------------------------------------------------- views


def contract_view(s: Session, c: Contract) -> dict[str, Any]:
    lines = s.exec(select(ContractLine).where(ContractLine.contract_id == c.id).order_by(ContractLine.sort_order, ContractLine.id)).all()
    return {
        "id": c.id, "ref": f"contract:{c.id}", "number": c.number, "title": c.title, "contractor_id": c.contractor_id,
        "retention_pct": c.retention_pct, "status": c.status, "value": round(sum(x.value for x in lines), 2),
        "lines": [{"id": x.id, "code": x.code, "description": x.description, "value": x.value,
                   "milestone_id": x.milestone_id, "activity_id": x.activity_id} for x in lines],
    }


def co_view(co: ChangeOrder) -> dict[str, Any]:
    return {
        "id": co.id, "ref": f"change_order:{co.id}", "number": co.number, "title": co.title, "description": co.description,
        "reason": co.reason, "stage": co.stage, "contract_id": co.contract_id, "proposed_amount": co.proposed_amount,
        "approved_amount": co.approved_amount, "recommended_amount": co.recommended_amount, "assessment": co.assessment,
        "time_impact_days": co.time_impact_days, "source_ref": co.source_ref, "created_at": co.created_at.isoformat(),
        "decided_at": co.decided_at.isoformat() if co.decided_at else None,
    }


def payapp_view(s: Session, p: PayApplication) -> dict[str, Any]:
    lines = s.exec(select(PayAppLine).where(PayAppLine.pay_app_id == p.id)).all()
    return {
        "id": p.id, "ref": f"payapp:{p.id}", "number": p.number, "contract_id": p.contract_id, "period_end": p.period_end.isoformat(),
        "status": p.status, "gross_to_date": p.gross_to_date, "certified_to_date": p.certified_to_date, "retention": p.retention,
        "previous_certified": p.previous_certified, "amount_due": p.amount_due, "notes": p.notes,
        "certified_at": p.certified_at.isoformat() if p.certified_at else None,
        "lines": [{"contract_line_id": x.contract_line_id, "claimed_pct": x.claimed_pct, "certified_pct": x.certified_pct} for x in lines],
    }


def _number(s: Session, model, field, **where) -> int:
    q = select(func.max(field))
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return int(s.exec(q).one() or 0) + 1


# --------------------------------------------------------------------------- contracts


def create_contract(pid: int, *, title: str, contractor_id: int | None, number: str = "", retention_pct: float = 5.0,
                    lines: list[dict[str, Any]]) -> dict[str, Any]:
    if not lines:
        raise ServiceError(400, "a contract needs at least one schedule-of-values line")
    with session_scope() as s:
        c = Contract(project_id=pid, contractor_id=contractor_id, title=title.strip(), number=number.strip(), retention_pct=retention_pct)
        s.add(c)
        s.flush()
        for i, ln in enumerate(lines):
            if ln.get("milestone_id"):
                m = s.get(Milestone, ln["milestone_id"])
                if m is None or m.project_id != pid:
                    raise ServiceError(400, f"milestone {ln['milestone_id']} not in this project")
            if ln.get("activity_id"):
                a = s.get(Activity, ln["activity_id"])
                if a is None or a.project_id != pid:
                    raise ServiceError(400, f"activity {ln['activity_id']} not in this project")
            s.add(ContractLine(contract_id=c.id, code=ln.get("code", ""), description=ln["description"], value=float(ln["value"]),
                               milestone_id=ln.get("milestone_id"), activity_id=ln.get("activity_id"), sort_order=i))
        s.flush()
        return contract_view(s, c)


# --------------------------------------------------------------------------- change orders


async def submit_change_order(rt: "Runtime", pid: int, *, title: str, description: str, reason: str, proposed_amount: float,
                              time_impact_days: int, contract_id: int | None, source_ref: str, user_id: int | None, by: str,
                              role: str) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        if contract_id:
            c = s.get(Contract, contract_id)
            if c is None or c.project_id != pid:
                raise ServiceError(400, "contract not found in this project")
        co = ChangeOrder(project_id=pid, contract_id=contract_id, number=_number(s, ChangeOrder, ChangeOrder.number, project_id=pid),
                         title=title.strip()[:200], description=description.strip(), reason=reason, proposed_amount=float(proposed_amount),
                         time_impact_days=int(time_impact_days), source_ref=source_ref.strip(),
                         stage="submitted" if role == "contractor" else "pco", created_by=user_id)
        s.add(co)
        s.flush()
        view = co_view(co)
    rt.publish(pid, "commercial", {})
    await rt.emit(pid, source="system", kind="change_order_submitted",
                  payload={"change_order": view["ref"], "number": view["number"], "title": view["title"], "stage": view["stage"],
                           "proposed_amount": view["proposed_amount"], "time_impact_days": view["time_impact_days"],
                           "contract": f"contract:{contract_id}" if contract_id else None, "source": source_ref, "by": by},
                  thread_key=f"change_order:{view['id']}", thread_title=f"CO #{view['number']} {view['title']}")
    return view


def decide_change_order_core(s: Session, co_id: int, approve: bool, amount: float | None, note: str, user_id: int | None) -> ChangeOrder:
    co = s.get(ChangeOrder, co_id)
    if co is None:
        raise ServiceError(404, "change order not found")
    if co.stage not in ("pco", "submitted"):
        raise ServiceError(409, f"change order already {co.stage}")
    co.stage = "approved" if approve else "rejected"
    co.approved_amount = float(amount if amount is not None else co.proposed_amount) if approve else None
    co.decided_by, co.decided_at = user_id, utcnow()
    if note:
        co.assessment = (co.assessment + "\n" if co.assessment else "") + f"Decision note: {note}"
    s.add(co)
    return co


async def decide_change_order(rt: "Runtime", co_id: int, approve: bool, amount: float | None, note: str, user_id: int, by: str) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        co = decide_change_order_core(s, co_id, approve, amount, note, user_id)
        view, pid = co_view(co), co.project_id
    rt.publish(pid, "commercial", {})
    rt.publish(pid, "kpi", {})
    await rt.emit(pid, source="owner", kind="change_order_decided",
                  payload={"change_order": view["ref"], "number": view["number"], "stage": view["stage"],
                           "approved_amount": view["approved_amount"], "by": by}, thread_key=f"change_order:{co_id}")
    return view


# --------------------------------------------------------------------------- pay applications


async def submit_pay_app(rt: "Runtime", pid: int, *, contract_id: int, period_end: date | None, claims: list[dict[str, Any]],
                         notes: str, user_id: int | None, by: str) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        c = s.get(Contract, contract_id)
        if c is None or c.project_id != pid:
            raise ServiceError(404, "contract not found in this project")
        lines = {x.id: x for x in s.exec(select(ContractLine).where(ContractLine.contract_id == c.id)).all()}
        if not claims:
            raise ServiceError(400, "claim at least one line")
        open_app = s.exec(select(PayApplication).where(PayApplication.contract_id == c.id, PayApplication.status == "submitted")).first()
        if open_app:
            raise ServiceError(409, f"pay app #{open_app.number} is still awaiting certification")
        prev = s.exec(select(PayApplication).where(PayApplication.contract_id == c.id, col(PayApplication.status).in_(["certified", "paid"]))
                      .order_by(col(PayApplication.number).desc())).first()
        gross = 0.0
        app = PayApplication(project_id=pid, contract_id=c.id, number=_number(s, PayApplication, PayApplication.number, contract_id=c.id),
                             period_end=period_end or today(), notes=notes.strip(), submitted_by=user_id,
                             previous_certified=float(prev.certified_to_date or 0) if prev else 0.0)
        s.add(app)
        s.flush()
        for cl in claims:
            ln = lines.get(int(cl["contract_line_id"]))
            if ln is None:
                raise ServiceError(400, f"line {cl['contract_line_id']} is not on this contract")
            pct = float(cl["claimed_pct"])
            if not 0 <= pct <= 100:
                raise ServiceError(400, "claimed % must be between 0 and 100")
            gross += ln.value * pct / 100
            s.add(PayAppLine(pay_app_id=app.id, contract_line_id=ln.id, claimed_pct=pct))
        app.gross_to_date = round(gross, 2)
        app.retention = round(gross * c.retention_pct / 100, 2)
        app.amount_due = round((gross - app.previous_certified) * (1 - c.retention_pct / 100), 2)
        s.add(app)
        s.flush()
        view = payapp_view(s, app)
    rt.publish(pid, "commercial", {})
    await rt.emit(pid, source="system", kind="payapp_submitted",
                  payload={"payapp": view["ref"], "number": view["number"], "contract": f"contract:{contract_id}",
                           "gross_to_date": view["gross_to_date"], "amount_due": view["amount_due"], "by": by},
                  thread_key=f"payapp:{view['id']}", thread_title=f"Pay app #{view['number']} contract {contract_id}")
    notify_background(pid, "warning", f"Pay application #{view['number']} to certify",
                      f"Claim to date {view['gross_to_date']:,.0f}; due this period {view['amount_due']:,.0f}.",
                      f"{settings.app_base_url}/projects/{pid}/commercial", perm="payapp.certify")
    return view


def certify_core(s: Session, app_id: int, line_pcts: dict[int, float] | None, note: str, user_id: int | None) -> PayApplication:
    """Certify (optionally at lower %s than claimed) and book the period's value as actual cost."""
    app = s.get(PayApplication, app_id)
    if app is None:
        raise ServiceError(404, "pay application not found")
    if app.status != "submitted":
        raise ServiceError(409, f"pay application already {app.status}")
    c = s.get(Contract, app.contract_id)
    lines = {x.id: x for x in s.exec(select(ContractLine).where(ContractLine.contract_id == c.id)).all()}
    prev = s.exec(select(PayApplication).where(PayApplication.contract_id == c.id, col(PayApplication.status).in_(["certified", "paid"]),
                                               PayApplication.id != app.id).order_by(col(PayApplication.number).desc())).first()
    prev_pct = {pl.contract_line_id: float(pl.certified_pct or 0)
                for pl in (s.exec(select(PayAppLine).where(PayAppLine.pay_app_id == prev.id)).all() if prev else [])}
    total = 0.0
    for pl in s.exec(select(PayAppLine).where(PayAppLine.pay_app_id == app.id)).all():
        pct = float((line_pcts or {}).get(pl.contract_line_id, pl.claimed_pct))
        pct = max(0.0, min(pct, pl.claimed_pct))  # can certify less than claimed, never more
        pl.certified_pct = pct
        s.add(pl)
        ln = lines[pl.contract_line_id]
        total += ln.value * pct / 100
        delta = ln.value * (pct - prev_pct.get(ln.id, 0.0)) / 100
        if delta:
            ms = ln.milestone_id or (s.get(Activity, ln.activity_id).milestone_id if ln.activity_id else None)
            s.add(CostEntry(project_id=app.project_id, milestone_id=ms, kind="actual", amount=round(delta, 2),
                            description=f"Pay app #{app.number} certified - {c.title}: {ln.description}", entry_date=today()))
    app.certified_to_date = round(total, 2)
    app.retention = round(total * c.retention_pct / 100, 2)
    app.amount_due = round((total - app.previous_certified) * (1 - c.retention_pct / 100), 2)
    app.status, app.certified_by, app.certified_at = "certified", user_id, utcnow()
    if note:
        app.notes = (app.notes + "\n" if app.notes else "") + f"Certifier: {note}"
    s.add(app)
    return app


async def certify_pay_app(rt: "Runtime", app_id: int, line_pcts: dict[int, float] | None, note: str, user_id: int, by: str,
                          reject: bool = False) -> dict[str, Any]:
    with session_scope(rt.engine) as s:
        if reject:
            app = s.get(PayApplication, app_id)
            if app is None or app.status != "submitted":
                raise ServiceError(409, "pay application is not awaiting certification")
            if not note.strip():
                raise ServiceError(400, "say why the pay application is rejected")
            app.status, app.notes = "rejected", (app.notes + "\n" if app.notes else "") + f"Rejected: {note.strip()}"
            s.add(app)
        else:
            app = certify_core(s, app_id, line_pcts, note, user_id)
        view, pid, cid = payapp_view(s, app), app.project_id, s.get(Contract, app.contract_id).contractor_id
        people = []
        if cid:
            for m in s.exec(select(Membership).where(Membership.project_id == pid, Membership.contractor_id == cid)).all():
                u = s.get(User, m.user_id)
                if u and u.is_active:
                    people.append((u.id, "whatsapp" if u.phone else "email"))
    rt.publish(pid, "commercial", {})
    rt.publish(pid, "kpi", {})
    await rt.emit(pid, source="owner", kind="payapp_decided",
                  payload={"payapp": view["ref"], "number": view["number"], "status": view["status"],
                           "certified_to_date": view["certified_to_date"], "amount_due": view["amount_due"], "by": by},
                  thread_key=f"payapp:{app_id}")
    body = (f"Pay application #{view['number']}: {view['status'].upper()}."
            + (f" Certified to date {view['certified_to_date']:,.0f}; payable this period {view['amount_due']:,.0f}." if not reject else f" {note.strip()}"))
    loop = asyncio.get_running_loop()
    for uid, ch in people:
        loop.run_in_executor(None, lambda uid=uid, ch=ch: send_direct(ch, f"Pay application #{view['number']}", body, uid, "", pid))
    return view
