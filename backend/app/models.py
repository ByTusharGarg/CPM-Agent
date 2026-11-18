"""Domain + harness data model.

Every record has an integer id and is addressable in the reference graph as
"<kind>:<id>" (e.g. "milestone:3", "work_item:12"). Records embed the ids of
related records so agents can follow links hop-by-hop ("Chain of Recall").
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .db import utcnow


def _json(default: Any = None):
    return Field(default_factory=lambda: default() if callable(default) else default, sa_column=Column(JSON))


DEFAULT_THRESHOLDS = {
    "milestone_slip_days": 7,       # warn when a milestone is forecast > N days late
    "project_slip_days": 0,         # warn when project finish is forecast late at all
    "spi_min": 0.90,                # schedule performance index floor
    "cpi_min": 0.95,                # cost performance index floor
    "cost_overrun_pct": 5.0,        # EAC above budget by more than this %
    "stale_update_hours": 48,       # contractor silent for longer than this
}


# --------------------------------------------------------------------------- identity & tenancy

ROLES = ("owner", "owner_rep", "consultant", "contractor", "viewer")


class Organization(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="organization.id", index=True)
    email: str = Field(index=True, unique=True)
    name: str = ""
    phone: str = ""  # E.164, used by the WhatsApp channel (phase 8)
    is_org_admin: bool = False  # implicit owner on every project of the org
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Membership(SQLModel, table=True):
    """A user's role on one project. Contractor members are linked to a Contractor record."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    role: str  # one of ROLES
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    created_at: datetime = Field(default_factory=utcnow)


class LoginCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    used: bool = False


class AuthSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utcnow)


class NotificationPref(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, unique=True)
    email_enabled: bool = True
    whatsapp_enabled: bool = False
    immediate_min_severity: str = "critical"  # info | warning | critical; lower severities go to the digest
    quiet_start_hour: Optional[int] = 21  # local hour; None disables quiet hours
    quiet_end_hour: Optional[int] = 7
    digest_enabled: bool = True


class Notification(SQLModel, table=True):
    """Outbox for out-of-app notifications (email / WhatsApp) and the daily digest."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    address: str = ""  # explicit phone/email when there is no user (e.g. a contractor company number)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    channel: str  # email | whatsapp
    severity: str = "info"
    title: str
    body: str = ""
    link: str = ""
    ref: str = ""  # what a reply answers, e.g. "alert:12" or "action:5"
    actions: list = _json(list)  # [{"id": "alert:12:opt:0", "label": "Expedite"}] -> buttons / signed links
    status: str = "queued"  # queued | sent | digest | failed | skipped | awaiting_window
    provider_id: str = ""  # e.g. WhatsApp message id
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    sent_at: Optional[datetime] = None


class StoredFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    key: str = Field(unique=True)
    filename: str
    content_type: str = "application/octet-stream"
    size: int = 0
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    source: str = "upload"  # upload | whatsapp | email
    # Photos sent on WhatsApp without a caption wait here until the sender's next text report.
    pending_for_user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class InboundMessage(SQLModel, table=True):
    """Every inbound WhatsApp/email message: idempotency (providers redeliver) + audit."""

    id: Optional[int] = Field(default=None, primary_key=True)
    channel: str  # whatsapp | email
    provider_id: str = Field(index=True, unique=True)
    from_addr: str
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    kind: str = "text"  # text | button | image | audio | email
    body: str = ""
    outcome: str = ""  # what we did with it: daily_update | decision | approval | chat | dropped:<why> | ...
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- domain


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: Optional[int] = Field(default=None, foreign_key="organization.id", index=True)
    name: str
    scope: str = ""
    location: str = ""
    owner_name: str = "Owner"
    currency: str = "USD"
    budget: float = 0.0
    start_date: date
    target_end_date: date
    status: str = "active"  # active | on_hold | closed
    thresholds: dict = _json(lambda: dict(DEFAULT_THRESHOLDS))
    # Working calendar for CPM: weekday numbers (Mon=0) that are working days, plus holidays.
    work_days: str = "0,1,2,3,4,5"  # Mon-Sat (6-day week, common on Indian sites)
    holidays: list = _json(list)     # ["2026-11-01", ...]
    created_at: datetime = Field(default_factory=utcnow)


class Contractor(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    trade: str = ""
    contact: str = ""
    allowlisted: bool = True  # only allowlisted contractors may submit updates (fast-path filter)
    phone: str = ""  # company WhatsApp number used for intake mapping (phase 8)


class Milestone(SQLModel, table=True):
    """A work package / milestone on the baseline schedule."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    discipline: str = ""
    planned_start: date
    planned_end: date
    budget: float = 0.0
    percent_complete: float = 0.0
    actual_start: Optional[date] = None
    actual_end: Optional[date] = None
    depends_on: list = _json(list)
    is_critical: bool = False
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    notes: str = ""


