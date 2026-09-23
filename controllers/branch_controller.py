import re
from fastapi import HTTPException, Depends, status
from typing import Optional
from datetime import datetime

from models.branch_models import BranchCreate, BranchUpdate, Branch
from models.holiday_models import HolidayCreate, Holiday
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.branch_geography import (
    apply_city_to_branch_doc,
    assert_unique_branch_code,
    ensure_unique_branch_slug,
    is_uuid,
    public_branch_discovery_query,
    resolve_city_for_write,
)
from utils.branch_courses import (
    available_course_ids_for_branch,
    prepare_assignments_for_write,
    sync_branch_courses_from_assignments,
)
from utils.course_hierarchy import name_slug
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_branch_sync import count_students_for_branch
from utils.collaboration_partner import (
    apply_collaboration_flag,
    enrich_collaboration_flag,
    resolve_collaboration_flag_from_payload,
)


async def _enrich_course_schedule_trainers(db, branch: dict) -> None:
    """Add trainer_name to each batch in assignments.course_schedule (mutates branch)."""
    asg = branch.get("assignments")
    if not isinstance(asg, dict):
        return
    sched = asg.get("course_schedule")
    if not isinstance(sched, list) or not sched:
        return
    ids = set()
    for entry in sched:
        if not isinstance(entry, dict):
            continue
        for b in entry.get("batches") or []:
            if not isinstance(b, dict):
                continue
            cid = str(b.get("coach_id") or b.get("coachId") or "").strip()
            if cid:
                ids.add(cid)
    if not ids:
        return
    users = await db.users.find({"id": {"$in": list(ids)}}).to_list(length=300)
    name_map = {}
    for u in users:
        fn = (u.get("first_name") or "").strip()
        ln = (u.get("last_name") or "").strip()
        name_map[u["id"]] = (
            f"{fn} {ln}".strip()
            or (u.get("full_name") or "").strip()
            or (u.get("email") or "").strip()
            or "Trainer"
        )
    for entry in sched:
        if not isinstance(entry, dict):
            continue
        batches = entry.get("batches")
        if not isinstance(batches, list):
            continue
        for b in batches:
            if not isinstance(b, dict):
                continue
            cid = str(b.get("coach_id") or b.get("coachId") or "").strip()
            if cid and name_map.get(cid):
                b["trainer_name"] = name_map[cid]


def _pydantic_dump(model) -> dict:
    """BranchUpdate payload as plain dicts for MongoDB (Pydantic v1/v2)."""
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True, mode="python")
    return model.dict(exclude_unset=True)


