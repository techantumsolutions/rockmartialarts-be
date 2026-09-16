"""Discount rule indexes (M05-S04)."""
import logging


async def ensure_discount_rule_indexes(mongo_db) -> None:
    try:
        await mongo_db.discount_rules.create_index("code", unique=True, name="discount_rules_code")
    except Exception:
        logging.exception("Failed to create discount_rules.code index")
    try:
        await mongo_db.discount_rules.create_index(
            [("is_active", 1), ("priority", 1)],
            name="discount_rules_active_priority",
        )
    except Exception:
        logging.exception("Failed to create discount_rules active index")
