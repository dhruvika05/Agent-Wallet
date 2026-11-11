from dataclasses import replace
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import require_admin
from db import get_db
from models import Agent, AgentStatus, PaymentRequest, RequestStatus, utcnow
from payments import build_history, log, to_out
from paypal_client import PayPalError, get_payout_batch, send_payout
from policy_engine import Limits, Outcome, PaymentInput, evaluate
from schemas import PaymentRequestOut

# Every route in this file needs the admin key.
router = APIRouter(prefix="/v1", tags=["admin"], dependencies=[Depends(require_admin)])

ADMIN_ACTOR = "admin-key"  # no user accounts yet; a real system would log a person here


class RejectIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=300)


def get_request_or_404(db: Session, request_id: int) -> PaymentRequest:
    req = db.get(PaymentRequest, request_id)
    if req is None:
        raise HTTPException(404, "Payment request not found")
    return req


def claim(db: Session, request_id: int, from_status: str, to_status: str) -> bool:
    """Atomically change status only if it is still `from_status`.
    If two admins (or a double-click) hit approve at once, only one gets True."""
    rows = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.id == request_id, PaymentRequest.status == from_status)
        .update({"status": to_status, "updated_at": utcnow()}, synchronize_session=False)
    )
    db.commit()
    return rows == 1


