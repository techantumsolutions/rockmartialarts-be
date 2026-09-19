from fastapi import HTTPException, Depends
from typing import Optional, Dict, Any, Set
from datetime import datetime
import logging

from models.course_models import CourseCreate, CourseUpdate, Course
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.course_hierarchy import (
    UUID_RE,
    assert_course_hierarchy,
    courses_for_category_query,
    ensure_course_slug,
    name_slug,
)
from utils.branch_courses import available_course_ids_for_branch, assert_course_available_at_branch
from controllers.student_showcase_achievement_controller import list_for_course_with_fallback

logger = logging.getLogger(__name__)


def _expand_branch_prices_to_branch_pricing(branch_prices: list) -> Dict[str, Any]:
    """Build branch_id -> fee map from pricing.branch_prices (same rules as update_course)."""
    branch_pricing: Dict[str, Any] = {}
    if not branch_prices:
        return branch_pricing
    for bp in branch_prices:
        bid = bp.get("branch_id") if isinstance(bp, dict) else getattr(bp, "branch_id", None)
        if not bid:
            continue
        bp_fee_per_duration = bp.get("fee_per_duration") if isinstance(bp, dict) else getattr(bp, "fee_per_duration", None)
        if bp_fee_per_duration and isinstance(bp_fee_per_duration, dict):
            try:
                branch_pricing[bid] = {str(k): float(v) for k, v in bp_fee_per_duration.items() if v is not None}
            except (TypeError, ValueError):
                pass
        has_duration_fees = not branch_pricing.get(bid) and any(
            (bp.get(attr) if isinstance(bp, dict) else getattr(bp, attr, None)) is not None
            for attr in ("fee_1_month", "fee_3_months", "fee_6_months", "fee_1_year")
        )
        if not branch_pricing.get(bid) and has_duration_fees:
            branch_pricing[bid] = {}
            for key, attr in [("1-month", "fee_1_month"), ("3-months", "fee_3_months"), ("6-months", "fee_6_months"), ("1-year", "fee_1_year")]:
                val = bp.get(attr) if isinstance(bp, dict) else getattr(bp, attr, None)
                if val is not None:
                    branch_pricing[bid][key] = float(val)
        elif not branch_pricing.get(bid):
            amt = bp.get("amount") if isinstance(bp, dict) else getattr(bp, "amount", None)
            if amt is not None:
                branch_pricing[bid] = float(amt)
    return branch_pricing


