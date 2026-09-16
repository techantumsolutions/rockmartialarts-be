"""
M02-S03 category / course hierarchy backfill.

- Normalizes empty parent_category_id to None
- Fills unique slugs on categories and courses
- Does not change ids or existing parent/child links

Run from backend repo root:
  python scripts/migrate_course_hierarchy.py --dry-run
  python scripts/migrate_course_hierarchy.py
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from pymongo import MongoClient  # noqa: E402

load_dotenv(ROOT / ".env")

_slug_strip_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = _slug_strip_re.sub("-", text).strip("-")
    return text or "item"


def unique_slug(collection, base: str, used: set, exclude_id: str = None) -> str:
    slug = slugify(base)
    candidate = slug
    n = 2
    while True:
        query = {"slug": candidate}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        existing = collection.find_one(query)
        if candidate not in used and not existing:
            used.add(candidate)
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
        if n > 500:
            used.add(candidate)
            return candidate


def run(dry_run: bool) -> dict:
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or os.getenv("MONGODB_URL")
    db_name = os.getenv("DB_NAME") or os.getenv("MONGO_DB") or "marshalats"
    if not mongo_url:
        raise SystemExit("MONGO_URL or MONGO_URI must be set")

    client = MongoClient(mongo_url, tlsAllowInvalidCertificates=True)
    db = client[db_name]
    stats = {
        "categories_examined": 0,
        "parents_normalized": 0,
        "category_slugs_set": 0,
        "courses_examined": 0,
        "course_slugs_set": 0,
    }

    used_cat_slugs = set()
    categories = list(db.categories.find({}).limit(5000))
    for category in categories:
        slug = (category.get("slug") or "").strip().lower()
        if slug:
            used_cat_slugs.add(slug)

    now = datetime.utcnow()
    for category in categories:
        stats["categories_examined"] += 1
        updates = {}
        parent = category.get("parent_category_id")
        if parent == "":
            updates["parent_category_id"] = None
            stats["parents_normalized"] += 1
        if not (category.get("slug") or "").strip():
            updates["slug"] = unique_slug(
                db.categories, category.get("name") or "category", used_cat_slugs, category.get("id")
            )
            stats["category_slugs_set"] += 1
        if updates:
            updates["updated_at"] = now
            if not dry_run:
                db.categories.update_one({"id": category["id"]}, {"$set": updates})

    used_course_slugs = set()
    courses = list(db.courses.find({}).limit(5000))
    for course in courses:
        slug = (course.get("slug") or "").strip().lower()
        if slug:
            used_course_slugs.add(slug)

    for course in courses:
        stats["courses_examined"] += 1
        if (course.get("slug") or "").strip():
            continue
        slug = unique_slug(
            db.courses, course.get("title") or course.get("code") or "course", used_course_slugs, course.get("id")
        )
        stats["course_slugs_set"] += 1
        if not dry_run:
            db.courses.update_one(
                {"id": course["id"]},
                {"$set": {"slug": slug, "updated_at": now}},
            )

    client.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill category/course slugs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = run(args.dry_run)
    prefix = "DRY RUN " if args.dry_run else ""
    print(f"{prefix}migration complete")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