@router.post("/payment-requests/{request_id}/approve", response_model=PaymentRequestOut)
async def approve(request_id: int, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    if req.status != RequestStatus.PENDING_APPROVAL:
        raise HTTPException(409, f"Only pending requests can be approved (this one is {req.status})")

    agent = req.agent
    policy = agent.policy
    if policy is None:
        raise HTTPException(409, "This agent has no policy configured")

    # Re-run the HARD rules with fresh numbers. We switch off the "ask a human"
    # rules because a human is exactly what is happening now:
    #  - approval_threshold = per_transaction_limit, so it can never trigger
    #  - duplicate / unusual-amount / rate-limit history is zeroed out
    limits = Limits(
        per_transaction_limit=policy.per_transaction_limit,
        daily_limit=policy.daily_limit,
        weekly_limit=policy.weekly_limit,
        approval_threshold=policy.per_transaction_limit,
        max_requests_per_hour=policy.max_requests_per_hour,
        currency=policy.currency,
    )
    history = replace(
        build_history(db, agent.id, req.recipient_email, req.amount),
        requests_last_hour=0,
        same_payment_last_10_min=False,
        prior_payments_to_recipient=0,
        avg_amount_to_recipient=None,
    )
    recheck = evaluate(
        PaymentInput(req.recipient_email, req.amount, req.currency),
        agent.status == AgentStatus.FROZEN,
        limits,
        {r.paypal_email for r in agent.recipients},
        history,
    )

    if recheck.outcome != Outcome.ALLOW:
        req.status = RequestStatus.DENIED
        req.decision_reason_code = recheck.reason_code
        req.decision_explanation = f"Approval blocked at re-check: {recheck.reason_code}"
        log(db, "APPROVAL_BLOCKED", agent.id, req.id,
            reason_code=recheck.reason_code, actor=ADMIN_ACTOR)
        db.commit()
        raise HTTPException(409, f"Cannot approve: {recheck.reason_code}")

    # Claim it. If someone else got there first, stop.
    if not claim(db, req.id, RequestStatus.PENDING_APPROVAL, RequestStatus.PROCESSING):
        raise HTTPException(409, "This request was already handled")

    db.refresh(req)
    req.decision_explanation = "Approved by a human after being held for review."
    log(db, "HUMAN_APPROVED", agent.id, req.id, actor=ADMIN_ACTOR)
    db.commit()  # saved BEFORE touching PayPal

    try:
        result = await send_payout(
            request_id=req.id,
            receiver_email=req.recipient_email,
            amount=req.amount,
            currency=req.currency,
            note=f"Agent Wallet request #{req.id}",
        )
        req.paypal_batch_id = result["batch_id"]
        log(db, "PAYPAL_SENT", agent.id, req.id,
            batch_id=str(result["batch_id"]), batch_status=str(result["batch_status"]))
    except PayPalError as e:
        req.status = RequestStatus.FAILED
        log(db, "PAYPAL_FAILED", agent.id, req.id,
            http_status=str(e.status_code), body=str(e.body)[:500])
    except httpx.HTTPError as e:
        # Timeout: the payout MAY have gone through. Stay PROCESSING.
        log(db, "PAYPAL_NO_RESPONSE", agent.id, req.id, error=type(e).__name__)
    db.commit()

    db.refresh(req)
    return to_out(req)


@router.post("/payment-requests/{request_id}/reject", response_model=PaymentRequestOut)
def reject(request_id: int, body: RejectIn = RejectIn(), db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)

    if not claim(db, req.id, RequestStatus.PENDING_APPROVAL, RequestStatus.DENIED):
        raise HTTPException(409, f"Only pending requests can be rejected (this one is {req.status})")

    db.refresh(req)
    req.decision_reason_code = "REJECTED_BY_ADMIN"
    req.decision_explanation = "Rejected by a human reviewer."
    log(db, "HUMAN_REJECTED", req.agent_id, req.id, actor=ADMIN_ACTOR, note=body.note or "")
    db.commit()

    db.refresh(req)
    return to_out(req)


def set_agent_status(db: Session, agent_id: int, new_status: str, event: str) -> dict:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    agent.status = new_status
    log(db, event, agent.id, actor=ADMIN_ACTOR)
    db.commit()
    return {"id": agent.id, "name": agent.name, "status": agent.status}


@router.post("/agents/{agent_id}/freeze")
def freeze(agent_id: int, db: Session = Depends(get_db)):
    return set_agent_status(db, agent_id, AgentStatus.FROZEN, "AGENT_FROZEN")


@router.post("/agents/{agent_id}/unfreeze")
def unfreeze(agent_id: int, db: Session = Depends(get_db)):
    return set_agent_status(db, agent_id, AgentStatus.ACTIVE, "AGENT_UNFROZEN")

# ---------- payout status refresh ----------
# Verify these status names in the PayPal Payouts docs. They are my best knowledge.
ITEM_PAID = {"SUCCESS"}
ITEM_FAILED = {"FAILED", "BLOCKED", "RETURNED", "REFUNDED", "REVERSED", "DENIED"}
BATCH_FAILED = {"DENIED", "CANCELED"}


def interpret(batch_status, item_status) -> Optional[str]:
    """Turn PayPal's statuses into ours. None means 'still waiting'
    (PENDING, ONHOLD, UNCLAIMED and similar stay PROCESSING)."""
    if item_status in ITEM_PAID:
        return RequestStatus.PAID
    if item_status in ITEM_FAILED or batch_status in BATCH_FAILED:
        return RequestStatus.FAILED
    return None


async def refresh_one(db: Session, req: PaymentRequest) -> PaymentRequest:
    if req.status != RequestStatus.PROCESSING or not req.paypal_batch_id:
        return req
    try:
        info = await get_payout_batch(req.paypal_batch_id)
    except PayPalError as e:
        log(db, "PAYPAL_STATUS_ERROR", req.agent_id, req.id,
            http_status=str(e.status_code), body=str(e.body)[:300])
        db.commit()
        return req
    except httpx.HTTPError as e:
        log(db, "PAYPAL_STATUS_ERROR", req.agent_id, req.id, error=type(e).__name__)
        db.commit()
        return req

    if info["item_id"]:
        req.paypal_item_id = info["item_id"]

    new_status = interpret(info["batch_status"], info["item_status"])
    if new_status and claim(db, req.id, RequestStatus.PROCESSING, new_status):
        db.refresh(req)
        log(db, "PAYOUT_PAID" if new_status == RequestStatus.PAID else "PAYOUT_FAILED",
            req.agent_id, req.id, batch_status=str(info["batch_status"]),
            item_status=str(info["item_status"]))
    db.commit()
    db.refresh(req)
    return req


@router.post("/payment-requests/{request_id}/refresh", response_model=PaymentRequestOut)
async def refresh_request(request_id: int, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    return to_out(await refresh_one(db, req))


@router.post("/payment-requests-refresh-all")
async def refresh_all(db: Session = Depends(get_db)):
    waiting = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.status == RequestStatus.PROCESSING,
                PaymentRequest.paypal_batch_id.isnot(None))
        .all()
    )
    paid = failed = 0
    for req in waiting:
        updated = await refresh_one(db, req)
        paid += updated.status == RequestStatus.PAID
        failed += updated.status == RequestStatus.FAILED
    return {"checked": len(waiting), "paid": paid, "failed": failed,
            "still_processing": len(waiting) - paid - failed}