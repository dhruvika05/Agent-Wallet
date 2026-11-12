from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_agent
from db import get_db
from models import (
    Agent, AgentStatus, AuditLog, PaymentRequest, RequestStatus, utcnow,
)
from paypal_client import PayPalError, send_payout
from policy_engine import History, Limits, Outcome, PaymentInput, evaluate
from schemas import PaymentRequestIn, PaymentRequestOut

router = APIRouter(prefix="/v1", tags=["payments"])

# Money in these states counts against the daily/weekly budget.
COMMITTED = (RequestStatus.APPROVED, RequestStatus.PROCESSING, RequestStatus.PAID)

STATUS_FOR_OUTCOME = {
    Outcome.ALLOW: RequestStatus.PROCESSING,
    Outcome.DENY: RequestStatus.DENIED,
    Outcome.NEEDS_APPROVAL: RequestStatus.PENDING_APPROVAL,
}

DECISION_FOR_STATUS = {
    RequestStatus.DENIED: "DENY",
    RequestStatus.PENDING_APPROVAL: "NEEDS_APPROVAL",
}

EXPLANATIONS = {
    "WITHIN_POLICY": "Within all limits, and the recipient is approved.",
    "AGENT_FROZEN": "Blocked: this agent has been frozen by its owner.",
    "INVALID_REQUEST": "Blocked: the amount or currency is invalid.",
    "RECIPIENT_NOT_ALLOWED": "Blocked: the recipient is not on this agent's approved list.",
    "OVER_TRANSACTION_LIMIT": "Blocked: the amount is above the per-payment limit.",
    "TOO_MANY_REQUESTS": "Blocked: too many requests in the last hour.",
    "OVER_DAILY_LIMIT": "Blocked: this would exceed the daily budget.",
    "OVER_WEEKLY_LIMIT": "Blocked: this would exceed the weekly budget.",
    "POSSIBLE_DUPLICATE": "Held for approval: a very similar payment was requested in the last 10 minutes.",
    "ABOVE_APPROVAL_THRESHOLD": "Held for approval: the amount is above the auto-approve threshold.",
    "UNUSUAL_AMOUNT": "Held for approval: the amount is much higher than usual for this recipient.",
}


def log(db: Session, event: str, agent_id: int, request_id: int = None, **details):
    """Add one audit entry. Details must be JSON-friendly, so use strings."""
    db.add(AuditLog(event=event, agent_id=agent_id, request_id=request_id, details_json=details))


def to_out(req: PaymentRequest, replayed: bool = False) -> PaymentRequestOut:
    return PaymentRequestOut(
        id=req.id,
        status=req.status,
        decision=DECISION_FOR_STATUS.get(req.status, "ALLOW"),
        reason_code=req.decision_reason_code,
        explanation=req.decision_explanation,
        recipient_email=req.recipient_email,
        amount=req.amount,
        currency=req.currency,
        paypal_batch_id=req.paypal_batch_id,
        created_at=req.created_at,
        replayed=replayed,
    )


def find_existing(db: Session, agent_id: int, key: str):
    return (
        db.query(PaymentRequest)
        .filter(PaymentRequest.agent_id == agent_id, PaymentRequest.idempotency_key == key)
        .first()
    )


def replay(existing: PaymentRequest, body: PaymentRequestIn) -> PaymentRequestOut:
    """Same key again: return the original answer, never pay twice.
    If the key is reused with DIFFERENT details, that's a bug or an attack."""
    if existing.recipient_email != body.recipient_email or existing.amount != body.amount:
        raise HTTPException(409, "idempotency_key was already used with different details")
    return to_out(existing, replayed=True)


def to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def payout_ref(req: PaymentRequest) -> str:
    """Stable for one request, but different after a database reset."""
    return f"{int(req.created_at.timestamp())}-{req.id}"

