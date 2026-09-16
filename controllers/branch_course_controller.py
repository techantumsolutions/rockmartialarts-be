from fastapi import HTTPException, Depends
from typing import Optional

from models.branch_course_models import BranchCourseUpsert, BranchCourseUpdate
from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin
from utils.branch_courses import (
    available_course_ids_for_branch,
    unique_course_ids,
    upsert_branch_course,
    schedule_availability_map,
)


class BranchCourseController:
    @staticmethod
    async def upsert_mapping(body: BranchCourseUpsert, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        mapping = await upsert_branch_course(
            db,
            body.branch_id,
            body.course_id,
            is_available=body.is_available,
            fee_per_duration=body.fee_per_duration,
        )
        return {"message": "Branch course mapping saved", "mapping": serialize_doc(mapping)}

    @staticmethod
    async def update_mapping(branch_id: str, course_id: str, body: BranchCourseUpdate, current_user: dict = None):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        existing = await db.branch_courses.find_one({"branch_id": branch_id, "course_id": course_id})
        if not existing:
            mapping = await upsert_branch_course(
                db,
                branch_id,
                course_id,
                is_available=True if body.is_available is None else body.is_available,
                fee_per_duration=body.fee_per_duration,
            )
            return {"message": "Branch course mapping saved", "mapping": serialize_doc(mapping)}
        is_available = existing.get("is_available", True) if body.is_available is None else body.is_available
        mapping = await upsert_branch_course(
            db,
            branch_id,
            course_id,
            is_available=is_available,
            fee_per_duration=body.fee_per_duration if body.fee_per_duration is not None else existing.get("fee_per_duration"),
        )
        return {"message": "Branch course mapping updated", "mapping": serialize_doc(mapping)}

    @staticmethod
    async def get_by_branch(branch_id: str, available_only: bool = False, current_user: dict = None):
        db = get_db()
        branch = await db.branches.find_one({"id": branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        query = {"branch_id": branch_id}
        if available_only:
            query["is_available"] = True
        mappings = await db.branch_courses.find(query).to_list(500)
        if not mappings:
            assigned = unique_course_ids((branch.get("assignments") or {}).get("courses"))
            if available_only:
                assigned = await available_course_ids_for_branch(db, branch)
            avail = schedule_availability_map(branch)
            mappings = [
                {
                    "branch_id": branch_id,
                    "course_id": cid,
                    "is_available": avail.get(cid, True),
                    "fee_per_duration": None,
                }
                for cid in assigned
            ]
        return {
            "message": f"Retrieved {len(mappings)} branch-course mappings",
            "branch_id": branch_id,
            "mappings": [serialize_doc(m) for m in mappings],
            "total": len(mappings),
        }

    @staticmethod
    async def get_by_course(course_id: str, available_only: bool = False, current_user: dict = None):
        db = get_db()
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        query = {"course_id": course_id}
        if available_only:
            query["is_available"] = True
        mappings = await db.branch_courses.find(query).to_list(500)
        if not mappings:
            branches = await db.branches.find({"assignments.courses": course_id, "is_active": True}).to_list(200)
            mappings = []
            for branch in branches:
                if available_only:
                    allowed = await available_course_ids_for_branch(db, branch)
                    if course_id not in allowed:
                        continue
                mappings.append({
                    "branch_id": branch["id"],
                    "course_id": course_id,
                    "is_available": schedule_availability_map(branch).get(course_id, True),
                    "branch_name": ((branch.get("branch") or {}).get("name")),
                })
        else:
            for mapping in mappings:
                branch = await db.branches.find_one({"id": mapping.get("branch_id")})
                if branch:
                    mapping["branch_name"] = ((branch.get("branch") or {}).get("name"))
        return {
            "message": f"Retrieved {len(mappings)} branch mappings for course",
            "course_id": course_id,
            "mappings": [serialize_doc(m) for m in mappings],
            "total": len(mappings),
        }
