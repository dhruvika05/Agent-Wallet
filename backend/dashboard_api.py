from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from admin import ADMIN_ACTOR
from auth import generate_api_key, hash_api_key, require_admin
from db import get_db
from models import Agent, AuditLog, PaymentRequest, Policy, Recipient, RequestStatus, utcnow
from payments import COMMITTED, log, to_decimal

router = APIRouter(prefix="/v1", tags=["dashboard"], dependencies=[Depends(require_admin)])

Money = Decimal


# ---------- shapes ----------
class PolicyIn(BaseModel):
    per_transaction_limit: Money = Field(ge=0, max_digits=12, decimal_places=2)
    daily_limit: Money = Field(ge=0, max_digits=12, decimal_places=2)
    weekly_limit: Money = Field(ge=0, max_digits=12, decimal_places=2)
    approval_threshold: Money = Field(ge=0, max_digits=12, decimal_places=2)
    max_requests_per_hour: int = Field(gt=0, le=1000)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def upper(cls, v):
        return v.strip().upper()

    @model_validator(mode="after")
    def limits_make_sense(self):
        if self.approval_threshold > self.per_transaction_limit:
            raise ValueError("approval_threshold cannot exceed per_transaction_limit")
        if self.per_transaction_limit > self.daily_limit:
            raise ValueError("per_transaction_limit cannot exceed daily_limit")
        if self.daily_limit > self.weekly_limit:
            raise ValueError("daily_limit cannot exceed weekly_limit")
        return self


class PolicyOut(PolicyIn):
    model_config = ConfigDict(from_attributes=True)


class RecipientIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    paypal_email: str = Field(min_length=3, max_length=254)
    category: Optional[str] = Field(default=None, max_length=50)

    @field_validator("paypal_email")
    @classmethod
    def clean(cls, v):
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("paypal_email must be an email address")
        return v


class RecipientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    paypal_email: str
    category: Optional[str]


class AgentOut(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    policy: Optional[PolicyOut]
    spent_today: Money
    spent_this_week: Money
    pending_count: int


class AgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    policy: PolicyIn


class AgentCreatedOut(BaseModel):
    id: int
    name: str
    api_key: str  # shown ONCE


class RequestRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_id: int
    recipient_email: str
    amount: Money
    currency: str
    purpose: str
    status: str
    decision_reason_code: Optional[str]
    decision_explanation: Optional[str]
    paypal_batch_id: Optional[str]
    created_at: datetime


class AuditRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    request_id: Optional[int]
    agent_id: Optional[int]
    event: str
    details_json: Optional[dict]
    timestamp: datetime


# ---------- helpers ----------
def get_agent_or_404(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


def spent_since(db: Session, agent_id: int, since: datetime) -> Decimal:
    return to_decimal(
        db.query(func.sum(PaymentRequest.amount))
        .filter(
            PaymentRequest.agent_id == agent_id,
            PaymentRequest.status.in_(COMMITTED),
            PaymentRequest.created_at >= since,
        )
        .scalar()
    )


def agent_summary(db: Session, agent: Agent) -> AgentOut:
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    pending = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.agent_id == agent.id,
                PaymentRequest.status == RequestStatus.PENDING_APPROVAL)
        .count()
    )
    from datetime import timedelta
    return AgentOut(
        id=agent.id, name=agent.name, status=agent.status, created_at=agent.created_at,
        policy=PolicyOut.model_validate(agent.policy) if agent.policy else None,
        spent_today=spent_since(db, agent.id, day_start),
        spent_this_week=spent_since(db, agent.id, now - timedelta(days=7)),
        pending_count=pending,
    )


# ---------- agents ----------
@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    return [agent_summary(db, a) for a in db.query(Agent).order_by(Agent.id).all()]


@router.post("/agents", response_model=AgentCreatedOut)
def create_agent(body: AgentCreateIn, db: Session = Depends(get_db)):
    key = generate_api_key()
    agent = Agent(name=body.name, api_key_hash=hash_api_key(key))
    agent.policy = Policy(**body.policy.model_dump())
    db.add(agent)
    db.flush()
    log(db, "AGENT_CREATED", agent.id, actor=ADMIN_ACTOR, name=body.name)
    db.commit()
    return AgentCreatedOut(id=agent.id, name=agent.name, api_key=key)


# ---------- policy ----------
@router.get("/agents/{agent_id}/policy", response_model=PolicyOut)
def get_policy(agent_id: int, db: Session = Depends(get_db)):
    agent = get_agent_or_404(db, agent_id)
    if agent.policy is None:
        raise HTTPException(404, "This agent has no policy")
    return agent.policy


@router.put("/agents/{agent_id}/policy", response_model=PolicyOut)
def update_policy(agent_id: int, body: PolicyIn, db: Session = Depends(get_db)):
    agent = get_agent_or_404(db, agent_id)
    before = None
    if agent.policy is None:
        agent.policy = Policy(**body.model_dump())
    else:
        before = PolicyOut.model_validate(agent.policy).model_dump(mode="json")
        for key, value in body.model_dump().items():
            setattr(agent.policy, key, value)
    log(db, "POLICY_UPDATED", agent.id, actor=ADMIN_ACTOR,
        before=before, after=body.model_dump(mode="json"))
    db.commit()
    db.refresh(agent.policy)
    return agent.policy


# ---------- recipients (allowlist) ----------
@router.get("/agents/{agent_id}/recipients", response_model=list[RecipientOut])
def list_recipients(agent_id: int, db: Session = Depends(get_db)):
    return get_agent_or_404(db, agent_id).recipients


@router.post("/agents/{agent_id}/recipients", response_model=RecipientOut)
def add_recipient(agent_id: int, body: RecipientIn, db: Session = Depends(get_db)):
    agent = get_agent_or_404(db, agent_id)
    rec = Recipient(agent_id=agent.id, name=body.name,
                    paypal_email=body.paypal_email, category=body.category)
    db.add(rec)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That recipient is already on this agent's list")
    log(db, "RECIPIENT_ADDED", agent.id, actor=ADMIN_ACTOR, email=rec.paypal_email)
    db.commit()
    db.refresh(rec)
    return rec


@router.delete("/agents/{agent_id}/recipients/{recipient_id}")
def remove_recipient(agent_id: int, recipient_id: int, db: Session = Depends(get_db)):
    rec = (
        db.query(Recipient)
        .filter(Recipient.id == recipient_id, Recipient.agent_id == agent_id)
        .first()
    )
    if rec is None:
        raise HTTPException(404, "Recipient not found")
    email = rec.paypal_email
    db.delete(rec)
    log(db, "RECIPIENT_REMOVED", agent_id, actor=ADMIN_ACTOR, email=email)
    db.commit()
    return {"removed": email}


# ---------- requests and audit log ----------
@router.get("/payment-requests", response_model=list[RequestRow])
def list_requests(
    status: Optional[str] = None,
    agent_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(PaymentRequest)
    if status:
        q = q.filter(PaymentRequest.status == status.upper())
    if agent_id:
        q = q.filter(PaymentRequest.agent_id == agent_id)
    return q.order_by(PaymentRequest.id.desc()).limit(limit).all()


@router.get("/audit-log", response_model=list[AuditRow])
def list_audit(
    request_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog)
    if request_id:
        q = q.filter(AuditLog.request_id == request_id)
    if agent_id:
        q = q.filter(AuditLog.agent_id == agent_id)
    return q.order_by(AuditLog.id.desc()).limit(limit).all()