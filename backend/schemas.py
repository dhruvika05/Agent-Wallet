from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PaymentRequestIn(BaseModel):
    """What an agent sends us. FastAPI rejects bad shapes with a 422 automatically."""

    recipient_email: str = Field(min_length=3, max_length=254)
    # Decimal, never float. gt=0 rejects zero and negatives.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    purpose: str = Field(min_length=1, max_length=500)  # shown to humans only
    idempotency_key: str = Field(min_length=1, max_length=100)

    @field_validator("recipient_email")
    @classmethod
    def clean_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("recipient_email must be an email address")
        return v

    @field_validator("currency")
    @classmethod
    def clean_currency(cls, v: str) -> str:
        return v.strip().upper()


class PaymentRequestOut(BaseModel):
    """What we send back."""

    id: int
    status: str                 # DENIED, PENDING_APPROVAL, PROCESSING, PAID, FAILED
    decision: str               # ALLOW, DENY, NEEDS_APPROVAL
    reason_code: Optional[str]
    explanation: Optional[str]
    recipient_email: str
    amount: Decimal
    currency: str
    paypal_batch_id: Optional[str] = None
    created_at: datetime
    replayed: bool = False      # true if this is the answer to a repeated idempotency key