def build_history(db: Session, agent_id: int, email: str, amount: Decimal) -> History:
    """Ask the database the questions the policy engine needs answered."""
    now = utcnow()
    hour_ago = now - timedelta(hours=1)
    ten_min_ago = now - timedelta(minutes=10)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)  # UTC midnight
    week_ago = now - timedelta(days=7)

    mine = db.query(PaymentRequest).filter(PaymentRequest.agent_id == agent_id)

    requests_last_hour = mine.filter(PaymentRequest.created_at >= hour_ago).count()

    def total_since(since):
        return to_decimal(
            db.query(func.sum(PaymentRequest.amount))
            .filter(
                PaymentRequest.agent_id == agent_id,
                PaymentRequest.status.in_(COMMITTED),
                PaymentRequest.created_at >= since,
            )
            .scalar()
        )

    duplicate = (
        mine.filter(
            PaymentRequest.recipient_email == email,
            PaymentRequest.amount == amount,
            PaymentRequest.status != RequestStatus.DENIED,
            PaymentRequest.created_at >= ten_min_ago,
        ).first()
        is not None
    )

    count, avg = (
        db.query(func.count(PaymentRequest.id), func.avg(PaymentRequest.amount))
        .filter(
            PaymentRequest.agent_id == agent_id,
            PaymentRequest.recipient_email == email,
            PaymentRequest.status.in_((RequestStatus.PROCESSING, RequestStatus.PAID)),
        )
        .one()
    )

    return History(
        requests_last_hour=requests_last_hour,
        paid_today=total_since(day_start),
        paid_this_week=total_since(week_ago),
        same_payment_last_10_min=duplicate,
        prior_payments_to_recipient=count or 0,
        avg_amount_to_recipient=to_decimal(avg) if count else None,
    )


@router.post("/payment-requests", response_model=PaymentRequestOut)
async def create_payment_request(
    body: PaymentRequestIn,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    # 1. Idempotency: seen this key before?
    existing = find_existing(db, agent.id, body.idempotency_key)
    if existing:
        return replay(existing, body)

    # 2. An agent needs a policy before it can do anything
    policy = agent.policy
    if policy is None:
        raise HTTPException(409, "This agent has no policy configured")

    limits = Limits(
        per_transaction_limit=policy.per_transaction_limit,
        daily_limit=policy.daily_limit,
        weekly_limit=policy.weekly_limit,
        approval_threshold=policy.approval_threshold,
        max_requests_per_hour=policy.max_requests_per_hour,
        currency=policy.currency,
    )
    allowed = {r.paypal_email for r in agent.recipients}
    history = build_history(db, agent.id, body.recipient_email, body.amount)

    # 3. THE DECISION. Note that body.purpose is not passed in.
    decision = evaluate(
        PaymentInput(body.recipient_email, body.amount, body.currency),
        agent.status == AgentStatus.FROZEN,
        limits,
        allowed,
        history,
    )

    # 4. Save the request with its final status
    req = PaymentRequest(
        agent_id=agent.id,
        recipient_email=body.recipient_email,
        amount=body.amount,
        currency=body.currency,
        purpose=body.purpose,
        idempotency_key=body.idempotency_key,
        status=STATUS_FOR_OUTCOME[decision.outcome],
        decision_reason_code=decision.reason_code,
        decision_explanation=EXPLANATIONS.get(decision.reason_code, decision.reason_code),
    )
    db.add(req)
    try:
        db.flush()  # assigns req.id; fails here if two identical requests race
    except IntegrityError:
        db.rollback()
        existing = find_existing(db, agent.id, body.idempotency_key)
        if existing:
            return replay(existing, body)
        raise

    log(db, "REQUEST_RECEIVED", agent.id, req.id,
        recipient=req.recipient_email, amount=str(req.amount), currency=req.currency)
    log(db, "POLICY_CHECKED", agent.id, req.id,
        outcome=decision.outcome.value, reason_code=decision.reason_code,
        **{k: str(v) for k, v in decision.details.items()})
    db.commit()  # saved BEFORE we touch PayPal, so a crash never loses the record

    # 5. Only ALLOW reaches PayPal
    if decision.outcome == Outcome.ALLOW:
        try:
            result = await send_payout(
                request_id=payout_ref(req),   
                receiver_email=req.recipient_email,
                amount=req.amount,
                currency=req.currency,
                note=f"Agent Wallet request #{req.id}",  # fixed text, not agent-written
            )
            req.paypal_batch_id = result["batch_id"]
            log(db, "PAYPAL_SENT", agent.id, req.id,
                batch_id=str(result["batch_id"]), batch_status=str(result["batch_status"]))
        except PayPalError as e:
            req.status = RequestStatus.FAILED
            log(db, "PAYPAL_FAILED", agent.id, req.id,
                http_status=str(e.status_code), body=str(e.body)[:500])
        except httpx.HTTPError as e:
            # Timeout or network error: the payout MAY have gone through.
            # Leave status as PROCESSING and let a status check settle it.
            log(db, "PAYPAL_NO_RESPONSE", agent.id, req.id, error=type(e).__name__)
        db.commit()

    db.refresh(req)
    return to_out(req)