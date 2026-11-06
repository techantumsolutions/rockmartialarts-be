from fastapi import HTTPException, status
from datetime import datetime
from typing import List, Optional

from models.achievement_models import StudentAchievement, AchievementCreate, AchievementUpdate
from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc


async def _get_student_branch_id(student_id: str) -> Optional[str]:
    """Resolve branch_id for a student from user.branch_id or enrollments."""
    db = get_db()
    user = await db.users.find_one({"id": student_id, "role": "student"})
    if not user:
        return None
    if user.get("branch_id"):
        return user["branch_id"]
    enrollment = await db.enrollments.find_one(
        {"student_id": student_id, "is_active": True},
        sort=[("created_at", -1)]
    )
    return enrollment.get("branch_id") if enrollment else None


def _can_manage_achievement(current_user: dict, branch_id: str) -> bool:
    """Super admin can manage Rock achievements; branch manager only their branches."""
    role = current_user.get("role")
    if role == "super_admin":
        return True
    if role == "branch_manager":
        managed = current_user.get("managed_branches") or []
        return branch_id in managed
    return False


async def _partner_owns_student(db, current_user: dict, student_id: str, branch_id: Optional[str]) -> bool:
    org_id = str(current_user.get("partner_org_id") or "").strip()
    if not org_id:
        return False
    student = await db.users.find_one(
        {"id": student_id, "role": "student"},
        {"partner_org_id": 1},
    )
    if student and str(student.get("partner_org_id") or "") == org_id:
        return True
    if branch_id:
        branch = await db.branches.find_one(
            {"id": branch_id, "partner_org_id": org_id},
            {"id": 1},
        )
        if branch:
            return True
    return False


async def _rock_achievement_scope(db) -> dict:
    """Exclude partner-stamped, partner-branch, and partner-created achievements."""
    partner_bids = [
        b
        for b in await db.branches.distinct(
            "id",
            {"partner_org_id": {"$exists": True, "$nin": [None, ""]}},
        )
        if b
    ]
    partner_users = [
        u
        for u in await db.users.distinct(
            "id",
            {"partner_org_id": {"$exists": True, "$nin": [None, ""]}},
        )
        if u
    ]
    nor = [{"partner_org_id": {"$exists": True, "$nin": [None, ""]}}]
    if partner_bids:
        nor.append({"branch_id": {"$in": partner_bids}})
    if partner_users:
        nor.append({"created_by": {"$in": partner_users}})
    return {"$nor": nor}


async def _achievement_is_partner_owned(db, achievement: dict) -> bool:
    if str(achievement.get("partner_org_id") or "").strip():
        return True
    bid = achievement.get("branch_id")
    if bid:
        branch = await db.branches.find_one({"id": bid}, {"partner_org_id": 1})
        if branch and str(branch.get("partner_org_id") or "").strip():
            return True
    created_by = str(achievement.get("created_by") or "").strip()
    if created_by:
        creator = await db.users.find_one(
            {"id": created_by},
            {"partner_org_id": 1, "role": 1},
        )
        if creator and (
            str(creator.get("partner_org_id") or "").strip()
            or str(creator.get("role") or "") == "partner_super_admin"
        ):
            return True
    return False


def _can_view_student_achievements(current_user: dict, student_id: str, branch_id: str) -> bool:
    """Super admin, branch manager (their branch), or the student themselves."""
    role = current_user.get("role")
    if role == "super_admin":
        return True
    if role == "branch_manager":
        managed = current_user.get("managed_branches") or []
        return branch_id in managed
    if role == "student" and current_user.get("id") == student_id:
        return True
    if role in ("coach_admin", "coach"):
        return True
    return False


