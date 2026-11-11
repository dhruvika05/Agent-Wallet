import sys
from decimal import Decimal

from auth import generate_api_key, hash_api_key
from db import SessionLocal, init_db
from models import Agent, Policy, Recipient


def main(vendor_email: str):
    init_db()
    db = SessionLocal()
    try:
        if db.query(Agent).filter(Agent.name == "Office Procurement Agent").first():
            print("Demo agent already exists. Delete agent_wallet.db to start fresh.")
            return

        key = generate_api_key()
        agent = Agent(name="Office Procurement Agent", api_key_hash=hash_api_key(key))
        agent.policy = Policy(
            per_transaction_limit=Decimal("150"),
            daily_limit=Decimal("300"),
            weekly_limit=Decimal("1000"),
            approval_threshold=Decimal("100"),
            max_requests_per_hour=10,
            currency="USD",
        )
        agent.recipients = [
            Recipient(name="Paper Vendor", paypal_email=vendor_email, category="supplies"),
        ]
        db.add(agent)
        db.commit()
        print("Agent created. SAVE THIS KEY, it is shown only once:")
        print(key)
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python seed.py your-sandbox-vendor@example.com")
    main(sys.argv[1])