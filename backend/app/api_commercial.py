"""REST endpoints: contracts, change orders, pay applications, commercial summary (phase 11)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import col, select

from . import commercial_services as cs
from .auth import Access, check, current_user, require
from .db import session_scope
from .models import ChangeOrder, Contract, PayApplication, User
from .tracking.service import project_health

router = APIRouter(prefix="/api")


def _rt(request: Request):
    return request.app.state.rt


def _own_contract_ids(s, pid: int, acc: Access) -> set[int] | None:
    if acc.role != "contractor":
        return None
    return {c.id for c in s.exec(select(Contract).where(Contract.project_id == pid, Contract.contractor_id == acc.contractor_id)).all()}


@router.get("/projects/{pid}/commercial")
def commercial(pid: int, acc: Access = Depends(require("commercial.view"))) -> dict[str, Any]:
    with session_scope() as s:
        h = project_health(s, pid)
        own = _own_contract_ids(s, pid, acc)
        contracts = [cs.contract_view(s, c) for c in s.exec(select(Contract).where(Contract.project_id == pid).order_by(Contract.id)).all()
                     if own is None or c.id in own]
        cos = [cs.co_view(c) for c in s.exec(select(ChangeOrder).where(ChangeOrder.project_id == pid).order_by(col(ChangeOrder.number).desc())).all()
               if own is None or c.contract_id in own]
        apps = [cs.payapp_view(s, p) for p in s.exec(select(PayApplication).where(PayApplication.project_id == pid)
                                                      .order_by(col(PayApplication.id).desc())).all() if own is None or p.contract_id in own]
    com = h.commercial
    summary = None
    if com is not None:
        d = asdict(com)
        d.pop("rule_hits", None)
        if own is not None:  # contractors see their own position, not the owner's budget
            d = {"contracts": [c for c in d["contracts"] if c["id"] in own],
                 "overbilling": [o for o in d["overbilling"] if o["contract_id"] in own]}
        summary = d
    return {"currency": h.currency, "summary": summary, "contracts": contracts, "change_orders": cos, "pay_apps": apps}


class LineIn(BaseModel):
    code: str = ""
    description: str
    value: float = Field(gt=0)
    milestone_id: Optional[int] = None
    activity_id: Optional[int] = None


class ContractIn(BaseModel):
    title: str = Field(min_length=2)
    contractor_id: Optional[int] = None
    number: str = ""
    retention_pct: float = Field(5.0, ge=0, le=20)
    lines: list[LineIn]


@router.post("/projects/{pid}/contracts")
def create_contract(pid: int, body: ContractIn, request: Request, acc: Access = Depends(require("contract.manage"))) -> dict[str, Any]:
    out = cs.create_contract(pid, title=body.title, contractor_id=body.contractor_id, number=body.number,
                             retention_pct=body.retention_pct, lines=[ln.model_dump() for ln in body.lines])
    _rt(request).publish(pid, "commercial", {})
    return out


class CoIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = ""
    reason: Literal["owner_request", "design_change", "site_condition", "error_omission", "other"] = "other"
    proposed_amount: float = Field(ge=0)
    time_impact_days: int = Field(0, ge=0, le=730)
    contract_id: Optional[int] = None
    source_ref: str = ""


@router.post("/projects/{pid}/change-orders")
async def submit_co(pid: int, body: CoIn, request: Request, acc: Access = Depends(require("change_order.submit"))) -> dict[str, Any]:
    if acc.role == "contractor":
        with session_scope() as s:
            own = _own_contract_ids(s, pid, acc)
        if body.contract_id not in own:
            raise HTTPException(403, "contractors raise change orders on their own contract")
    return await cs.submit_change_order(_rt(request), pid, title=body.title, description=body.description, reason=body.reason,
                                        proposed_amount=body.proposed_amount, time_impact_days=body.time_impact_days,
                                        contract_id=body.contract_id, source_ref=body.source_ref, user_id=acc.user.id,
                                        by=acc.actor, role=acc.role)


class CoDecision(BaseModel):
    approve: bool
    amount: Optional[float] = Field(None, ge=0)
    note: str = ""


@router.post("/change-orders/{co_id}/decide")
async def decide_co(co_id: int, body: CoDecision, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    with session_scope() as s:
        co = s.get(ChangeOrder, co_id)
        if co is None:
            raise HTTPException(404)
        pid = co.project_id
    acc = check(user, pid, "change_order.approve")
    return await cs.decide_change_order(_rt(request), co_id, body.approve, body.amount, body.note, user.id, acc.actor)


class ClaimIn(BaseModel):
    contract_line_id: int
    claimed_pct: float = Field(ge=0, le=100)


class PayAppIn(BaseModel):
    contract_id: int
    period_end: Optional[date] = None
    claims: list[ClaimIn]
    notes: str = ""


@router.post("/projects/{pid}/pay-apps")
async def submit_pay_app(pid: int, body: PayAppIn, request: Request, acc: Access = Depends(require("payapp.submit"))) -> dict[str, Any]:
    if acc.role == "contractor":
        with session_scope() as s:
            if body.contract_id not in _own_contract_ids(s, pid, acc):
                raise HTTPException(403, "not your contract")
    return await cs.submit_pay_app(_rt(request), pid, contract_id=body.contract_id, period_end=body.period_end,
                                   claims=[c.model_dump() for c in body.claims], notes=body.notes, user_id=acc.user.id, by=acc.actor)


class CertifyIn(BaseModel):
    line_pcts: dict[int, float] = Field(default_factory=dict, description="contract_line_id -> % to certify (<= claimed)")
    note: str = ""


def _app_project(app_id: int) -> int:
    with session_scope() as s:
        p = s.get(PayApplication, app_id)
        if p is None:
            raise HTTPException(404)
        return p.project_id


@router.post("/pay-apps/{app_id}/certify")
async def certify(app_id: int, body: CertifyIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    acc = check(user, _app_project(app_id), "payapp.certify")
    return await cs.certify_pay_app(_rt(request), app_id, body.line_pcts, body.note, user.id, acc.actor)


class RejectIn(BaseModel):
    note: str = Field(min_length=3)


@router.post("/pay-apps/{app_id}/reject")
async def reject(app_id: int, body: RejectIn, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    acc = check(user, _app_project(app_id), "payapp.certify")
    return await cs.certify_pay_app(_rt(request), app_id, None, body.note, user.id, acc.actor, reject=True)
