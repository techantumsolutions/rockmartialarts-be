import asyncio
import os
from collections import Counter

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def main() -> None:
    db = AsyncIOMotorClient(os.getenv("MONGO_URI"))["marshalats"]
    types = Counter()
    async for p in db.payments.find({}).limit(800):
        types[str(p.get("payment_type") or "none")] += 1
    print("payment_types", types.most_common(30))

    coach_pays = await db.payments.find(
        {"payment_type": {"$in": ["coach_registration", "coach_subscription", "subscription"]}}
    ).to_list(50)
    print("coach-like payments", len(coach_pays))
    for p in coach_pays[:5]:
        print(
            {
                k: p.get(k)
                for k in [
                    "id",
                    "payment_type",
                    "payment_method",
                    "plan_id",
                    "invoice_id",
                    "amount",
                    "coach_id",
                ]
            }
        )

    # invoices linked via payment
    inv_with_pay = 0
    async for inv in db.invoices.find({"payment_id": {"$exists": True}}):
        inv_with_pay += 1
        pay = await db.payments.find_one({"id": inv.get("payment_id")})
        if pay:
            print(
                "inv",
                inv.get("invoice_number"),
                "pay_type",
                pay.get("payment_type"),
                "method",
                inv.get("payment_method"),
                pay.get("payment_method"),
            )


if __name__ == "__main__":
    asyncio.run(main())
