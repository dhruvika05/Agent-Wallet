import os
from decimal import Decimal

from dotenv import load_dotenv

# Reads the .env file (if present) into environment variables.
# On Render you set env vars in the dashboard instead, and this does nothing.
load_dotenv()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")  # protects dashboard actions (approve, freeze, policy edits)
# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agent_wallet.db")

# PayPal (sandbox by default; never point this at live while building)
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID")  # needed on Day 3 for webhooks

# LLM
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5")  # change to whatever model you use

# CORS: the frontend URL allowed to call this API
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


def require_paypal_config() -> None:
    """Call this before any PayPal request so a missing key gives a clear error
    instead of a confusing 401 from PayPal."""
    missing = [
        name
        for name, value in [
            ("PAYPAL_CLIENT_ID", PAYPAL_CLIENT_ID),
            ("PAYPAL_CLIENT_SECRET", PAYPAL_CLIENT_SECRET),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")