class AchievementController:
    @staticmethod
    async def create(data: AchievementCreate, current_user: dict):
        db = get_db()
        branch_id = await _get_student_branch_id(data.student_id)
        if not branch_id:
            raise HTTPException(status_code=400, detail="Student not found or has no branch")
        role = str(current_user.get("role") or "").lower()
        partner_org_id = None
        if role == "partner_super_admin":
            if not await _partner_owns_student(db, current_user, data.student_id, branch_id):
                raise HTTPException(status_code=403, detail="Not allowed to create achievements for this student")
            partner_org_id = str(current_user.get("partner_org_id") or "").strip() or None
        elif not _can_manage_achievement(current_user, branch_id):
            raise HTTPException(status_code=403, detail="Not allowed to create achievements for this student")

        achievement = StudentAchievement(
            student_id=data.student_id,
            branch_id=branch_id,
            title=data.title,
            description=data.description,
            images=data.images or [],
            documents=data.documents or [],
            is_featured=False,
            created_by=current_user.get("id"),
        )
        doc = achievement.dict()
        doc["created_at"] = datetime.utcnow()
        doc["updated_at"] = datetime.utcnow()
        if partner_org_id:
            doc["partner_org_id"] = partner_org_id
        await db.student_achievements.insert_one(doc)
        return serialize_doc(doc)

    @staticmethod
    async def update(achievement_id: str, data: AchievementUpdate, current_user: dict):
        db = get_db()
        achievement = await db.student_achievements.find_one({"id": achievement_id})
        if not achievement:
            raise HTTPException(status_code=404, detail="Achievement not found")
        if achievement.get("is_deleted"):
            raise HTTPException(status_code=404, detail="Achievement not found")
        branch_id = achievement["branch_id"]
        role = str(current_user.get("role") or "").lower()
        if role == "partner_super_admin":
            if not await _partner_owns_student(db, current_user, achievement.get("student_id"), branch_id):
                raise HTTPException(status_code=403, detail="Not allowed to edit this achievement")
        elif not _can_manage_achievement(current_user, branch_id):
            raise HTTPException(status_code=403, detail="Not allowed to edit this achievement")

        update_fields = {}
        if data.title is not None:
            update_fields["title"] = data.title
        if data.description is not None:
            update_fields["description"] = data.description
        if data.images is not None:
            update_fields["images"] = data.images
        if data.documents is not None:
            update_fields["documents"] = data.documents
        if data.is_featured is not None:
            role = current_user.get("role")
            if role not in ("super_admin", "superadmin"):
                raise HTTPException(status_code=403, detail="Only super admin can set featured")
            update_fields["is_featured"] = bool(data.is_featured)
        update_fields["updated_at"] = datetime.utcnow()
        await db.student_achievements.update_one(
            {"id": achievement_id},
            {"$set": update_fields}
        )
        updated = await db.student_achievements.find_one({"id": achievement_id})
        return serialize_doc(updated)

    @staticmethod
    async def delete(achievement_id: str, current_user: dict):
        db = get_db()
        achievement = await db.student_achievements.find_one({"id": achievement_id})
        if not achievement:
            raise HTTPException(status_code=404, detail="Achievement not found")
        branch_id = achievement["branch_id"]
        role = str(current_user.get("role") or "").lower()
        if role == "partner_super_admin":
            if not await _partner_owns_student(db, current_user, achievement.get("student_id"), branch_id):
                raise HTTPException(status_code=403, detail="Not allowed to delete this achievement")
        elif not _can_manage_achievement(current_user, branch_id):
            raise HTTPException(status_code=403, detail="Not allowed to delete this achievement")
        await db.student_achievements.update_one(
            {"id": achievement_id},
            {"$set": {"is_deleted": True, "updated_at": datetime.utcnow()}}
        )
        return {"message": "Achievement deleted"}

    @staticmethod
    async def get_by_student(student_id: str, current_user: dict):
        db = get_db()
        user = await db.users.find_one({"id": student_id})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")
        branch_id = await _get_student_branch_id(student_id)
        role = str(current_user.get("role") or "").lower()
        if role == "partner_super_admin":
            if not await _partner_owns_student(db, current_user, student_id, branch_id):
                raise HTTPException(status_code=403, detail="Not allowed to view these achievements")
        elif not _can_view_student_achievements(current_user, student_id, branch_id or ""):
            raise HTTPException(status_code=403, detail="Not allowed to view these achievements")

        cursor = db.student_achievements.find({
            "student_id": student_id,
            "is_deleted": False
        }).sort("created_at", -1)
        items = await cursor.to_list(length=500)
        return {"achievements": [serialize_doc(d) for d in items]}

    @staticmethod
    async def get_by_branch_public(branch_id: str, skip: int = 0, limit: int = 12):
        """Public endpoint: achievements for a branch (for branch detail page)."""
        db = get_db()
        cursor = db.student_achievements.find({
            "branch_id": branch_id,
            "is_deleted": False
        }).sort("created_at", -1).skip(skip).limit(limit)
        items = await cursor.to_list(length=limit)
        total = await db.student_achievements.count_documents({
            "branch_id": branch_id,
            "is_deleted": False
        })
        # Enrich with student names
        student_ids = list({a["student_id"] for a in items})
        users = await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids))
        user_map = {u["id"]: u.get("full_name") or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Student" for u in users}
        result = []
        for a in items:
            d = serialize_doc(a)
            d["student_name"] = user_map.get(a["student_id"], "Student")
            result.append(d)
        return {"achievements": result, "total": total }

    @staticmethod
    async def get_global_public(skip: int = 0, limit: int = 6):
        """Public: featured achievements for homepage (is_featured=True)."""
        db = get_db()
        q = {
            "is_deleted": False,
            "is_featured": True,
        }
        q.update(await _rock_achievement_scope(db))
        # $nor from the scope must AND with the featured filter
        if "$nor" in q and ("is_deleted" in q or "is_featured" in q):
            nor = q.pop("$nor")
            q = {"$and": [{"is_deleted": False, "is_featured": True}, {"$nor": nor}]}
        cursor = db.student_achievements.find(q).sort("created_at", -1).skip(skip).limit(limit)
        items = await cursor.to_list(length=limit)
        # Enrich with student names
        student_ids = list({a["student_id"] for a in items})
        users = await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids)) if student_ids else []
        user_map = {u["id"]: u.get("full_name") or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Student" for u in users}
        result = []
        for a in items:
            d = serialize_doc(a)
            d["student_name"] = user_map.get(a["student_id"], "Student")
            result.append(d)
        return {"achievements": result}

    @staticmethod
    async def list_for_showcase_manage(current_user: dict, featured_only: bool = False):
        """Rock SA: all achievements. Partner SA: only their org branch achievements."""
        role = str(current_user.get("role") or "").lower()
        db = get_db()
        q: dict = {"is_deleted": False}
        if role in ("super_admin", "superadmin"):
            q.update(await _rock_achievement_scope(db))
            if "$nor" in q:
                nor = q.pop("$nor")
                q = {"$and": [{"is_deleted": False}, {"$nor": nor}]}
                if featured_only:
                    q["$and"][0]["is_featured"] = True
        elif role == "partner_super_admin":
            org_id = str(current_user.get("partner_org_id") or "").strip()
            if not org_id:
                raise HTTPException(status_code=403, detail="Partner organization missing")
            owned = await db.branches.find(
                {"partner_org_id": org_id}, {"id": 1}
            ).to_list(500)
            branch_ids = [b["id"] for b in owned if b.get("id")]
            creator_ids = [
                u
                for u in await db.users.distinct("id", {"partner_org_id": org_id})
                if u
            ]
            clauses = [{"partner_org_id": org_id}]
            if branch_ids:
                clauses.append({"branch_id": {"$in": branch_ids}})
            if creator_ids:
                clauses.append({"created_by": {"$in": creator_ids}})
            q["$or"] = clauses
        else:
            raise HTTPException(status_code=403, detail="Not allowed to manage featured achievements")
        if featured_only:
            q["is_featured"] = True
        cursor = db.student_achievements.find(q).sort([("is_featured", -1), ("created_at", -1)])
        items = await cursor.to_list(length=500)
        student_ids = list({a["student_id"] for a in items})
        users = await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids)) if student_ids else []
        user_map = {
            u["id"]: u.get("full_name")
            or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
            or "Student"
            for u in users
        }
        branch_ids = list({a.get("branch_id") for a in items if a.get("branch_id")})
        branches = await db.branches.find({"id": {"$in": branch_ids}}).to_list(length=len(branch_ids)) if branch_ids else []
        branch_map = {
            b["id"]: (b.get("branch") or {}).get("name") or b.get("name") or b["id"]
            for b in branches
        }
        result = []
        for a in items:
            d = serialize_doc(a)
            d["student_name"] = user_map.get(a["student_id"], "Student")
            d["branch_name"] = branch_map.get(a.get("branch_id"), a.get("branch_id") or "—")
            d["is_featured"] = bool(a.get("is_featured", False))
            result.append(d)
        return {"achievements": result}

    @staticmethod
    async def set_featured(achievement_id: str, is_featured: bool, current_user: dict):
        """Rock SA: any. Partner SA: only achievements on their org branches (landing featured)."""
        role = str(current_user.get("role") or "").lower()
        db = get_db()
        achievement = await db.student_achievements.find_one({"id": achievement_id, "is_deleted": False})
        if not achievement:
            raise HTTPException(status_code=404, detail="Achievement not found")
        if role in ("super_admin", "superadmin"):
            if await _achievement_is_partner_owned(db, achievement):
                raise HTTPException(status_code=404, detail="Achievement not found")
        elif role == "partner_super_admin":
            org_id = str(current_user.get("partner_org_id") or "").strip()
            if not org_id:
                raise HTTPException(status_code=403, detail="Partner organization missing")
            bid = achievement.get("branch_id")
            owned = await db.branches.find_one({"id": bid, "partner_org_id": org_id}) if bid else None
            if not owned:
                raise HTTPException(status_code=403, detail="Achievement is outside your organization")
        else:
            raise HTTPException(status_code=403, detail="Only admin can set featured")
        await db.student_achievements.update_one(
            {"id": achievement_id},
            {"$set": {"is_featured": bool(is_featured), "updated_at": datetime.utcnow()}},
        )
        updated = await db.student_achievements.find_one({"id": achievement_id})
        return serialize_doc(updated)
