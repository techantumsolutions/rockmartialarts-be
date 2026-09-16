"""
M02-S04 branch-course mapping backfill.

- Upserts unique (branch_id, course_id) rows from branches.assignments
- Copies is_available from course_schedule (default true)
- Deactivates mappings that are no longer assigned (no hard delete)
- Does not change branch ids or live assignment lists

Run from backend repo root:
  python scripts/migrate_branch_courses.py --dry-run
  python scripts/migrate_branch_courses.py
"""
import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from pymongo import MongoClient  # noqa: E402

load_dotenv(ROOT / ".env")


def unique_course_ids(raw):
    seen = set()
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        cid = ""
        if isinstance(item, str):
            cid = item.strip()
        elif isinstance(item, dict):
            cid = str(item.get("course_id") or item.get("courseId") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def schedule_availability_map(branch):
    out = {}
    sched = ((branch or {}).get("assignments") or {}).get("course_schedule") or []
    for row in sched:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("course_id") or row.get("courseId") or "").strip()
        if cid:
            out[cid] = row.get("is_available", True) is not False
    return out


def merge_schedule_fees(branch, course_id):
    merged = {}
    sched = ((branch or {}).get("assignments") or {}).get("course_schedule") or []
    for row in sched:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("course_id") or row.get("courseId") or "").strip()
        if cid != course_id:
            continue
        for batch in row.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            fpd = batch.get("fee_per_duration") or {}
            if isinstance(fpd, dict):
                for key, value in fpd.items():
                    try:
                        if value is not None:
                            merged[str(key)] = float(value)
                    except (TypeError, ValueError):
                        continue
            raw_fee = batch.get("batch_fee")
            if raw_fee is not None and not merged:
                try:
                    merged["default"] = float(raw_fee)
                except (TypeError, ValueError):
                    pass
    return merged or None


def run(dry_run: bool) -> dict:
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or os.getenv("MONGODB_URL")
    db_name = os.getenv("DB_NAME") or os.getenv("MONGO_DB") or "marshalats"
    if not mongo_url:
        raise SystemExit("MONGO_URL or MONGO_URI must be set")

    client = MongoClient(mongo_url, tlsAllowInvalidCertificates=True)
    db = client[db_name]
    stats = {
        "branches_examined": 0,
        "mappings_upserted": 0,
        "mappings_deactivated": 0,
        "schedule_availability_backfilled": 0,
    }
    now = datetime.utcnow()
    branches = list(db.branches.find({}).limit(5000))

    try:
        db.branch_courses.create_index(
            [("branch_id", 1), ("course_id", 1)],
            unique=True,
            name="branch_courses_pair_unique",
        )
    except Exception as exc:
        print(f"Index note: {exc}")

    for branch in branches:
        stats["branches_examined"] += 1
        branch_id = branch.get("id")
        if not branch_id:
            continue

        assignments = branch.get("assignments")
        if isinstance(assignments, dict):
            sched = assignments.get("course_schedule")
            if isinstance(sched, list):
                changed = False
                for row in sched:
                    if isinstance(row, dict) and "is_available" not in row:
                        row["is_available"] = True
                        changed = True
                        stats["schedule_availability_backfilled"] += 1
                if changed and not dry_run:
                    db.branches.update_one(
                        {"id": branch_id},
                        {"$set": {"assignments.course_schedule": sched, "updated_at": now}},
                    )

        course_ids = unique_course_ids((branch.get("assignments") or {}).get("courses"))
        avail = schedule_availability_map(branch)
        for cid in course_ids:
            fees = merge_schedule_fees(branch, cid)
            payload = {
                "branch_id": branch_id,
                "course_id": cid,
                "is_available": avail.get(cid, True),
                "fee_per_duration": fees,
                "updated_at": now,
            }
            existing = db.branch_courses.find_one({"branch_id": branch_id, "course_id": cid})
            stats["mappings_upserted"] += 1
            if dry_run:
                continue
            if existing:
                db.branch_courses.update_one({"id": existing["id"]}, {"$set": payload})
            else:
                payload["id"] = str(uuid.uuid4())
                payload["created_at"] = now
                db.branch_courses.insert_one(payload)

        deactivate_query = {
            "branch_id": branch_id,
            "course_id": {"$nin": course_ids or ["__none__"]},
            "is_available": {"$ne": False},
        }
        to_deactivate = db.branch_courses.count_documents(deactivate_query)
        stats["mappings_deactivated"] += to_deactivate
        if to_deactivate and not dry_run:
            db.branch_courses.update_many(
                {"branch_id": branch_id, "course_id": {"$nin": course_ids or ["__none__"]}},
                {"$set": {"is_available": False, "updated_at": now}},
            )

    client.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill branch_courses mappings")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = run(args.dry_run)
    prefix = "DRY RUN " if args.dry_run else ""
    print(f"{prefix}migration complete")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
