import os
from decimal import Decimal
from pathlib import Path
import uuid

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).parent / ".env")

WALLET_URL = os.getenv("WALLET_URL", "http://localhost:8000")
AGENT_KEY = os.getenv("AGENT_KEY")
MODEL = os.getenv("GEMINI_MODEL")

SYSTEM_PROMPT = (
    "You are an office procurement agent for a small company. "
    "You buy supplies by calling the request_payment tool. "
    "If a payment is denied or held for approval, tell the user what happened "
    "and why. Do not retry the same payment or try to work around the result."
)


def request_payment(recipient_email: str, amount: float, purpose: str) -> dict:
    """Ask the Agent Wallet to pay a vendor.

    Args:
        recipient_email: PayPal email of the vendor to pay.
        amount: Amount in USD.
        purpose: Short reason for the payment.
    """
    body = {
        "recipient_email": recipient_email,
        # sent as a string so floats never touch money
        "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
        "currency": "USD",
        "purpose": purpose,
        "idempotency_key": f"agent-{uuid.uuid4()}",
    }
    r = httpx.post(
        f"{WALLET_URL}/v1/payment-requests",
        json=body,
        headers={"X-Agent-Key": AGENT_KEY},
        timeout=30,
    )
    if r.status_code != 200:
        result = {"error": r.status_code, "detail": r.text[:300]}
    else:
        d = r.json()
        result = {
            "status": d["status"],
            "decision": d["decision"],
            "reason_code": d["reason_code"],
            "explanation": d["explanation"],
        }
    print(f"   [wallet] ${body['amount']} -> {recipient_email}: "
          f"{result.get('decision', 'ERROR')} {result.get('reason_code', '')}")
    return result


def run_agent(task: str) -> str:
    """Give the LLM a task. It may call request_payment as many times as it wants."""
    client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=30000),
)
    response = client.models.generate_content(
        model=MODEL,
        contents=task,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[request_payment],  # the SDK calls the function for the model automatically
        ),
    )
    return response.text