"""Assign unclassified Martial Arts courses to matching subcategories.

Run from the backend repository root:
  python scripts/migrate_martial_arts_subcategory_courses.py
  python scripts/migrate_martial_arts_subcategory_courses.py --apply
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from pymongo import MongoClient  # noqa: E402

load_dotenv(ROOT / ".env")

COURSE_NAME_ALIASES = {
    "teakwando": "taekwondo",
}


def canonical_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").strip().lower())


def run(apply: bool) -> list[tuple[str, str]]:
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or os.getenv("MONGODB_URL")
    db_name = os.getenv("DB_NAME") or os.getenv("MONGO_DB") or "marshalats"
    if not mongo_url:
        raise SystemExit("MONGO_URL or MONGO_URI must be set")

    client = MongoClient(mongo_url, tlsAllowInvalidCertificates=True)
    try:
        db = client[db_name]
        parent = db.categories.find_one({"slug": "martial-arts", "is_active": True})
        if not parent:
            raise SystemExit("Active Martial Arts category with slug 'martial-arts' was not found")

        subcategories = list(
            db.categories.find({
                "parent_category_id": parent["id"],
                "is_active": True,
            })
        )
        subcategory_by_name = {
            canonical_name(category.get("name", "")): category
            for category in subcategories
        }

        unassigned = db.courses.find({
            "category_id": parent["id"],
            "$or": [
                {"sub_category": None},
                {"sub_category": ""},
                {"sub_category": {"$exists": False}},
            ],
        })

        assignments = []
        for course in unassigned:
            course_name = canonical_name(course.get("title", ""))
            course_name = COURSE_NAME_ALIASES.get(course_name, course_name)
            subcategory = subcategory_by_name.get(course_name)
            if not subcategory:
                continue

            title = course.get("title") or course.get("code") or course.get("id")
            assignments.append((title, subcategory["name"]))
            if apply:
                db.courses.update_one(
                    {
                        "id": course["id"],
                        "category_id": parent["id"],
                        "$or": [
                            {"sub_category": None},
                            {"sub_category": ""},
                            {"sub_category": {"$exists": False}},
                        ],
                    },
                    {
                        "$set": {
                            "sub_category": subcategory["id"],
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
        return assignments
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign matching Martial Arts courses to subcategories")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the assignments; without this option, only preview them",
    )
    args = parser.parse_args()

    assignments = run(args.apply)
    print("Assignments applied:" if args.apply else "Dry run; no assignments changed:")
    if not assignments:
        print("  No matching unassigned courses found")
    for course_title, subcategory_name in assignments:
        print(f"  {course_title} -> {subcategory_name}")
    print(f"Total: {len(assignments)}")


if __name__ == "__main__":
    main()
