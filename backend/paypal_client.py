import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

import httpx

from config import (
    PAYPAL_BASE_URL,
    PAYPAL_CLIENT_ID,
    PAYPAL_CLIENT_SECRET,
    require_paypal_config,
)

BASE = PAYPAL_BASE_URL.rstrip("/")
TIMEOUT = httpx.Timeout(20.0)

# Simple in-memory token cache
_token = {"value": None, "expires_at": 0.0}


class PayPalError(Exception):
    """Raised when PayPal rejects a request. Carries PayPal's own error body."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"PayPal error {status_code}: {body}")


async def get_access_token() -> str:
    """Get an OAuth token from PayPal, reusing it until shortly before expiry."""
    require_paypal_config()

    if _token["value"] and time.time() < _token["expires_at"]:
        return _token["value"]

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{BASE}/v1/oauth2/token",
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )

    if response.status_code != 200:
        raise PayPalError(response.status_code, response.text)

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("PayPal did not return an access token.")

    # Refresh 60 seconds early to be safe
    _token["value"] = token
    _token["expires_at"] = time.time() + int(data.get("expires_in", 300)) - 60
    return token


async def send_payout(
    request_id: int,
    receiver_email: str,
    amount: Decimal,
    currency: str = "USD",
    note: str = "Agent Wallet payout",
) -> dict:
    """Send ONE payout. request_id is the payment_requests.id from your database.

    Using request_id makes retries safe: the same request always produces the
    same batch ID, so PayPal won't pay twice.
    """
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal, never a float")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Payout amount must be positive and finite.")

    # PayPal wants a string with 2 decimal places, e.g. "40.00"
    amount_str = str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    access_token = await get_access_token()

    batch_id = f"aw-req-{request_id}"  # stable: same request -> same ID

    payload = {
        "sender_batch_header": {
            "sender_batch_id": batch_id,
            "email_subject": "You have received a payment",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver_email,
                "amount": {"value": amount_str, "currency": currency},
                "note": note,
                "sender_item_id": batch_id,
            }
        ],
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{BASE}/v1/payments/payouts",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": batch_id,  # PayPal's duplicate protection
            },
        )

    if response.status_code not in (200, 201):
        raise PayPalError(response.status_code, response.text)

    data = response.json()
    header = data.get("batch_header", {})
    return {
        "batch_id": header.get("payout_batch_id"),
        "batch_status": header.get("batch_status"),  # usually PENDING at first
        "raw": data,
    }

async def get_payout_batch(batch_id: str) -> dict:
    """Ask PayPal for the current status of a payout batch (we send one item per batch)."""
    token = await get_access_token()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(
            f"{BASE}/v1/payments/payouts/{batch_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code != 200:
        raise PayPalError(response.status_code, response.text)

    data = response.json()
    items = data.get("items") or []
    item = items[0] if items else {}
    return {
        "batch_status": data.get("batch_header", {}).get("batch_status"),
        "item_status": item.get("transaction_status"),
        "item_id": item.get("payout_item_id"),
    }