def _merge_branch_pricing_delta(existing: Any, delta: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge duration fees per branch; preserve other branches and legacy values."""
    out: Dict[str, Any] = {}
    if isinstance(existing, dict):
        for k, v in existing.items():
            if isinstance(v, dict):
                out[k] = dict(v)
            else:
                out[k] = v
    for bid, new_val in delta.items():
        if isinstance(new_val, dict):
            cur = out.get(bid)
            if isinstance(cur, dict):
                out[bid] = {**cur, **new_val}
            else:
                out[bid] = dict(new_val)
        else:
            out[bid] = new_val
    return out


def _collect_coach_ids_from_branch_schedule(branch: dict) -> Set[str]:
    ids: Set[str] = set()
    sched = (branch.get("assignments") or {}).get("course_schedule") or []
    for entry in sched:
        if not isinstance(entry, dict):
            continue
        for b in entry.get("batches") or []:
            if not isinstance(b, dict):
                continue
            cid = str(b.get("coach_id") or b.get("coachId") or "").strip()
            if cid:
                ids.add(cid)
    return ids


async def _user_display_names_by_ids(db, user_ids: Set[str]) -> Dict[str, str]:
    """Map user id -> display name for batch trainers."""
    if not user_ids:
        return {}
    users = await db.users.find({"id": {"$in": list(user_ids)}}).to_list(length=300)
    out: Dict[str, str] = {}
    for u in users:
        fn = (u.get("first_name") or "").strip()
        ln = (u.get("last_name") or "").strip()
        label = (
            f"{fn} {ln}".strip()
            or (u.get("full_name") or "").strip()
            or (u.get("email") or "").strip()
            or "Trainer"
        )
        out[u["id"]] = label
    return out


def _public_branch_batches_for_course(
    branch: dict,
    course_id: str,
    coach_names: Optional[Dict[str, str]] = None,
) -> list:
    """Batches from branch course_schedule for registration (batch_ref matches payment-info)."""
    sched = (branch.get("assignments") or {}).get("course_schedule") or []
    for entry in sched:
        cid = str(entry.get("course_id") or entry.get("courseId") or "")
        if cid != str(course_id):
            continue
        batches = list(entry.get("batches") or [])
        out = []
        for i, b in enumerate(batches):
            bid = str((b.get("batch_id") or b.get("id") or "")).strip()
            ref = bid if bid else f"__index:{i}__"
            days = b.get("days") or []
            st = str((b.get("start_time") or b.get("startTime") or "")).strip()
            et = str((b.get("end_time") or b.get("endTime") or "")).strip()
            label_parts = []
            if days:
                label_parts.append(", ".join(str(d) for d in days))
            if st or et:
                label_parts.append(f"{st or '—'} – {et or '—'}")
            label = " · ".join(label_parts) if label_parts else f"Batch {i + 1}"
            raw_fee = b.get("batch_fee")
            if raw_fee is None:
                raw_fee = b.get("batchFee")
            if raw_fee is None:
                raw_fee = b.get("fee")
            if raw_fee is None:
                raw_fee = b.get("price")
            fee = None
            if raw_fee is not None:
                try:
                    fee = float(raw_fee)
                except (TypeError, ValueError):
                    fee = None
            bname = str((b.get("batch_name") or b.get("name") or "")).strip()
            coach_id = str((b.get("coach_id") or b.get("coachId") or "")).strip()
            trainer_name = None
            if coach_names and coach_id:
                trainer_name = coach_names.get(coach_id)
            fpd = b.get("fee_per_duration") if isinstance(b.get("fee_per_duration"), dict) else None
            row = {
                "batch_ref": ref,
                "name": bname or None,
                "label": label,
                "batch_fee": fee,
                "fee_per_duration": fpd,
                "days": days,
                "start_time": st,
                "end_time": et,
            }
            if coach_id:
                row["coach_id"] = coach_id
            if trainer_name:
                row["trainer_name"] = trainer_name
            out.append(row)
        return out
    return []


def _timing_field(t: Dict[str, Any], *keys: str) -> str:
    if not isinstance(t, dict):
        return ""
    for k in keys:
        v = t.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def format_branch_timings_display(timings_list: Any) -> str:
    """Build a single display string from branch operational_details.timings (flexible field names)."""
    if not timings_list or not isinstance(timings_list, (list, tuple)):
        return "—"
    parts: list[str] = []
    for t in timings_list[:24]:
        if not isinstance(t, dict):
            continue
        open_t = _timing_field(t, "open", "open_time", "start", "from")
        close_t = _timing_field(t, "close", "close_time", "end", "to")
        day = _timing_field(t, "day", "weekday")
        if open_t and close_t:
            parts.append(f"{day}: {open_t} to {close_t}" if day else f"{open_t} to {close_t}")
        elif open_t or close_t:
            slot = f"{open_t} – {close_t}".strip(" –") if (open_t and close_t) else (open_t or close_t)
            parts.append(f"{day}: {slot}" if day else slot)
    if parts:
        return " | ".join(parts)
    first = timings_list[0] if timings_list else None
    if isinstance(first, dict):
        open_t = _timing_field(first, "open", "open_time", "start", "from")
        close_t = _timing_field(first, "close", "close_time", "end", "to")
        if open_t and close_t:
            return f"{open_t} to {close_t}"
        if open_t or close_t:
            return open_t or close_t
    return "—"


def _format_course_batches_timings(course_id: str, schedule: Any) -> str:
    """Build display string from assignments.course_schedule for one course."""
    if not schedule or not isinstance(schedule, list):
        return ""
    entry = None
    for e in schedule:
        if isinstance(e, dict) and str(e.get("course_id", "")).strip() == str(course_id).strip():
            entry = e
            break
    if not entry:
        return ""
    batches = entry.get("batches") or []
    parts: list[str] = []
    for b in batches:
        if not isinstance(b, dict):
            continue
        days = b.get("days") or []
        ds = ", ".join(str(d) for d in days if d) if days else ""
        st = str(b.get("start_time") or "").strip()
        en = str(b.get("end_time") or "").strip()
        ts = f"{st} – {en}" if st and en else (st or en)
        if ds and ts:
            parts.append(f"{ds} · {ts}")
        elif ds:
            parts.append(ds)
        elif ts:
            parts.append(ts)
    return " | ".join(parts) if parts else ""


class CourseController:
    @staticmethod
    def _attach_public_about_fields(course_dict: Dict[str, Any]) -> None:
        """Flatten page_content.about_section for public APIs (aboutTitle, aboutDescription)."""
        page_content = course_dict.get("page_content") or {}
        about = page_content.get("about_section") or {}
        if not isinstance(about, dict):
            about = {}
        course_dict["aboutTitle"] = str(about.get("title") or about.get("aboutTitle") or "").strip()
        course_dict["aboutDescription"] = str(
            about.get("description") or about.get("aboutDescription") or ""
        ).strip()

    @staticmethod
    async def create_course(
        course_data: CourseCreate,
        current_user: dict = None
    ):
        """Create new course with comprehensive nested structure"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Check for duplicate course title
        existing_course = await db.courses.find_one({
            "title": {"$regex": f"^{course_data.title.strip()}$", "$options": "i"}
        })
        if existing_course:
            raise HTTPException(status_code=400, detail=f"A course with the name '{course_data.title}' already exists")

        category_id, sub_category = await assert_course_hierarchy(
            db, course_data.category_id, course_data.sub_category
        )
        payload = course_data.dict()
        payload["category_id"] = category_id
        payload["sub_category"] = sub_category
        payload["slug"] = await ensure_course_slug(
            db, course_data.title, existing_slug=payload.get("slug")
        )

        course = Course(**payload)

        # Store the course with nested structure exactly as provided
        course_dict = course.dict()
        settings = course_dict.setdefault("settings", {})
        if settings.get("active") is None:
            settings["active"] = True
        course_dict["settings"] = settings

        await db.courses.insert_one(course_dict)
        return {"message": "Course created successfully", "course_id": course.id}

    @staticmethod
    async def get_courses(
        category_id: Optional[str] = None,
        difficulty_level: Optional[str] = None,
        instructor_id: Optional[str] = None,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = None
    ):
        """Get courses with enhanced data including branch assignments, instructor counts, and student enrollments"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        filter_query = {}

        if active_only:
            filter_query["settings.active"] = True
        if category_id:
            cat_query = await courses_for_category_query(db, category_id)
            filter_query["$or"] = cat_query["$or"]
        if difficulty_level:
            filter_query["difficulty_level"] = difficulty_level
        if instructor_id:
            filter_query["instructor_id"] = instructor_id

        # Apply role-based filtering for branch managers
        current_role = current_user.get("role")
        managed_branch_ids = None

        if current_role == "branch_manager":
            # Branch managers can only see courses from their managed branches
            branch_manager_id = current_user.get("id")
            if not branch_manager_id:
                raise HTTPException(status_code=403, detail="Branch manager ID not found")

            # Find all branches managed by this branch manager
            managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)

            if not managed_branches:
                return {"courses": []}

            # Get all branch IDs managed by this branch manager
            managed_branch_ids = [branch["id"] for branch in managed_branches]
            print(f"Branch manager {branch_manager_id} manages branches for courses: {managed_branch_ids}")

        courses = await db.courses.find(filter_query).skip(skip).limit(limit).to_list(length=limit)

        # Enhance courses with additional data
        enhanced_courses = []
        for course in courses:
            # Get branch assignments for this course
            branch_query = {
                "assignments.courses": course["id"],
                "is_active": True
            }

            # For branch managers, only include branches they manage
            if managed_branch_ids is not None:
                branch_query["id"] = {"$in": managed_branch_ids}

            branches = await db.branches.find(branch_query).to_list(length=100)

            # For branch managers, skip courses that aren't assigned to any of their managed branches
            if managed_branch_ids is not None and len(branches) == 0:
                continue

            # Get instructor assignments (coaches assigned to this course)
            instructors = await db.coaches.find({
                "is_active": True,
                "$or": [
                    {"assignment_details.courses": course["id"]},
                    {"areas_of_expertise": course["id"]},
                ],
            }).to_list(length=100)

            # Get student enrollment count
            enrollment_count = await db.enrollments.count_documents({
                "course_id": course["id"],
                "is_active": True
            })

            # Create enhanced course object
            enhanced_course = serialize_doc(course)
            enhanced_course.update({
                "branch_assignments": [
                    {
                        "branch_id": branch["id"],
                        "branch_name": branch["branch"]["name"],
                        "branch_code": branch["branch"]["code"],
                        "location": f"{branch['branch']['address']['area']}, {branch['branch']['address']['city']}"
                    }
                    for branch in branches
                ],
                "instructor_count": len(instructors),
                "instructor_assignments": [
                    {
                        "instructor_id": instructor["id"],
                        "instructor_name": instructor.get("full_name", f"{instructor.get('first_name', '')} {instructor.get('last_name', '')}".strip()),
                        "email": instructor.get("email", instructor.get("contact_info", {}).get("email", ""))
                    }
                    for instructor in instructors
                ],
                "student_enrollment_count": enrollment_count,
                # Add display fields for frontend compatibility
                "name": course["title"],  # Map title to name for frontend
                "branches": len(branches),  # Number of branches
                "masters": len(instructors),  # Number of instructors
                "students": enrollment_count,  # Number of students
                "icon": "🥋",  # Default icon for martial arts courses
                "enabled": course.get("settings", {}).get("active", True)
            })

            enhanced_courses.append(enhanced_course)

        return {"courses": enhanced_courses}

    @staticmethod
    async def get_course(
        course_id: str,
        current_user: dict = None
    ):
        """Get course by ID with nested structure"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        if current_user.get("role") == UserRole.BRANCH_MANAGER:
            manager_id = current_user.get("id")
            managed_branches = await db.branches.find({"manager_id": manager_id, "is_active": True}).to_list(length=None)
            if not any(
                course_id in (b.get("assignments") or {}).get("courses", [])
                for b in (managed_branches or [])
            ):
                raise HTTPException(status_code=404, detail="Course not found")

        return serialize_doc(course)

    @staticmethod
    async def get_public_course_by_slug(slug: str):
        """Public course lookup by slug, with id / title / code fallback for existing URLs."""
        db = get_db()
        value = (slug or "").strip()
        if not value:
            raise HTTPException(status_code=404, detail="Course not found")

        lowered = value.lower()
        course = await db.courses.find_one({"slug": lowered, "settings.active": True})
        if not course and UUID_RE.match(value):
            course = await db.courses.find_one({"id": value, "settings.active": True})
        if not course:
            candidates = await db.courses.find({"settings.active": True}).to_list(500)
            by_title = next((item for item in candidates if name_slug(item.get("title")) == lowered), None)
            by_code = next((item for item in candidates if name_slug(item.get("code")) == lowered), None)
            course = by_title or by_code
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        return await CourseController.get_public_course_detail(course["id"])

    @staticmethod
    async def get_courses_by_branch(
        branch_id: str,
        current_user: dict = None
    ):
        """Get courses assigned to a specific branch"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Apply role-based access control
            current_role = current_user.get("role")
            if current_role == "branch_manager":
                # Branch managers can only access courses from their managed branches
                # Get the branch assignment from the branch manager's profile
                branch_assignment = current_user.get("branch_assignment")
                if branch_assignment and branch_assignment.get("branch_id"):
                    managed_branch_id = branch_assignment["branch_id"]
                    if managed_branch_id != branch_id:
                        raise HTTPException(
                            status_code=403,
                            detail="You can only access courses from your managed branch"
                        )
                else:
                    raise HTTPException(status_code=403, detail="No branch assigned to this manager")

            # First, get the branch to find assigned courses
            branch = await db.branches.find_one({"id": branch_id})
            if not branch:
                raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

            # Get course IDs assigned and available at this branch
            course_ids = await available_course_ids_for_branch(db, branch)

            if not course_ids:
                return {"courses": [], "total": 0}

            # Fetch course details for assigned course IDs
            courses = await db.courses.find({
                "id": {"$in": course_ids},
                "settings.active": True
            }).to_list(length=100)

            # Enhance courses with additional data
            enhanced_courses = []
            for course in courses:
                # Get instructor assignments (coaches assigned to this course at this branch)
                instructors = await db.coaches.find({
                    "branch_id": branch_id,
                    "is_active": True,
                    "$or": [
                        {"assignment_details.courses": course["id"]},
                        {"areas_of_expertise": course["id"]},
                    ],
                }).to_list(length=100)

                # Get student enrollment count for this course at this branch
                enrollment_count = await db.enrollments.count_documents({
                    "course_id": course["id"],
                    "branch_id": branch_id,
                    "is_active": True
                })

                # Create enhanced course object
                enhanced_course = serialize_doc(course)
                # Use 'title' field for course name (not 'name')
                enhanced_course.update({
                    "name": course.get("title", course.get("name", "Unknown Course")),
                    "enrolled_students": enrollment_count,
                    "instructor_name": instructors[0].get("full_name", f"{instructors[0].get('first_name', '')} {instructors[0].get('last_name', '')}".strip()) if instructors else None,
                    "instructor_count": len(instructors),
                    "difficulty_level": course.get("difficulty_level", "Beginner")
                })
                enhanced_courses.append(enhanced_course)

            return {"courses": enhanced_courses, "total": len(enhanced_courses)}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    @staticmethod
    async def update_course(
        course_id: str,
        course_update: CourseUpdate,
        current_user: dict = None
    ):
        """Update course with nested structure"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        
        # Check if course exists
        existing_course = await db.courses.find_one({"id": course_id})
        if not existing_course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        bm_managed_branches = None

        # Coach Admin permission check
        if current_user["role"] == UserRole.COACH_ADMIN:
            # Check if user is the instructor of this course or can manage it
            if existing_course.get("instructor_id") != current_user["id"]:
                raise HTTPException(status_code=403, detail="You can only update courses where you are the instructor.")

        # Branch Manager permission check
        elif current_user["role"] == UserRole.BRANCH_MANAGER:
            # Check if this course is assigned to any branch managed by this branch manager
            manager_id = current_user["id"]

            # Find branches managed by this branch manager
            bm_managed_branches = await db.branches.find({"manager_id": manager_id, "is_active": True}).to_list(length=None)

            if not bm_managed_branches:
                raise HTTPException(status_code=403, detail="You don't manage any branches.")

            # Check if the course is assigned to any of the managed branches
            course_assigned_to_managed_branch = False
            for branch in bm_managed_branches:
                assigned_courses = branch.get("assignments", {}).get("courses", [])
                if course_id in assigned_courses:
                    course_assigned_to_managed_branch = True
                    break

            if not course_assigned_to_managed_branch:
                raise HTTPException(status_code=403, detail="You can only update courses assigned to branches you manage.")

        # Branch managers may only merge branch-specific fees (branch_pricing) for their branches
        if current_user["role"] == UserRole.BRANCH_MANAGER:
            managed_branches = bm_managed_branches or []
            managed_ids = {b["id"] for b in managed_branches}
            raw = course_update.dict(exclude_unset=True)
            if not raw:
                raise HTTPException(status_code=400, detail="No update data provided")
            pricing = raw.get("pricing")
            if not isinstance(pricing, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Branch managers can only update branch-specific fees. Send pricing.branch_prices for your branch.",
                )
            branch_prices = pricing.get("branch_prices") or []
            delta_bp = _expand_branch_prices_to_branch_pricing(branch_prices)
            if not delta_bp:
                raise HTTPException(
                    status_code=400,
                    detail="Provide at least one branch fee in pricing.branch_prices for your branch.",
                )
            for bid in delta_bp.keys():
                if bid not in managed_ids:
                    raise HTTPException(status_code=403, detail=f"Cannot set fees for branch {bid}")
                branch_doc = next((b for b in managed_branches if b["id"] == bid), None)
                if not branch_doc:
                    raise HTTPException(status_code=403, detail=f"Cannot set fees for branch {bid}")
                assigned = branch_doc.get("assignments", {}).get("courses", [])
                if course_id not in assigned:
                    raise HTTPException(
                        status_code=403,
                        detail="This course is not assigned to that branch.",
                    )
            merged_bp = _merge_branch_pricing_delta(existing_course.get("branch_pricing"), delta_bp)
            update_data = {"branch_pricing": merged_bp, "updated_at": datetime.utcnow()}
            result = await db.courses.update_one({"id": course_id}, {"$set": update_data})
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Course not found")
            return {"message": "Course updated successfully"}

        update_data = {k: v for k, v in course_update.dict(exclude_unset=True).items()}
        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        # Check for duplicate course title on update
        if "title" in update_data and update_data["title"]:
            title_conflict = await db.courses.find_one({
                "title": {"$regex": f"^{update_data['title'].strip()}$", "$options": "i"},
                "id": {"$ne": course_id}
            })
            if title_conflict:
                raise HTTPException(status_code=400, detail=f"A course with the name '{update_data['title']}' already exists")

        if "category_id" in update_data or "sub_category" in update_data:
            cat_id = update_data.get("category_id") or existing_course.get("category_id")
            sub_id = update_data["sub_category"] if "sub_category" in update_data else existing_course.get("sub_category")
            cat_id, sub_id = await assert_course_hierarchy(db, cat_id, sub_id)
            update_data["category_id"] = cat_id
            update_data["sub_category"] = sub_id

        if update_data.get("slug"):
            update_data["slug"] = await ensure_course_slug(
                db,
                update_data.get("title") or existing_course.get("title") or "course",
                exclude_id=course_id,
                existing_slug=update_data.get("slug"),
            )
        elif not existing_course.get("slug"):
            update_data["slug"] = await ensure_course_slug(
                db,
                update_data.get("title") or existing_course.get("title") or "course",
                exclude_id=course_id,
            )

        # When pricing is updated, set base_fee / fee_per_duration / branch_pricing for payment API
        if "pricing" in update_data:
            p = update_data["pricing"]
            if isinstance(p, dict):
                base_fee = p.get("fee_1_month") if p.get("fee_1_month") is not None else p.get("amount")
                if base_fee is not None:
                    update_data["base_fee"] = float(base_fee)
                # Support fee_per_duration keyed by duration id (from master data) or legacy keys
                fee_per_duration = {}
                if p.get("fee_per_duration") and isinstance(p.get("fee_per_duration"), dict):
                    for k, v in p["fee_per_duration"].items():
                        if v is not None:
                            try:
                                fee_per_duration[str(k)] = float(v)
                            except (TypeError, ValueError):
                                pass
                if not fee_per_duration:
                    for key, attr in [("1-month", "fee_1_month"), ("3-months", "fee_3_months"), ("6-months", "fee_6_months"), ("1-year", "fee_1_year")]:
                        if p.get(attr) is not None:
                            fee_per_duration[key] = float(p[attr])
                if fee_per_duration:
                    update_data["fee_per_duration"] = fee_per_duration
                # Persist flat/offer prices to top-level for payment API
                flat_price_per_duration = {}
                if p.get("flat_price_per_duration") and isinstance(p.get("flat_price_per_duration"), dict):
                    for k, v in p["flat_price_per_duration"].items():
                        if v is not None:
                            try:
                                flat_price_per_duration[str(k)] = float(v)
                            except (TypeError, ValueError):
                                pass
                if flat_price_per_duration:
                    update_data["flat_price_per_duration"] = flat_price_per_duration
                branch_prices = p.get("branch_prices") or []
                if branch_prices:
                    branch_pricing = {}
                    for bp in branch_prices:
                        bid = bp.get("branch_id") if isinstance(bp, dict) else getattr(bp, "branch_id", None)
                        if not bid:
                            continue
                        bp_fee_per_duration = bp.get("fee_per_duration") if isinstance(bp, dict) else getattr(bp, "fee_per_duration", None)
                        if bp_fee_per_duration and isinstance(bp_fee_per_duration, dict):
                            try:
                                branch_pricing[bid] = {str(k): float(v) for k, v in bp_fee_per_duration.items() if v is not None}
                            except (TypeError, ValueError):
                                pass
                        has_duration_fees = not branch_pricing.get(bid) and any(
                            (bp.get(attr) if isinstance(bp, dict) else getattr(bp, attr, None)) is not None
                            for attr in ("fee_1_month", "fee_3_months", "fee_6_months", "fee_1_year")
                        )
                        if not branch_pricing.get(bid) and has_duration_fees:
                            branch_pricing[bid] = {}
                            for key, attr in [("1-month", "fee_1_month"), ("3-months", "fee_3_months"), ("6-months", "fee_6_months"), ("1-year", "fee_1_year")]:
                                val = bp.get(attr) if isinstance(bp, dict) else getattr(bp, attr, None)
                                if val is not None:
                                    branch_pricing[bid][key] = float(val)
                        elif not branch_pricing.get(bid):
                            amt = bp.get("amount") if isinstance(bp, dict) else getattr(bp, "amount", None)
                            if amt is not None:
                                branch_pricing[bid] = float(amt)
                    if branch_pricing:
                        update_data["branch_pricing"] = branch_pricing

        update_data["updated_at"] = datetime.utcnow()
        
        result = await db.courses.update_one(
            {"id": course_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Course not found")
        
        return {"message": "Course updated successfully"}

    @staticmethod
    async def get_course_stats(
        course_id: str,
        current_user: dict = None
    ):
        """Get statistics for a specific course."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        active_enrollments = await db.enrollments.count_documents({"course_id": course_id, "is_active": True})

        stats = {
            "course_details": serialize_doc(course),
            "active_enrollments": active_enrollments
        }
        return stats

    @staticmethod
    async def delete_course(
        course_id: str,
        current_user: dict = None
    ):
        """Delete course (soft delete by setting active to False)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Check if course exists
        existing_course = await db.courses.find_one({"id": course_id})
        if not existing_course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Soft delete by setting settings.active to False
        result = await db.courses.update_one(
            {"id": course_id},
            {"$set": {"settings.active": False, "updated_at": datetime.utcnow()}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Course not found")

        return {"message": "Course deleted successfully"}

    @staticmethod
    async def get_public_courses(
    active_only: bool = True,
    skip: int = 0,
    limit: int = 500
):
        """Get all courses with enhanced data - Public endpoint (no authentication required)"""
        db = get_db()

        # Build query
        query = {}
        if active_only:
            query["settings.active"] = True

        # Apply pagination
        if limit > 500:
            limit = 500

        courses_cursor = db.courses.find(query).sort("created_at", 1).skip(skip).limit(limit)
        courses = await courses_cursor.to_list(limit)

        # Enhance courses with additional data
        enhanced_courses = []
        for course in courses:
            # Get branch assignments for this course
            branches = await db.branches.find({
                "assignments.courses": course["id"],
                "is_active": True
            }).to_list(length=100)
            available_branches = []
            for branch in branches:
                allowed = await available_course_ids_for_branch(db, branch)
                if course["id"] in allowed:
                    available_branches.append(branch)
            branches = available_branches

            # Get instructor assignments (coaches assigned to this course)
            instructors = await db.coaches.find({
                "is_active": True,
                "$or": [
                    {"assignment_details.courses": course["id"]},
                    {"areas_of_expertise": course["id"]},
                ],
            }).to_list(length=100)

            # Get student enrollment count
            enrollment_count = await db.enrollments.count_documents({
                "course_id": course["id"],
                "is_active": True
            })

            # Create enhanced course object
            enhanced_course = serialize_doc(course)
            enhanced_course.update({
                "branch_assignments": [
                    {
                        "branch_id": branch["id"],
                        "branch_name": branch["branch"]["name"],
                        "branch_code": branch["branch"]["code"],
                        "location": f"{branch['branch']['address']['area']}, {branch['branch']['address']['city']}"
                    }
                    for branch in branches
                ],
                "instructor_count": len(instructors),
                "instructor_assignments": [
                    {
                        "instructor_id": instructor["id"],
                        "instructor_name": instructor.get("full_name", f"{instructor.get('first_name', '')} {instructor.get('last_name', '')}".strip()),
                        "email": instructor.get("email", instructor.get("contact_info", {}).get("email", ""))
                    }
                    for instructor in instructors
                ],
                "student_enrollment_count": enrollment_count,
                # Add display fields for frontend compatibility
                "name": course["title"],  # Map title to name for frontend
                "branches": len(branches),  # Number of branches
                "masters": len(instructors),  # Number of instructors
                "students": enrollment_count,  # Number of students
                "icon": "🥋",  # Default icon for martial arts courses
                "enabled": course.get("settings", {}).get("active", True)
            })
            CourseController._attach_public_about_fields(enhanced_course)

            enhanced_courses.append(enhanced_course)

        # Get total count
        total = await db.courses.count_documents(query)

        logger.info(
            "public courses list: page_size=%s total_in_db=%s active_only=%s skip=%s",
            len(enhanced_courses),
            total,
            active_only,
            skip,
        )

        return {
            "message": f"Retrieved {len(enhanced_courses)} courses successfully",
            "courses": enhanced_courses,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    async def get_public_course_detail(course_id: str):
        """Public: full course detail with statistics, curriculum, enrolled students, reviews, student achievements."""
        db = get_db()
        course = await db.courses.find_one({"id": course_id, "settings.active": True})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Statistics
        branches = await db.branches.find({
            "assignments.courses": course_id,
            "is_active": True
        }).to_list(length=100)
        available_branches = []
        for b in branches:
            allowed = await available_course_ids_for_branch(db, b)
            if course_id in allowed:
                available_branches.append(b)
        branches = available_branches
        instructors = await db.coaches.find({
            "is_active": True,
            "$or": [
                {"assignment_details.courses": course_id},
                {"areas_of_expertise": course_id},
            ],
        }).to_list(length=100)
        enrollment_count = await db.enrollments.count_documents({
            "course_id": course_id,
            "is_active": True
        })
        statistics = {
            "enrolled_count": enrollment_count,
            "branches_count": len(branches),
            "instructors_count": len(instructors),
        }

        # Curriculum from course_content.syllabus (split into lines)
        course_content = course.get("course_content") or {}
        syllabus_text = course_content.get("syllabus") or ""
        curriculum = [s.strip() for s in syllabus_text.split("\n") if s.strip()] if syllabus_text else []

        # Enrolled students (limit 24 for display)
        enrollments = await db.enrollments.find(
            {"course_id": course_id, "is_active": True}
        ).limit(24).to_list(length=24)
        student_ids = list({e["student_id"] for e in enrollments})
        users = await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids))
        user_map = {u["id"]: u for u in users}
        enrolled_students = []
        for sid in student_ids:
            u = user_map.get(sid)
            if not u:
                continue
            enrolled_students.append({
                "id": u.get("id"),
                "first_name": u.get("first_name", ""),
                "last_name": u.get("last_name", ""),
                "full_name": (u.get("full_name") or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Student"),
                "photo": u.get("photo") or u.get("profile_photo") or u.get("profile_image"),
            })

        # Student reviews = testimonials from page_content
        page_content = course.get("page_content") or {}
        student_reviews = page_content.get("testimonials") or []

        # Student achievements: achievements of students enrolled in this course
        all_enrollment_cursor = await db.enrollments.find(
            {"course_id": course_id, "is_active": True}
        ).to_list(length=500)
        all_student_ids = list({e["student_id"] for e in all_enrollment_cursor})
        achievements_cursor = db.student_achievements.find({
            "student_id": {"$in": all_student_ids},
            "is_deleted": False
        }).sort("created_at", -1).limit(30)
        achievement_docs = await achievements_cursor.to_list(length=30)
        ach_user_ids = list({a["student_id"] for a in achievement_docs})
        ach_users = await db.users.find({"id": {"$in": ach_user_ids}}).to_list(length=len(ach_user_ids))
        ach_user_map = {u["id"]: (u.get("full_name") or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Student") for u in ach_users}
        student_achievements = []
        for a in achievement_docs:
            d = serialize_doc(a)
            d["student_name"] = ach_user_map.get(a["student_id"], "Student")
            student_achievements.append(d)

        fallback_branch_id = branches[0]["id"] if branches else None
        showcase_achievements = await list_for_course_with_fallback(
            course_id, fallback_branch_id=fallback_branch_id, limit=30
        )

        branches_offering = []
        for b in branches:
            info = b.get("branch") or {}
            addr = info.get("address") or {}
            name = info.get("name") or b.get("name") or "Branch"
            area = (addr.get("area") or "").strip()
            city = (addr.get("city") or "").strip()
            location = ", ".join([p for p in (area, city) if p]) or name
            slug = (b.get("slug") or name_slug(name) or b.get("id") or "").strip().lower()
            branches_offering.append({
                "id": b["id"],
                "branch_id": b["id"],
                "name": name,
                "branch_name": name,
                "slug": slug,
                "location": location,
                "area": area,
                "city": city,
                "state": (addr.get("state") or "").strip(),
            })

        assigned_coaches = []
        for instructor in instructors:
            prof = instructor.get("professional_info") or {}
            contact = instructor.get("contact_info") or {}
            assigned_coaches.append({
                "id": instructor.get("id"),
                "name": instructor.get("full_name")
                or f"{instructor.get('first_name', '')} {instructor.get('last_name', '')}".strip()
                or "Coach",
                "designation": prof.get("designation_id") or "Coach",
                "bio": instructor.get("about_short") or "",
                "photo": instructor.get("profile_image_url") or "",
                "email": instructor.get("email") or contact.get("email") or "",
            })

        serialized_course = serialize_doc(course)
        CourseController._attach_public_about_fields(serialized_course)
        serialized_course.pop("description", None)
        stored_slug = (course.get("slug") or name_slug(course.get("title") or course.get("code")) or course["id"]).strip().lower()
        serialized_course["slug"] = stored_slug
        serialized_course["branch_assignments"] = [
            {
                "branch_id": item["branch_id"],
                "branch_name": item["branch_name"],
                "location": item.get("location") or "",
            }
            for item in branches_offering
        ]
        serialized_course["branches_offering"] = branches_offering

        category_payload = None
        category_id = course.get("category_id")
        if category_id:
            category = await db.categories.find_one({"id": category_id, "is_active": True})
            if category:
                category_payload = {
                    "id": category["id"],
                    "name": category.get("name"),
                    "slug": (category.get("slug") or name_slug(category.get("name")) or category["id"]).strip().lower(),
                }
        serialized_course["category"] = category_payload

        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)
        serialized_course["available_durations"] = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "duration_months": d.get("duration_months"),
                "code": d.get("code"),
            }
            for d in durations
        ]

        return {
            "course": serialized_course,
            "category": category_payload,
            "statistics": statistics,
            "curriculum": curriculum,
            "enrolled_students": enrolled_students,
            "student_reviews": student_reviews,
            "student_achievements": student_achievements,
            "showcase_achievements": showcase_achievements,
            "branches_offering": branches_offering,
            "assigned_coaches": assigned_coaches,
            "instructor_assignments": assigned_coaches,
        }

    @staticmethod
    async def get_public_course_branch_info(course_id: str, branch_id: str):
        """Public: for course detail page - get duration, price, timings for a selected branch."""
        db = get_db()
        course = await db.courses.find_one({"id": course_id, "settings.active": True})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        branch = await db.branches.find_one({"id": branch_id, "is_active": True})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        await assert_course_available_at_branch(
            db, branch_id, course_id, require_active_branch=False, require_active_course=True
        )

        branch_name = (branch.get("branch") or {}).get("name") or branch.get("name") or "Branch"
        timings_list = (branch.get("operational_details") or {}).get("timings") or []
        timings_display = format_branch_timings_display(timings_list)
        batch_timings = _format_course_batches_timings(
            course_id, (branch.get("assignments") or {}).get("course_schedule")
        )
        if batch_timings:
            timings_display = batch_timings

        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).limit(20).to_list(length=20)
        duration_display = "—"
        if durations:
            first_d = durations[0]
            duration_display = first_d.get("name") or f"{first_d.get('duration_months', 1)} month(s)"

        pricing = course.get("pricing") or {}
        branch_prices = pricing.get("branch_prices") or []
        branch_entry = next((b for b in branch_prices if b.get("branch_id") == branch_id), None)
        amount = None
        if branch_entry:
            amount = branch_entry.get("amount") or branch_entry.get("fee_1_month") or branch_entry.get("fee_1_year")
            fee_per_duration = branch_entry.get("fee_per_duration")
            if amount is None and fee_per_duration and isinstance(fee_per_duration, dict):
                vals = [v for v in fee_per_duration.values() if v is not None]
                amount = vals[0] if vals else None
        if amount is None:
            amount = pricing.get("amount") or pricing.get("fee_1_month") or pricing.get("fee_1_year")
        currency = (branch_entry or {}).get("currency") or pricing.get("currency", "INR")
        price_display = f"{currency} {amount}" if amount is not None else "—"
        if currency == "INR":
            price_display = f"₹ {amount}" if amount is not None else "—"

        branch_fee_per_duration = None
        if branch_entry and isinstance(branch_entry.get("fee_per_duration"), dict):
            branch_fee_per_duration = branch_entry.get("fee_per_duration")

        return {
            "branch_id": branch_id,
            "branch_name": branch_name,
            "duration": duration_display,
            "price_display": price_display,
            "timings": timings_display,
            "fee_per_duration": branch_fee_per_duration,
        }

    @staticmethod
    async def get_public_courses_by_branch(branch_id: str):
        """Get courses assigned to a branch with details and fees - Public (no auth)."""
        db = get_db()
        branch = await db.branches.find_one({"id": branch_id, "is_active": True})
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        course_ids = await available_course_ids_for_branch(db, branch)
        if not course_ids:
            timings = (branch.get("operational_details") or {}).get("timings", [])
            return {"courses": [], "branch_timings": timings}
        courses = await db.courses.find({
            "id": {"$in": course_ids},
            "settings.active": True
        }).to_list(length=100)
        branch_timings = (branch.get("operational_details") or {}).get("timings", [])
        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)
        coach_ids = _collect_coach_ids_from_branch_schedule(branch)
        coach_names = await _user_display_names_by_ids(db, coach_ids)
        result_courses = []
        for course in courses:
            serialized = serialize_doc(course)
            pricing = course.get("pricing") or {}
            branch_prices = pricing.get("branch_prices") or []
            branch_entry = next((b for b in branch_prices if b.get("branch_id") == branch_id), None)
            if branch_entry:
                fees = {
                    "currency": branch_entry.get("currency") or pricing.get("currency", "INR"),
                    "amount": branch_entry.get("amount"),
                    "fee_1_month": branch_entry.get("fee_1_month"),
                    "fee_3_months": branch_entry.get("fee_3_months"),
                    "fee_6_months": branch_entry.get("fee_6_months"),
                    "fee_1_year": branch_entry.get("fee_1_year"),
                    "fee_per_duration": branch_entry.get("fee_per_duration"),
                }
            else:
                fees = {
                    "currency": pricing.get("currency", "INR"),
                    "amount": pricing.get("amount"),
                    "fee_1_month": pricing.get("fee_1_month"),
                    "fee_3_months": pricing.get("fee_3_months"),
                    "fee_6_months": pricing.get("fee_6_months"),
                    "fee_1_year": pricing.get("fee_1_year"),
                    "fee_per_duration": pricing.get("fee_per_duration"),
                }
            # Live prices come from super-admin batch setup, not leftover course catalog amounts.
            branch_batches = _public_branch_batches_for_course(
                branch, serialized.get("id"), coach_names
            )
            live_fpd: dict = {}
            live_amounts = []
            for bat in branch_batches:
                fpd = bat.get("fee_per_duration") or {}
                if isinstance(fpd, dict):
                    for k, v in fpd.items():
                        if v is None:
                            continue
                        try:
                            fv = float(v)
                        except (TypeError, ValueError):
                            continue
                        live_amounts.append(fv)
                        if k not in live_fpd or fv < float(live_fpd[k]):
                            live_fpd[k] = fv
                bf = bat.get("batch_fee")
                if bf is not None:
                    try:
                        live_amounts.append(float(bf))
                    except (TypeError, ValueError):
                        pass
            if live_fpd:
                fees["fee_per_duration"] = live_fpd
            if live_amounts:
                lowest = min(live_amounts)
                fees["amount"] = lowest
                fees["fee_1_month"] = lowest
            available_durations = []
            for d in durations:
                available_durations.append({
                    "id": d["id"],
                    "name": d.get("name", d.get("id", "")),
                    "code": d.get("code", d.get("id", "")),
                    "duration_months": d.get("duration_months", 1),
                    "pricing_multiplier": d.get("pricing_multiplier", 1.0),
                })
            result_courses.append({
                "id": serialized.get("id"),
                "title": serialized.get("title"),
                "code": serialized.get("code"),
                "description": serialized.get("description"),
                "difficulty_level": serialized.get("difficulty_level"),
                "category_id": serialized.get("category_id"),
                "media_resources": serialized.get("media_resources") or {},
                "pricing": fees,
                "available_durations": available_durations,
                "branch_batches": branch_batches,
            })
        return {"courses": result_courses, "branch_timings": branch_timings}

    @staticmethod
    async def get_courses_by_category(
        category_id: str,
        difficulty_level: Optional[str] = None,
        active_only: bool = True,
        include_durations: bool = True,
        skip: int = 0,
        limit: int = 50
    ):
        """Get all courses filtered by category - Public endpoint"""
        db = get_db()

        # Verify category exists
        category = await db.categories.find_one({"id": category_id})
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

        # Build query (include courses linked via child subcategories)
        query = await courses_for_category_query(db, category_id)
        extra = {}
        if difficulty_level:
            extra["difficulty_level"] = difficulty_level
        if active_only:
            extra["settings.active"] = True
        if extra:
            query = {"$and": [query, extra]}

        # Apply pagination
        if limit > 100:
            limit = 100

        # Get courses
        courses_cursor = db.courses.find(query).skip(skip).limit(limit)
        courses = await courses_cursor.to_list(limit)

        # Get total count
        total = await db.courses.count_documents(query)

        # Enrich courses with additional data
        enriched_courses = []
        for course in courses:
            # Get available durations
            available_durations = []
            if include_durations:
                durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)
                base_price = course.get("pricing", {}).get("amount", 0)

                for duration in durations:
                    multiplier = duration.get("pricing_multiplier", 1.0)
                    duration_data = {
                        "id": duration["id"],
                        "name": duration["name"],
                        "duration_months": duration["duration_months"],
                        "pricing_multiplier": multiplier
                    }
                    available_durations.append(duration_data)

            # Get locations where this course is offered
            branches = await db.branches.find({
                "assignments.courses": course["id"],
                "is_active": True
            }).to_list(100)

            location_map = {}
            for branch in branches:
                allowed = await available_course_ids_for_branch(db, branch)
                if course["id"] not in allowed:
                    continue
                city = branch["branch"]["address"]["city"]
                if city not in location_map:
                    # Try to find location record
                    location = await db.locations.find_one({
                        "name": {"$regex": city, "$options": "i"},
                        "is_active": True
                    })
                    location_map[city] = {
                        "location_id": location["id"] if location else None,
                        "location_name": location["name"] if location else city,
                        "branch_count": 1
                    }
                else:
                    location_map[city]["branch_count"] += 1

            course_data = {
                "id": course["id"],
                "title": course["title"],
                "code": course["code"],
                "slug": course.get("slug"),
                "description": course["description"],
                "difficulty_level": course["difficulty_level"],
                "pricing": {
                    "currency": course.get("pricing", {}).get("currency", "INR"),
                    "amount": course.get("pricing", {}).get("amount", 0)
                },
                "student_requirements": course.get("student_requirements", {}),
                "available_durations": available_durations,
                "locations_offered": list(location_map.values())
            }
            enriched_courses.append(course_data)

        return {
            "message": f"Retrieved {len(enriched_courses)} courses for category successfully",
            "category": {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"]
            },
            "courses": enriched_courses,
            "total": total
        }

    @staticmethod
    async def get_courses_by_location(
        location_id: str,
        category_id: Optional[str] = None,
        difficulty_level: Optional[str] = None,
        include_durations: bool = True,
        include_branches: bool = False,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 50
    ):
        """Get courses available at a specific location - Public endpoint"""
        db = get_db()

        # Verify location exists
        location = await db.locations.find_one({"id": location_id})
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")

        # Find branches in this location
        branches = await db.branches.find({
            "branch.address.city": {"$regex": location["name"], "$options": "i"},
            "is_active": True
        }).to_list(100)

        if not branches:
            return {
                "message": "No branches found for this location",
                "location": {
                    "id": location["id"],
                    "name": location["name"],
                    "code": location["code"],
                    "branch_count": 0
                },
                "courses": [],
                "total": 0
            }

        # Get all course IDs offered at these branches
        course_ids = set()
        branch_course_map = {}

        for branch in branches:
            branch_courses = await available_course_ids_for_branch(db, branch)
            course_ids.update(branch_courses)

            for course_id in branch_courses:
                if course_id not in branch_course_map:
                    branch_course_map[course_id] = []
                branch_course_map[course_id].append({
                    "id": branch["id"],
                    "name": branch["branch"]["name"],
                    "code": branch["branch"]["code"],
                    "area": branch["branch"]["address"]["area"]
                })

        # Build course query
        course_query = {"id": {"$in": list(course_ids)}}
        if category_id:
            cat_query = await courses_for_category_query(db, category_id)
            course_query = {"$and": [course_query, cat_query]}
        if difficulty_level:
            course_query["difficulty_level"] = difficulty_level
        if active_only:
            course_query["settings.active"] = True

        # Apply pagination
        if limit > 100:
            limit = 100

        # Get courses
        courses_cursor = db.courses.find(course_query).skip(skip).limit(limit)
        courses = await courses_cursor.to_list(limit)

        # Get total count
        total = await db.courses.count_documents(course_query)

        # Enrich courses with additional data
        enriched_courses = []
        for course in courses:
            # Get category info
            category = await db.categories.find_one({"id": course["category_id"]})

            # Get available durations
            available_durations = []
            if include_durations:
                durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)
                base_price = course.get("pricing", {}).get("amount", 0)

                for duration in durations:
                    multiplier = duration.get("pricing_multiplier", 1.0)
                    final_price = base_price * multiplier
                    duration_data = {
                        "id": duration["id"],
                        "name": duration["name"],
                        "duration_months": duration["duration_months"],
                        "final_price": final_price
                    }
                    available_durations.append(duration_data)

            # Get branches offering this course
            branches_offering = []
            if include_branches:
                branches_offering = branch_course_map.get(course["id"], [])

            course_data = {
                "id": course["id"],
                "title": course["title"],
                "code": course["code"],
                "description": course["description"],
                "category": {
                    "id": category["id"] if category else None,
                    "name": category["name"] if category else "Unknown",
                    "code": category["code"] if category else "UNK"
                },
                "difficulty_level": course["difficulty_level"],
                "pricing": {
                    "currency": course.get("pricing", {}).get("currency", "INR"),
                    "amount": course.get("pricing", {}).get("amount", 0)
                },
                "available_durations": available_durations,
                "branches_offering": branches_offering
            }
            enriched_courses.append(course_data)

        return {
            "message": f"Retrieved {len(enriched_courses)} courses for location successfully",
            "location": {
                "id": location["id"],
                "name": location["name"],
                "code": location["code"],
                "branch_count": len(branches)
            },
            "courses": enriched_courses,
            "total": total
        }
