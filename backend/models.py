from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    JSON,
    UniqueConstraint,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import relationship, validates

from db import Base


def utcnow() -> datetime:
    """Current UTC time. (datetime.utcnow() is deprecated in newer Python.)
    Stored without tzinfo because SQLite doesn't keep timezones."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Status constants: use these everywhere instead of typing strings by hand,
# so a typo like "PENDING_APROVAL" can't silently break the policy engine.
class AgentStatus:
    ACTIVE = "active"
    FROZEN = "frozen"


class RequestStatus:
    DENIED = "DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    PAID = "PAID"
    FAILED = "FAILED"


# 1. AGENTS
class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    api_key_hash = Column(String, nullable=False, unique=True)
    status = Column(String, default=AgentStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    policy = relationship(
        "Policy", back_populates="agent", uselist=False,
        cascade="all, delete-orphan",
    )
    recipients = relationship(
        "Recipient", back_populates="agent", cascade="all, delete-orphan",
    )
    payment_requests = relationship("PaymentRequest", back_populates="agent")


# 2. POLICIES
class Policy(Base):
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), unique=True, nullable=False)

    per_transaction_limit = Column(Numeric(12, 2), nullable=False)
    daily_limit = Column(Numeric(12, 2), nullable=False)
    weekly_limit = Column(Numeric(12, 2), nullable=False)
    approval_threshold = Column(Numeric(12, 2), nullable=False)
    max_requests_per_hour = Column(Integer, nullable=False)
    currency = Column(String, default="USD", nullable=False)

    agent = relationship("Agent", back_populates="policy")

    # The database itself refuses nonsense limits.
    __table_args__ = (
        CheckConstraint("per_transaction_limit >= 0", name="ck_policy_txn_limit"),
        CheckConstraint("daily_limit >= 0", name="ck_policy_daily_limit"),
        CheckConstraint("weekly_limit >= 0", name="ck_policy_weekly_limit"),
        CheckConstraint("approval_threshold >= 0", name="ck_policy_threshold"),
        CheckConstraint("max_requests_per_hour > 0", name="ck_policy_rate"),
    )


# 3. RECIPIENTS (the allowlist)
class Recipient(Base):
    __tablename__ = "recipients"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    name = Column(String, nullable=False)
    paypal_email = Column(String, nullable=False)
    category = Column(String)

    agent = relationship("Agent", back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("agent_id", "paypal_email", name="unique_agent_recipient"),
    )

    @validates("paypal_email")
    def _normalize_email(self, key, value):
        # "Vendor@Example.com" and "vendor@example.com" must count as the same person.
        return value.strip().lower()


# 4. PAYMENT REQUESTS
class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)

    recipient_email = Column(String, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String, nullable=False)
    purpose = Column(Text, nullable=False)  # shown to humans, NEVER used for decisions
    idempotency_key = Column(String, nullable=False)

    status = Column(String, nullable=False)
    decision_reason_code = Column(String)
    decision_explanation = Column(Text)

    paypal_batch_id = Column(String)
    paypal_item_id = Column(String)  # webhooks refer to the payout item; check PayPal docs for the exact ID

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    agent = relationship("Agent", back_populates="payment_requests")
    # No delete cascade: an audit trail must survive even if a request is removed.
    audit_logs = relationship(
        "AuditLog", back_populates="payment_request",
        order_by="AuditLog.timestamp",
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "idempotency_key", name="unique_agent_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_request_amount_positive"),
        # The policy engine constantly asks "what did this agent request recently?"
        Index("ix_requests_agent_created", "agent_id", "created_at"),
    )

    @validates("recipient_email")
    def _normalize_email(self, key, value):
        return value.strip().lower()


# 5. AUDIT LOG (append-only: only ever insert, never update or delete)
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)

    # Both optional: some events have no payment request
    # (for example "AGENT_FROZEN", "POLICY_UPDATED", or a request with a bad API key).
    request_id = Column(Integer, ForeignKey("payment_requests.id"), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)

    event = Column(String, nullable=False)
    details_json = Column(JSON)
    timestamp = Column(DateTime, default=utcnow, index=True)

    payment_request = relationship("PaymentRequest", back_populates="audit_logs")