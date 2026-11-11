import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from config import ADMIN_API_KEY
from db import get_db
from models import Agent

# auto_error=False lets us return our own clear 401 message.
# These also add an "Authorize" button to /docs, handy for your demo.
agent_key_header = APIKeyHeader(name="X-Agent-Key", auto_error=False)
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def generate_api_key() -> str:
    """Make a new random key. Show it to the user ONCE, then store only its hash."""
    return "aw_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """SHA-256 is fine here because the key is long and random (unlike a password)."""
    return hashlib.sha256(key.encode()).hexdigest()


def get_current_agent(
    api_key: str = Security(agent_key_header),
    db: Session = Depends(get_db),
) -> Agent:
    """Dependency: use it in an endpoint to get the calling agent.
    Frozen agents still pass here on purpose. The policy engine denies them
    with AGENT_FROZEN, so the attempt is logged instead of vanishing."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Key header")

    agent = db.query(Agent).filter(Agent.api_key_hash == hash_api_key(api_key)).first()
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return agent


def require_admin(api_key: str = Security(admin_key_header)) -> None:
    """Dependency for dashboard-only actions (approve, reject, freeze, edit policy)."""
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
    # compare_digest takes the same time however many characters match,
    # so attackers can't guess the key one character at a time.
    if not api_key or not hmac.compare_digest(api_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid admin key")