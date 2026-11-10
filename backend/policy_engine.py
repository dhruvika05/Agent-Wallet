from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass(frozen=True)
class Limits:
    per_transaction_limit: Decimal
    daily_limit: Decimal
    weekly_limit: Decimal
    approval_threshold: Decimal
    max_requests_per_hour: int
    currency: str = "USD"


@dataclass(frozen=True)
class PaymentInput:
    recipient_email: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class History:
    """Facts about the past. The API layer fills this in from the database."""
    requests_last_hour: int = 0                 # ALL requests, including denied ones
    paid_today: Decimal = Decimal("0")          # money already paid or committed today
    paid_this_week: Decimal = Decimal("0")
    same_payment_last_10_min: bool = False      # same recipient + same amount
    prior_payments_to_recipient: int = 0
    avg_amount_to_recipient: Optional[Decimal] = None


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason_code: str
    details: dict = field(default_factory=dict)


ANOMALY_MULTIPLIER = Decimal("3")


def evaluate(
    payment: PaymentInput,
    agent_frozen: bool,
    limits: Limits,
    allowed_emails: set,
    history: History,
) -> Decision:
    """Checks run in a fixed order. The first failure decides the outcome.
    NOTE: the free-text 'purpose' is deliberately NOT an input. Text can't
    influence the decision, which is what makes prompt injection harmless here."""

    def deny(code, **details):
        return Decision(Outcome.DENY, code, details)

    def ask(code, **details):
        return Decision(Outcome.NEEDS_APPROVAL, code, details)

    # 1. Kill switch
    if agent_frozen:
        return deny("AGENT_FROZEN")

    # 2. Basic sanity
    amount = payment.amount
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount <= 0
        or payment.currency.upper() != limits.currency.upper()
    ):
        return deny("INVALID_REQUEST")

    # 3. Allowlist (compare lowercase so Vendor@X.com == vendor@x.com)
    allowed = {e.strip().lower() for e in allowed_emails}
    if payment.recipient_email.strip().lower() not in allowed:
        return deny("RECIPIENT_NOT_ALLOWED")

    # 4. Per-transaction cap
    if amount > limits.per_transaction_limit:
        return deny("OVER_TRANSACTION_LIMIT", limit=str(limits.per_transaction_limit))

    # 5. Rate limit (stops loops)
    if history.requests_last_hour >= limits.max_requests_per_hour:
        return deny("TOO_MANY_REQUESTS", limit=limits.max_requests_per_hour)

    # 6. Daily budget
    if history.paid_today + amount > limits.daily_limit:
        return deny("OVER_DAILY_LIMIT", paid_today=str(history.paid_today))

    # 7. Weekly budget
    if history.paid_this_week + amount > limits.weekly_limit:
        return deny("OVER_WEEKLY_LIMIT", paid_this_week=str(history.paid_this_week))

    # 8. Possible duplicate: ask a human
    if history.same_payment_last_10_min:
        return ask("POSSIBLE_DUPLICATE")

    # 9. Above the human-approval threshold
    if amount > limits.approval_threshold:
        return ask("ABOVE_APPROVAL_THRESHOLD", threshold=str(limits.approval_threshold))

    # 10. Unusual amount for this recipient (only if we have history)
    if (
        history.prior_payments_to_recipient > 0
        and history.avg_amount_to_recipient is not None
        and amount > history.avg_amount_to_recipient * ANOMALY_MULTIPLIER
    ):
        return ask("UNUSUAL_AMOUNT", average=str(history.avg_amount_to_recipient))

    # 11. All clear
    return Decision(Outcome.ALLOW, "WITHIN_POLICY")