class BranchController:
    @staticmethod
    async def create_branch(
        branch_data: BranchCreate,
        current_user: dict = None
    ):
        """Create new branch with comprehensive nested structure"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        db = get_db()
        payload = branch_data.dict()
        city, state = await resolve_city_for_write(db, payload.get("location_id"))
        apply_city_to_branch_doc(payload, city, state)
        code = ((payload.get("branch") or {}).get("code") or "").strip()
        await assert_unique_branch_code(db, code)
        name = ((payload.get("branch") or {}).get("name") or "").strip()
        payload["slug"] = await ensure_unique_branch_slug(
            db, name, existing_slug=payload.get("slug")
        )
        flag = resolve_collaboration_flag_from_payload(payload, default=False)
        if flag:
            from utils.collaboration_partner import assert_can_toggle_collaboration_flag

            assert_can_toggle_collaboration_flag(current_user)
        apply_collaboration_flag(payload, flag)
        if isinstance(payload.get("assignments"), dict):
            payload["assignments"] = prepare_assignments_for_write(payload.get("assignments"))

        branch = Branch(**payload)
        
        # Store the branch with nested structure exactly as provided
        branch_dict = branch.dict()
        apply_collaboration_flag(branch_dict, flag)
        
        await db.branches.insert_one(branch_dict)
        await sync_branch_courses_from_assignments(db, branch.id, branch_dict)
        return {"message": "Branch created successfully", "branch_id": branch.id}

    @staticmethod
    async def get_branches(
        skip: int = 0,
        limit: int = 50,
        active_only: bool = True,
        current_user: dict = None,
        include_stats: bool = True,
    ):
        """Get branches with nested structure and statistics, filtered by user role"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query based on user role
        filter_query: dict = {}
        if active_only:
            filter_query["is_active"] = True
        current_role = current_user.get("role")

        if current_role == "branch_manager":
            # Branch managers can only see branches they manage
            # The unified auth system populates managed_branches array
            managed_branches = current_user.get("managed_branches", [])

            if managed_branches:
                # Filter to only show branches managed by this branch manager
                filter_query["id"] = {"$in": managed_branches}
            else:
                # If no branches are managed, return empty result
                return {
                    "branches": [],
                    "total_count": 0,
                    "skip": skip,
                    "limit": limit,
                    "message": "No branches assigned to this manager"
                }

        branches = await db.branches.find(filter_query).skip(skip).limit(limit).to_list(length=limit)

        if not include_stats:
            return {"branches": serialize_doc(branches)}

        # Enhance branches with coach and student counts
        enhanced_branches = []
        for branch in branches:
            branch_id = branch["id"]

            # Count coaches assigned to this branch
            coach_count = await db.coaches.count_documents({
                "branch_id": branch_id,
                "is_active": True
            })

            # Also count coaches assigned as managers or branch admins (if they exist in users collection)
            # Count manager if assigned
            if branch.get("manager_id"):
                manager_exists = await db.users.count_documents({
                    "id": branch["manager_id"],
                    "role": {"$in": ["coach", "coach_admin"]},
                    "is_active": True
                })
                coach_count += manager_exists

            # Count branch admins (coaches assigned as admins)
            if branch.get("assignments", {}).get("branch_admins"):
                admin_coaches = await db.users.count_documents({
                    "id": {"$in": branch["assignments"]["branch_admins"]},
                    "role": {"$in": ["coach", "coach_admin"]},
                    "is_active": True
                })
                coach_count += admin_coaches

            branch_id = branch["id"]
            student_count = await count_students_for_branch(db, branch_id)

            # Add statistics to branch data
            branch_with_stats = {
                **branch,
                "statistics": {
                    "coach_count": coach_count,
                    "student_count": student_count,
                    "course_count": len(branch.get("operational_details", {}).get("courses_offered", [])),
                    "active_courses": len(branch.get("assignments", {}).get("courses", []))
                }
            }
            enrich_collaboration_flag(branch_with_stats)
            enhanced_branches.append(branch_with_stats)

        return {"branches": serialize_doc(enhanced_branches)}

    @staticmethod
    async def get_branches_public(
        active_only: bool = True,
        skip: int = 0,
        limit: int = 100
    ):
        """Get all branches - Public endpoint (no authentication required)"""
        db = get_db()

        # Build query
        query = {}
        if active_only:
            query["is_active"] = True

        # Apply pagination with limit for public endpoint
        if limit > 100:
            limit = 100  # Cap at 100 for public endpoint

        # Get branches
        branches_cursor = db.branches.find(query).skip(skip).limit(limit)
        branches = await branches_cursor.to_list(limit)

        # Get total count
        total = await db.branches.count_documents(query)

        formatted_branches = []
        for branch in branches:
            await _enrich_course_schedule_trainers(db, branch)
            formatted_branches.append(BranchController._public_list_item(branch))

        return {
            "message": f"Retrieved {len(formatted_branches)} branches successfully",
            "branches": formatted_branches,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    def _public_list_item(branch: dict) -> dict:
        branch_info = branch.get("branch") or {}
        operational = branch.get("operational_details") or {}
        assignments = branch.get("assignments") or {}
        return {
            "id": branch.get("id"),
            "name": branch_info.get("name"),
            "code": branch_info.get("code"),
            "email": branch_info.get("email"),
            "phone": branch_info.get("phone"),
            "address": branch_info.get("address", {}),
            "branch": {
                "name": branch_info.get("name"),
                "code": branch_info.get("code"),
                "email": branch_info.get("email"),
                "phone": branch_info.get("phone"),
                "address": branch_info.get("address", {}),
            },
            "location_id": branch.get("location_id"),
            "slug": branch.get("slug") or BranchController._name_to_slug(
                str(branch_info.get("name") or "")
            ),
            "allows_collaboration": bool(branch.get("allows_collaboration", False))
            or bool(branch.get("is_collaboration_partner", False)),
            "is_collaboration_partner": bool(branch.get("allows_collaboration", False))
            or bool(branch.get("is_collaboration_partner", False)),
            "is_active": branch.get("is_active", True),
            "admission_fee": branch.get("admission_fee", 500.0),
            "operational_details": {
                "timings": operational.get("timings", []),
                "courses_offered": operational.get("courses_offered", []),
                "holidays": operational.get("holidays", []),
            },
            "assignments": {
                "accessories_available": assignments.get("accessories_available", False),
                "courses": assignments.get("courses", []),
                "course_schedule": assignments.get("course_schedule") or [],
            },
        }

    @staticmethod
    async def _public_courses_for_branch(db, branch: dict) -> list:
        course_ids = await available_course_ids_for_branch(db, branch)
        if not course_ids:
            return []
        courses = await db.courses.find({
            "id": {"$in": course_ids},
            "settings.active": True,
        }).to_list(100)
        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)
        duration_rows = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "code": d.get("code"),
                "duration_months": d.get("duration_months", 1),
            }
            for d in durations
        ]
        branch_id = branch.get("id")
        cards = []
        for course in courses:
            pricing = course.get("pricing") or {}
            branch_prices = pricing.get("branch_prices") or []
            branch_entry = next((row for row in branch_prices if row.get("branch_id") == branch_id), None)
            src = branch_entry or pricing
            media = course.get("media_resources") or {}
            hero = ((course.get("page_content") or {}).get("hero_section") or {}).get("hero_image")
            cards.append({
                "id": course.get("id"),
                "title": course.get("title"),
                "code": course.get("code"),
                "slug": (
                    course.get("slug")
                    or name_slug(course.get("title") or course.get("code"))
                    or course.get("id")
                    or ""
                ).strip().lower(),
                "description": course.get("description"),
                "difficulty_level": course.get("difficulty_level"),
                "media_resources": {
                    "course_image_url": media.get("course_image_url") or hero,
                },
                "pricing": {
                    "currency": src.get("currency") or pricing.get("currency", "INR"),
                    "amount": src.get("amount"),
                    "fee_1_month": src.get("fee_1_month"),
                    "fee_3_months": src.get("fee_3_months"),
                    "fee_6_months": src.get("fee_6_months"),
                    "fee_1_year": src.get("fee_1_year"),
                    "fee_per_duration": src.get("fee_per_duration"),
                },
                "available_durations": duration_rows,
            })
        return cards

    @staticmethod
    async def _public_branch_detail(db, branch: dict) -> dict:
        await _enrich_course_schedule_trainers(db, branch)
        payload = BranchController._public_list_item(branch)
        payload["description"] = branch.get("description")
        payload["gallery_images"] = branch.get("gallery_images") or []
        payload["map_link"] = branch.get("map_link")
        payload["facilities"] = branch.get("facilities") or []
        payload["coordinates"] = branch.get("coordinates")
        payload["courses"] = await BranchController._public_courses_for_branch(db, branch)
        available_ids = {c.get("id") for c in payload["courses"] if c.get("id")}
        assignments = payload.get("assignments") or {}
        assignments["courses"] = [
            cid for cid in (assignments.get("courses") or []) if cid in available_ids
        ]
        assignments["course_schedule"] = [
            row
            for row in (assignments.get("course_schedule") or [])
            if isinstance(row, dict)
            and (row.get("course_id") or row.get("courseId")) in available_ids
        ]
        payload["assignments"] = assignments
        for key in ("manager_id", "bank_details", "bank_info", "bank_account"):
            payload.pop(key, None)
        return payload

    @staticmethod
    async def search_branches_public(
        state_id: Optional[str] = None,
        city_id: Optional[str] = None,
        q: Optional[str] = None,
        active_only: bool = True,
        collaboration_partners_only: bool = False,
        skip: int = 0,
        limit: int = 100,
    ):
        """Public branch discovery: state, city, and text search, alone or combined."""
        db = get_db()
        if limit > 100:
            limit = 100
        query = await public_branch_discovery_query(
            db,
            state_id=state_id,
            city_id=city_id,
            q=q,
            active_only=active_only,
        )
        if collaboration_partners_only:
            partner_clause = {
                "$or": [
                    {"allows_collaboration": True},
                    {"is_collaboration_partner": True},
                ]
            }
            query = {"$and": [query, partner_clause]} if query else partner_clause
        total = await db.branches.count_documents(query)
        branches = await db.branches.find(query).skip(skip).limit(limit).to_list(limit)
        formatted = []
        for branch in branches:
            await _enrich_course_schedule_trainers(db, branch)
            formatted.append(BranchController._public_list_item(branch))
        return {
            "message": f"Retrieved {len(formatted)} branches successfully",
            "branches": formatted,
            "total": total,
            "skip": skip,
            "limit": limit,
            "filters": {
                "state_id": (state_id or "").strip() or None,
                "city_id": (city_id or "").strip() or None,
                "q": (q or "").strip() or None,
                "collaboration_partners_only": collaboration_partners_only,
            },
        }

    @staticmethod
    async def get_branch_public(branch_id: str):
        """Get one branch by ID for public detail page (no auth)."""
        db = get_db()
        branch = await db.branches.find_one({"id": branch_id, "is_active": True})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        return await BranchController._public_branch_detail(db, branch)

    @staticmethod
    def _name_to_slug(name: str) -> str:
        """Convert branch name to URL slug (same logic as frontend branchNameToSlug)."""
        if not name or not isinstance(name, str):
            return ""
        s = name.strip().lower()
        s = re.sub(r"\s+", "-", s)
        s = re.sub(r"[^a-z0-9-]", "", s)
        s = re.sub(r"-+", "-", s).strip("-")
        return s or "branch"

    @staticmethod
    async def get_branch_by_slug(slug: str):
        """Get one branch by URL slug for public detail page (no auth). Slug matches branch name normalized."""
        if not slug or not slug.strip():
            raise HTTPException(status_code=404, detail="Branch not found")
        slug = slug.strip().lower()
        db = get_db()
        # Prefer stored slug (S02); fall back to name-derived slug for older rows.
        stored = await db.branches.find_one({"slug": slug, "is_active": True})
        if stored:
            return await BranchController._public_branch_detail(db, stored)
        # If slug looks like UUID, try by id first
        if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", slug, re.I):
            branch = await db.branches.find_one({"id": slug, "is_active": True})
            if branch:
                return await BranchController._public_branch_detail(db, branch)
        # Find by name slug (check both nested branch.name and top-level name)
        branches = await db.branches.find({"is_active": True}).to_list(length=500)
        for b in branches:
            name = (b.get("branch") or {}).get("name") or b.get("name") or ""
            if not name:
                continue
            if BranchController._name_to_slug(str(name).strip()) == slug:
                return await BranchController._public_branch_detail(db, b)
        raise HTTPException(status_code=404, detail="Branch not found")

    @staticmethod
    async def get_branch(
        branch_id: str,
        current_user: dict = None
    ):
        """Get branch by ID with nested structure and statistics"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only access branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to access this branch")

        branch = await db.branches.find_one({"id": branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        # Count coaches assigned to this branch
        coach_count = await db.coaches.count_documents({
            "branch_id": branch_id,
            "is_active": True
        })

        # Also count coaches assigned as managers or branch admins (if they exist in users collection)
        # Count manager if assigned
        if branch.get("manager_id"):
            manager_exists = await db.users.count_documents({
                "id": branch["manager_id"],
                "role": {"$in": ["coach", "coach_admin"]},
                "is_active": True
            })
            coach_count += manager_exists

        # Count branch admins (coaches assigned as admins)
        if branch.get("assignments", {}).get("branch_admins"):
            admin_coaches = await db.users.count_documents({
                "id": {"$in": branch["assignments"]["branch_admins"]},
                "role": {"$in": ["coach", "coach_admin"]},
                "is_active": True
            })
            coach_count += admin_coaches

        student_count = await count_students_for_branch(db, branch_id)

        # Add statistics to branch data
        branch_with_stats = {
            **branch,
            "statistics": {
                "coach_count": coach_count,
                "student_count": student_count,
                "course_count": len(branch.get("operational_details", {}).get("courses_offered", [])),
                "active_courses": len(branch.get("assignments", {}).get("courses", []))
            }
        }

        await _enrich_course_schedule_trainers(db, branch_with_stats)
        enrich_collaboration_flag(branch_with_stats)

        return serialize_doc(branch_with_stats)

    @staticmethod
    async def get_branch_stats(
        branch_id: str,
        current_user: dict = None
    ):
        """Get detailed statistics for a specific branch"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only access branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to access this branch")

        branch = await db.branches.find_one({"id": branch_id})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        # Count coaches assigned to this branch
        coach_count = 0
        coach_details = []

        # First, get coaches directly assigned to this branch from coaches collection
        branch_coaches = await db.coaches.find({
            "branch_id": branch_id,
            "is_active": True
        }).to_list(length=100)

        coach_count += len(branch_coaches)
        for coach in branch_coaches:
            coach_details.append({
                "id": coach["id"],
                "name": coach.get("full_name", "Unknown"),
                "role": "Coach",
                "email": coach.get("email", coach.get("contact_info", {}).get("email", ""))
            })

        # Count manager if assigned (from users collection)
        if branch.get("manager_id"):
            manager = await db.users.find_one({
                "id": branch["manager_id"],
                "role": {"$in": ["coach", "coach_admin"]},
                "is_active": True
            })
            if manager:
                coach_count += 1
                coach_details.append({
                    "id": manager["id"],
                    "name": manager.get("full_name", "Unknown"),
                    "role": "Manager",
                    "email": manager.get("email", manager.get("contact_info", {}).get("email", ""))
                })

        # Count branch admins (coaches assigned as admins from users collection)
        if branch.get("assignments", {}).get("branch_admins"):
            admin_coaches = await db.users.find({
                "id": {"$in": branch["assignments"]["branch_admins"]},
                "role": {"$in": ["coach", "coach_admin"]},
                "is_active": True
            }).to_list(length=100)

            coach_count += len(admin_coaches)
            for coach in admin_coaches:
                coach_details.append({
                    "id": coach["id"],
                    "name": coach.get("full_name", "Unknown"),
                    "role": "Branch Admin",
                    "email": coach.get("email", coach.get("contact_info", {}).get("email", ""))
                })

        student_count = await count_students_for_branch(db, branch_id)
        student_ids = await db.enrollments.distinct(
            "student_id", {"branch_id": branch_id, "is_active": True}
        )
        students = []
        if student_ids:
            students = await db.users.find({
                "id": {"$in": student_ids},
                "role": "student",
                "is_active": True,
            }).to_list(length=1000)

        # Get course statistics
        course_count = len(branch.get("operational_details", {}).get("courses_offered", []))
        active_courses = len(branch.get("assignments", {}).get("courses", []))

        # Get enrollment statistics
        total_enrollments = await db.enrollments.count_documents({
            "branch_id": branch_id,
            "is_active": True
        })

        return {
            "branch_id": branch_id,
            "branch_name": branch.get("branch", {}).get("name", "Unknown"),
            "statistics": {
                "coach_count": coach_count,
                "student_count": student_count,
                "course_count": course_count,
                "active_courses": active_courses,
                "total_enrollments": total_enrollments
            },
            "coach_details": coach_details,
            "student_details": [
                {
                    "id": student["id"],
                    "name": student.get("full_name", "Unknown"),
                    "email": student.get("email", student.get("contact_info", {}).get("email", "")),
                    "enrollment_date": student.get("created_at", "")
                } for student in students[:10]  # Limit to first 10 for performance
            ]
        }

    @staticmethod
    async def update_branch(
        branch_id: str,
        branch_update: BranchUpdate,
        current_user: dict = None
    ):
        """Update branch with nested structure"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        db = get_db()
        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")
        
        # Branch Manager permission check
        if current_role == "branch_manager":
            # Branch managers can only update branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to update this branch")

        # Coach Admin permission check
        elif current_role == UserRole.COACH_ADMIN:
            # For nested structure, check if user is admin of this branch
            existing_branch = await db.branches.find_one({"id": branch_id})
            if not existing_branch:
                raise HTTPException(status_code=404, detail="Branch not found")
            
            # Check if current user is in the branch_admins list
            branch_admins = existing_branch.get("assignments", {}).get("branch_admins", [])
            if current_user["id"] not in branch_admins:
                raise HTTPException(status_code=403, detail="You can only update branches where you are listed as an admin.")
            
            # Restrict fields a Coach Admin can update
            update_dict = _pydantic_dump(branch_update)
            restricted_fields = ["manager_id", "is_active", "assignments", "bank_details"]
            for field in restricted_fields:
                if field in update_dict:
                    raise HTTPException(status_code=403, detail=f"Coach Admins cannot update the '{field}' field.")

        update_data = {k: v for k, v in _pydantic_dump(branch_update).items()}
        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        existing_for_geo = await db.branches.find_one({"id": branch_id})
        if not existing_for_geo:
            raise HTTPException(status_code=404, detail="Branch not found")

        if (
            "allows_collaboration" in update_data
            or "is_collaboration_partner" in update_data
        ):
            from utils.collaboration_partner import assert_can_toggle_collaboration_flag

            assert_can_toggle_collaboration_flag(current_user)
            flag = resolve_collaboration_flag_from_payload(
                update_data, existing=existing_for_geo
            )
            apply_collaboration_flag(update_data, flag)

        if "location_id" in update_data or "branch" in update_data:
            location_ref = (update_data.get("location_id") or existing_for_geo.get("location_id") or "").strip()
            changing_location = (
                "location_id" in update_data
                and (update_data.get("location_id") or "") != (existing_for_geo.get("location_id") or "")
            )
            city = None
            state = None
            try:
                city, state = await resolve_city_for_write(db, location_ref)
            except HTTPException:
                # Keep unmigrated branches editable; only reject a newly chosen invalid city.
                if changing_location or is_uuid(location_ref):
                    raise
            if city:
                if "branch" in update_data and isinstance(update_data["branch"], dict):
                    apply_city_to_branch_doc(update_data, city, state)
                else:
                    update_data["location_id"] = city["id"]
                    update_data["branch.address.city"] = city.get("name") or ""
                    update_data["branch.address.state"] = (
                        (state.get("name") if state else None)
                        or city.get("state")
                        or ""
                    )

            code = ((update_data.get("branch") or existing_for_geo.get("branch") or {}).get("code") or "").strip()
            if code:
                await assert_unique_branch_code(db, code, exclude_id=branch_id)
            name = ((update_data.get("branch") or existing_for_geo.get("branch") or {}).get("name") or "").strip()
            if not existing_for_geo.get("slug"):
                update_data["slug"] = await ensure_unique_branch_slug(
                    db, name, exclude_id=branch_id, existing_slug=update_data.get("slug")
                )
            elif "slug" in update_data:
                update_data["slug"] = await ensure_unique_branch_slug(
                    db, name, exclude_id=branch_id, existing_slug=update_data.get("slug")
                )

        # $set replaces the whole `assignments` document. If the client omits
        # `course_schedule`, model_dump(exclude_unset=True) drops the key and Mongo
        # would erase saved batch data — carry forward the previous schedule.
        if "assignments" in update_data and isinstance(update_data["assignments"], dict):
            new_asg = update_data["assignments"]
            if "course_schedule" not in new_asg:
                prev_doc = await db.branches.find_one(
                    {"id": branch_id},
                    {"assignments.course_schedule": 1, "_id": 0},
                )
                prev_cs = (prev_doc or {}).get("assignments", {}).get("course_schedule")
                if prev_cs is not None:
                    new_asg["course_schedule"] = prev_cs
            update_data["assignments"] = prepare_assignments_for_write(new_asg)

        update_data["updated_at"] = datetime.utcnow()
        
        result = await db.branches.update_one(
            {"id": branch_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Branch not found")

        if "assignments" in update_data:
            refreshed = await db.branches.find_one({"id": branch_id})
            await sync_branch_courses_from_assignments(db, branch_id, refreshed)
        
        return {"message": "Branch updated successfully"}

    @staticmethod
    async def create_holiday(
        branch_id: str,
        holiday_data: HolidayCreate,
        current_user: dict = None
    ):
        """Create a new holiday for a branch."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")
        
        db = get_db()

        # Branch Manager permission check
        if current_role == "branch_manager":
            # Branch managers can only manage holidays for branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to manage holidays for this branch")
        elif current_role == UserRole.COACH_ADMIN and current_user.get("branch_id") != branch_id:
            raise HTTPException(status_code=403, detail="You can only add holidays to your own branch.")

        holiday = Holiday(
            **holiday_data.dict(),
            branch_id=branch_id
        )
        # Convert date to datetime for MongoDB serialization
        holiday_dict = holiday.dict()
        holiday_dict["date"] = datetime.combine(holiday_dict["date"], datetime.min.time())

        await db.holidays.insert_one(holiday_dict)
        return holiday

    @staticmethod
    async def get_holidays(
        branch_id: str,
        current_user: dict = None
    ):
        """Get all holidays for a specific branch."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only access holidays for branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to access holidays for this branch")

        db = get_db()
        holidays = await db.holidays.find({"branch_id": branch_id}).to_list(1000)
        return {"holidays": serialize_doc(holidays)}

    @staticmethod
    async def delete_holiday(
        branch_id: str,
        holiday_id: str,
        current_user: dict = None
    ):
        """Delete a holiday for a branch."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        db = get_db()

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only delete holidays for branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to manage holidays for this branch")
        elif current_user["role"] == UserRole.COACH_ADMIN and current_user.get("branch_id") != branch_id:
            raise HTTPException(status_code=403, detail="You can only delete holidays from your own branch.")

        result = await db.holidays.delete_one({"id": holiday_id, "branch_id": branch_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Holiday not found")
        return

    @staticmethod
    async def delete_branch(
        branch_id: str,
        current_user: dict = None
    ):
        """Delete branch (soft delete by setting is_active to False)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only delete branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to delete this branch")

        # Check if branch exists
        existing_branch = await db.branches.find_one({"id": branch_id})
        if not existing_branch:
            raise HTTPException(status_code=404, detail="Branch not found")

        # Soft delete by setting is_active to False
        result = await db.branches.update_one(
            {"id": branch_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Branch not found")

        return {"message": "Branch deleted successfully"}