class Activity(SQLModel, table=True):
    """A schedulable activity (P6 'activity' / MS Project task). Milestones are its WBS roll-up."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id", index=True)
    code: str = ""  # external id (P6 task_code / MSP UID) for re-imports
    name: str
    duration_days: int = 1  # working days; 0 = milestone event
    planned_start: Optional[date] = None   # current baseline
    planned_finish: Optional[date] = None
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    percent_complete: float = 0.0
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    sort_order: int = 0


class Dependency(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    predecessor_id: int = Field(foreign_key="activity.id", index=True)
    successor_id: int = Field(foreign_key="activity.id", index=True)
    type: str = "FS"  # FS | SS | FF | SF
    lag_days: int = 0  # working days, may be negative (lead)


class Baseline(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    source: str = "manual"  # manual | msp_import | p6_import | rebaseline
    is_current: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class BaselineActivity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    baseline_id: int = Field(foreign_key="baseline.id", index=True)
    activity_id: int = Field(foreign_key="activity.id", index=True)
    start: Optional[date] = None
    finish: Optional[date] = None
    duration_days: int = 0


class Document(SQLModel, table=True):
    """A controlled document (drawing, specification, report...) with revisions."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    number: str = Field(default="", index=True)  # e.g. "A-101", "S-2001"; unique per project when set
    title: str
    discipline: str = ""  # architecture | structure | mep | facade | civil | general ...
    doc_type: str = "drawing"  # drawing | specification | report | contract | correspondence | submittal | other
    status: str = "for_review"  # draft | for_review | approved | for_construction | superseded | rejected
    current_version_id: Optional[int] = None
    design_id: Optional[int] = Field(default=None, foreign_key="designdeliverable.id")
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")
    classified: bool = False  # set once the Document Controller has reviewed the metadata
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    revision: str = "R0"
    file_id: int = Field(foreign_key="storedfile.id")
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    source: str = "upload"  # upload | email | whatsapp | submittal
    notes: str = ""
    pages: int = 0
    text_chars: int = 0  # 0 = no extractable text (scan/image)
    created_at: datetime = Field(default_factory=utcnow)


class DocChunk(SQLModel, table=True):
    """Searchable text of one page-sized piece of a document version."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    version_id: int = Field(foreign_key="documentversion.id", index=True)
    page: int = 1
    seq: int = 0
    text: str


class RFI(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    number: int = 0  # sequential per project
    subject: str
    question: str
    raised_by: Optional[int] = Field(default=None, foreign_key="user.id")
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    ball_in_court: str = "consultant"  # role or "user:<id>" expected to act next
    due_date: date
    status: str = "open"  # open | answered | closed | void
    answer: str = ""
    answered_by: Optional[int] = Field(default=None, foreign_key="user.id")
    answered_at: Optional[datetime] = None
    cost_impact: bool = False
    time_impact: bool = False
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")
    document_ids: list = _json(list)
    created_at: datetime = Field(default_factory=utcnow)


class Submittal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    number: int = 0
    title: str
    spec_section: str = ""
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    revision: int = 0
    status: str = "submitted"  # submitted | under_review | approved | approved_as_noted | revise_resubmit | rejected
    due_date: date  # review due
    required_by: Optional[date] = None  # when the linked work needs it approved
    document_id: Optional[int] = Field(default=None, foreign_key="document.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")
    reviewer_comments: str = ""
    decided_by: Optional[int] = Field(default=None, foreign_key="user.id")
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class DesignDeliverable(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str
    discipline: str = ""
    due_date: date
    status: str = "not_started"  # not_started | in_progress | submitted | approved | rejected
    revision: str = "R0"
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")  # the activity it unblocks
    notes: str = ""


class ProcurementItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    item: str
    vendor: str = ""
    quantity: float = 0
    unit: str = ""
    cost: float = 0.0
    lead_time_days: int = 0
    po_date: Optional[date] = None
    required_on_site: date
    expected_delivery: Optional[date] = None
    status: str = "not_ordered"  # not_ordered | ordered | shipped | delivered | delayed
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")  # the activity that needs it
    notes: str = ""


class DailyUpdate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    contractor_id: int = Field(foreign_key="contractor.id")
    report_date: date
    raw_text: str
    parsed: dict = _json(dict)
    processed: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Contract(SQLModel, table=True):
    """A commitment with a contractor/supplier, broken into a schedule of values (ContractLine)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    contractor_id: Optional[int] = Field(default=None, foreign_key="contractor.id")
    number: str = ""
    title: str
    retention_pct: float = 5.0
    status: str = "active"  # draft | active | closed
    created_at: datetime = Field(default_factory=utcnow)


class ContractLine(SQLModel, table=True):
    """Schedule-of-values line. Progress comes from the linked activity or milestone."""

    id: Optional[int] = Field(default=None, primary_key=True)
    contract_id: int = Field(foreign_key="contract.id", index=True)
    code: str = ""
    description: str
    value: float
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id")
    activity_id: Optional[int] = Field(default=None, foreign_key="activity.id")
    sort_order: int = 0


