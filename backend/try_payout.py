import asyncio
from decimal import Decimal

from paypal_client import send_payout


async def main():
    result = await send_payout(
        request_id=999001,
        receiver_email="sb-nu5x753751645@personal.example.com",
        amount=Decimal("5.00"),
    )

    print("Batch ID:", result["batch_id"])
    print("Batch Status:", result["batch_status"])


asyncio.run(main())