class ChangeOrder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    contract_id: Optional[int] = Field(default=None, foreign_key="contract.id")
    number: int = 0
    title: str
    description: str = ""
    reason: str = "owner_request"  # owner_request | design_change | site_condition | error_omission | other
    stage: str = "pco"  # pco (potential) | submitted | approved | rejected | void
    proposed_amount: float = 0.0
    approved_amount: Optional[float] = None
    time_impact_days: int = 0
    source_ref: str = ""  # e.g. rfi:1
    assessment: str = ""  # commercial manager's note (agent or QS)
    recommended_amount: Optional[float] = None
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    decided_by: Optional[int] = Field(default=None, foreign_key="user.id")
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class PayApplication(SQLModel, table=True):
    """A contractor's progress claim for a period (running account bill)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    contract_id: int = Field(foreign_key="contract.id", index=True)
    number: int = 0
    period_end: date
    status: str = "submitted"  # submitted | certified | rejected | paid
    gross_to_date: float = 0.0       # claimed value of work done to date
    certified_to_date: Optional[float] = None
    retention: float = 0.0
    previous_certified: float = 0.0
    amount_due: float = 0.0          # this period, after retention
    notes: str = ""
    submitted_by: Optional[int] = Field(default=None, foreign_key="user.id")
    certified_by: Optional[int] = Field(default=None, foreign_key="user.id")
    certified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class PayAppLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pay_app_id: int = Field(foreign_key="payapplication.id", index=True)
    contract_line_id: int = Field(foreign_key="contractline.id")
    claimed_pct: float  # cumulative % complete claimed
    certified_pct: Optional[float] = None


class FundingSource(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str  # e.g. "Equity", "Term loan - SBI"
    amount: float
    drawn: float = 0.0


class CostEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    milestone_id: Optional[int] = Field(default=None, foreign_key="milestone.id")
    kind: str = "actual"  # committed | actual | change_order
    amount: float
    description: str = ""
    entry_date: date


class Alert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    rule: str = "manual"
    rule_key: str = ""  # stable key so the same condition is not alerted twice
    severity: str = "warning"  # info | warning | critical
    title: str
    details: str = ""
    options: list = _json(list)  # [{label, description, impact_days, impact_cost}]
    refs: list = _json(list)     # ["milestone:3", "procurement:1", ...]
    status: str = "open"  # open | awaiting_owner | resolved | dismissed
    thread_id: Optional[int] = Field(default=None, foreign_key="thread.id")
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None


class Decision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    alert_id: Optional[int] = Field(default=None, foreign_key="alert.id")
    choice: str
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Report(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    period: str
    content: str
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- harness


class Thread(SQLModel, table=True):
    """A group of related events (one procurement item, one alert->decision chain, ...)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    key: str = Field(index=True)  # e.g. "procurement:3", "alert:7", "contractor:2"
    title: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_event_at: datetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    source: str  # owner | contractor | engine | scheduler | worker | watchdog | system
    kind: str    # owner_message | daily_update | rule_hit | health_check | work_result | ...
    payload: dict = _json(dict)
    thread_id: Optional[int] = Field(default=None, foreign_key="thread.id")
    status: str = "pending"  # pending | to_router | to_worker | dropped | processed
    route_note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class WorkItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    worker: str  # design | procurement | site_updates | cost | planner | reporter
    title: str
    instructions: str = ""
    status: str = "queued"  # queued | running | done | failed | cancelled
    thread_id: Optional[int] = Field(default=None, foreign_key="thread.id")
    alert_id: Optional[int] = Field(default=None, foreign_key="alert.id")
    result: str = ""
    max_turns: int = 12
    turns_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    transcript: list = _json(list)  # persisted Messages API history -> resumable
    inbox: list = _json(list)       # directly-delivered events waiting for the next turn
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_progress_at: datetime = Field(default_factory=utcnow)


class Subscription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: int = Field(foreign_key="thread.id", index=True)
    work_item_id: int = Field(foreign_key="workitem.id", index=True)
    active: bool = True


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    role: str  # owner | manager | system
    content: str
    kind: str = "text"  # text | alert | approval | report | activity
    meta: dict = _json(dict)
    created_at: datetime = Field(default_factory=utcnow)


class AuthzPolicy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    action: str
    mode: str  # off | observe | auto | approval


class ActionLog(SQLModel, table=True):
    """Audit trail + observe log + approval queue for every side-effecting action."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    actor: str  # router | worker:<name>#<id> | owner
    action: str
    params: dict = _json(dict)
    mode: str  # auto | observe | approval | off
    status: str  # executed | observed | pending_approval | approved | rejected | blocked | failed
    reason: str = ""
    result: str = ""
    work_item_id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: Optional[datetime] = None
