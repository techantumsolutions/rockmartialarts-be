import logging
import re
from fastapi import HTTPException, Depends, status
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

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
    """Add trainer_name and batch capacity to each batch in assignments.course_schedule (mutates branch)."""
    asg = branch.get("assignments")
    if not isinstance(asg, dict):
        return
    sched = asg.get("course_schedule")
    if not isinstance(sched, list) or not sched:
        return
    branch_id = str(branch.get("id") or "").strip()
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
    name_map = {}
    if ids:
        users = await db.users.find({"id": {"$in": list(ids)}}).to_list(length=300)
        for u in users:
            fn = (u.get("first_name") or "").strip()
            ln = (u.get("last_name") or "").strip()
            name_map[u["id"]] = (
                f"{fn} {ln}".strip()
                or (u.get("full_name") or "").strip()
                or (u.get("email") or "").strip()
                or "Trainer"
            )
        missing_ids = [i for i in ids if i not in name_map]
        if missing_ids:
            coaches = await db.coaches.find({"id": {"$in": missing_ids}}).to_list(length=300)
            for c in coaches:
                pinfo = c.get("personal_info") or {}
                fn = (pinfo.get("first_name") or "").strip()
                ln = (pinfo.get("last_name") or "").strip()
                name_map[c["id"]] = (
                    (c.get("full_name") or "").strip()
                    or f"{fn} {ln}".strip()
                    or ((c.get("contact_info") or {}).get("email") or "").strip()
                    or "Trainer"
                )
    for entry in sched:
        if not isinstance(entry, dict):
            continue
        cid_entry = str(entry.get("course_id") or entry.get("courseId") or "").strip()
        batches = entry.get("batches")
        if not isinstance(batches, list):
            continue
        for b in batches:
            if not isinstance(b, dict):
                continue
            cid = str(b.get("coach_id") or b.get("coachId") or "").strip()
            if cid and name_map.get(cid):
                b["trainer_name"] = name_map[cid]

            # Capacity enrichment
            bid = str(b.get("batch_id") or b.get("id") or "").strip()
            max_stu = b.get("max_students")
            if max_stu is not None:
                try:
                    max_stu = int(max_stu)
                except (ValueError, TypeError):
                    max_stu = None
            b["max_students"] = max_stu

            active_count = 0
            if branch_id and cid_entry and bid:
                active_count = await db.enrollments.count_documents({
                    "branch_id": branch_id,
                    "course_id": cid_entry,
                    "$or": [{"batch_id": bid}, {"batch_ref": bid}, {"slot_id": bid}],
                    "is_active": True
                })
            b["enrolled_students"] = active_count
            b["is_full"] = bool(max_stu is not None and max_stu > 0 and active_count >= max_stu)


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

        from utils.partner_tenant import (
            assert_partner_may_not_create_branch,
            assign_partner_org_to_branch_doc,
            is_partner_super_admin,
        )

        # Partners cannot create branches (1 partner ↔ 1 branch via Rock SA assignment).
        await assert_partner_may_not_create_branch(current_user)
            
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

        # Normalize optional map coordinates for visitor→branch distance
        if payload.get("latitude") is not None and payload.get("longitude") is not None:
            try:
                from utils.branch_distance import validate_coordinates

                la, lo = validate_coordinates(payload.get("latitude"), payload.get("longitude"))
                payload["latitude"] = la
                payload["longitude"] = lo
                payload["coordinates"] = {"lat": la, "lng": lo}
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        # Rock SA may create a partner-driven branch (1:1 partner_org ↔ branch).
        partner_org_id = payload.pop("partner_org_id", None)
        cover_image_url = payload.pop("cover_image_url", None)
        description = payload.pop("description", None)
        if partner_org_id and is_partner_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Partners cannot assign organizations to branches")

        branch = Branch(**payload)
        
        # Store the branch with nested structure exactly as provided
        branch_dict = branch.dict()
        if payload.get("coordinates"):
            branch_dict["coordinates"] = payload["coordinates"]
        if cover_image_url:
            branch_dict["cover_image_url"] = cover_image_url
        if description:
            branch_dict["description"] = description
        apply_collaboration_flag(branch_dict, flag)
        await assign_partner_org_to_branch_doc(db, branch_dict, partner_org_id)
        # New academies are public by default (backward-compatible visibility).
        if "is_public" not in branch_dict or branch_dict.get("is_public") is None:
            branch_dict["is_public"] = True
        
        await db.branches.insert_one(branch_dict)
        await sync_branch_courses_from_assignments(db, branch.id, branch_dict)

        if partner_org_id:
            from utils.academy_ownership_audit import record_academy_ownership_change

            b_info = branch_dict.get("branch") if isinstance(branch_dict.get("branch"), dict) else {}
            await record_academy_ownership_change(
                branch_id=branch_dict.get("id") or branch.id,
                branch_name=b_info.get("name"),
                previous_partner_org_id=None,
                new_partner_org_id=str(partner_org_id).strip() or None,
                current_user=current_user,
                reason="Created under Partner",
            )

        mid = branch_dict.get("manager_id")
        if mid:
            b_info = branch_dict.get("branch") if isinstance(branch_dict.get("branch"), dict) else {}
            b_name = b_info.get("name") or branch_dict.get("name") or ""
            b_addr = b_info.get("address") or branch_dict.get("address") or {}
            city = b_addr.get("city") or branch_dict.get("city") or ""
            state = b_addr.get("state") or branch_dict.get("state") or ""
            loc = f"{city}, {state}".strip(", ")
            await db.branch_managers.update_one(
                {"id": mid},
                {"$set": {
                    "branch_assignment": {
                        "branch_id": branch.id,
                        "branch_name": b_name,
                        "branch_location": loc
                    }
                }}
            )
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
            # Full assigned list (not the selected branch) so the header switch can list them.
            from utils.student_status_service import get_assigned_branch_ids_for_user

            managed_branches = await get_assigned_branch_ids_for_user(db, current_user)

            if managed_branches:
                filter_query["id"] = {"$in": managed_branches}
            else:
                return {
                    "branches": [],
                    "total_count": 0,
                    "skip": skip,
                    "limit": limit,
                    "message": "No branches assigned to this manager"
                }
        from utils.partner_tenant import (
            merge_and_clause,
            partner_org_id_of,
            rock_dashboard_branch_clause,
            tenant_or_rock_owned_filter,
        )

        # Partner → own tenant only.
        # Rock Super Admin → all physical branches (Rock-owned + Partner Branches).
        # Other Rock roles (BM/coach) → rock-owned only (exclude partner tenants).
        role_val = str(current_user.get("role") or "").lower()
        is_super = role_val in ("super_admin", "superadmin")
        if partner_org_id_of(current_user):
            # Partner dashboard: own genuine academies only (never auto stubs).
            from utils.academy_visibility import genuine_academy_mongo_clause

            filter_query = tenant_or_rock_owned_filter(filter_query, current_user)
            filter_query = merge_and_clause(filter_query, genuine_academy_mongo_clause())
        elif is_super:
            filter_query = merge_and_clause(
                filter_query,
                {"created_via": {"$ne": "partner_org_auto_branch"}},
            )
        else:
            filter_query = merge_and_clause(filter_query, rock_dashboard_branch_clause())

        branches = await db.branches.find(filter_query).skip(skip).limit(limit).to_list(length=limit)

        # Enrich partner org names for SA Partner label
        org_ids = list(
            {
                str(b.get("partner_org_id") or "").strip()
                for b in branches
                if str(b.get("partner_org_id") or "").strip()
            }
        )
        org_name_by_id: dict = {}
        if org_ids:
            async for org in db.partner_organizations.find(
                {"id": {"$in": org_ids}}, {"_id": 0, "id": 1, "name": 1, "slug": 1}
            ):
                if org.get("id"):
                    org_name_by_id[str(org["id"])] = org

        for b in branches:
            b_info = b.get("branch") if isinstance(b.get("branch"), dict) else {}
            if not b.get("name") and b_info.get("name"):
                b["name"] = b_info.get("name")
            if not b.get("code") and b_info.get("code"):
                b["code"] = b_info.get("code")
            org_id = str(b.get("partner_org_id") or "").strip()
            if org_id and org_id in org_name_by_id:
                org = org_name_by_id[org_id]
                b["partner_org_name"] = org.get("name")
                b["partner_org_slug"] = org.get("slug")
                b["is_partner_branch"] = True
            else:
                b["is_partner_branch"] = bool(org_id)

        if not include_stats:
            for branch in branches:
                await _enrich_course_schedule_trainers(db, branch)
                enrich_collaboration_flag(branch)
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
            await _enrich_course_schedule_trainers(db, branch_with_stats)
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

        # Genuine active publicly-enabled academies; hide disabled/expired partners.
        from utils.academy_visibility import public_academy_mongo_clause_for_db

        query = await public_academy_mongo_clause_for_db(db, active_only=active_only)

        # Apply pagination with limit for public endpoint
        if limit > 100:
            limit = 100  # Cap at 100 for public endpoint

        # Get branches
        branches_cursor = db.branches.find(query).skip(skip).limit(limit)
        branches = await branches_cursor.to_list(limit)

        # Get total count
        total = await db.branches.count_documents(query)

        from utils.google_place_rating import enrich_branches_google_ratings

        await enrich_branches_google_ratings(db, branches)
        formatted_branches = []
        for branch in branches:
            await _enrich_course_schedule_trainers(db, branch)
            formatted_branches.append(BranchController._public_list_item(branch))
        await BranchController._enrich_public_partner_names(db, formatted_branches)

        return {
            "message": f"Retrieved {len(formatted_branches)} branches successfully",
            "branches": formatted_branches,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    async def list_branches_for_student(current_user: dict):
        """Branches a student may pick when adding a linked profile.

        Partner students see only their organization's branches.
        Rock students see only Rock branches.
        """
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        org = str((current_user or {}).get("partner_org_id") or "").strip()
        uid = str((current_user or {}).get("id") or "").strip()
        if not org and uid:
            student = await db.users.find_one({"id": uid}, {"partner_org_id": 1})
            org = str((student or {}).get("partner_org_id") or "").strip()

        from utils.partner_tenant import merge_and_clause, rock_owned_clause

        query: dict = {"is_active": True}
        if org:
            query["partner_org_id"] = org
        else:
            query = merge_and_clause(query, rock_owned_clause())

        branches = await db.branches.find(query).limit(100).to_list(length=100)
        formatted = [BranchController._public_list_item(branch) for branch in branches]
        await BranchController._enrich_public_partner_names(db, formatted)
        return {
            "branches": formatted,
            "total": len(formatted),
        }

    @staticmethod
    def _public_list_item(branch: dict) -> dict:
        branch_info = branch.get("branch") or {}
        operational = branch.get("operational_details") or {}
        assignments = branch.get("assignments") or {}
        org_id = str(branch.get("partner_org_id") or "").strip() or None
        partner_name = str(branch.get("partner_org_name") or "").strip() or None
        branch_name = branch_info.get("name")
        # Public brand label: partner org name when assigned, else Rock Martial Arts.
        brand_name = partner_name if org_id and partner_name else (
            "Rock Martial Arts" if not org_id else None
        )
        return {
            "id": branch.get("id"),
            "name": branch_name,
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
            "partner_org_id": org_id,
            "partner_org_name": partner_name,
            "brand_name": brand_name or "Rock Martial Arts",
            "slug": branch.get("slug") or BranchController._name_to_slug(
                str(branch_info.get("name") or "")
            ),
            "allows_collaboration": bool(branch.get("allows_collaboration", False))
            or bool(branch.get("is_collaboration_partner", False)),
            "is_collaboration_partner": bool(branch.get("allows_collaboration", False))
            or bool(branch.get("is_collaboration_partner", False)),
            "is_active": branch.get("is_active", True),
            # Missing is_public ⇒ publicly visible (backward compatible).
            "is_public": branch.get("is_public") is not False,
            "admission_fee": branch.get("admission_fee"),
            "admission_fee_mode": branch.get("admission_fee_mode") or "fixed",
            "admission_fee_by_course": branch.get("admission_fee_by_course") or {},
            "latitude": branch.get("latitude"),
            "longitude": branch.get("longitude"),
            "coordinates": branch.get("coordinates"),
            "cover_image_url": branch.get("cover_image_url") or None,
            "description": branch.get("description") or None,
            "rating": branch.get("rating"),
            "review_count": branch.get("review_count"),
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
    async def _enrich_public_partner_names(db, items: list) -> list:
        """Attach partner_org_name / brand_name / logo_url onto public branch payloads."""
        if not items:
            return items
        org_ids = list(
            {
                str(b.get("partner_org_id") or "").strip()
                for b in items
                if str(b.get("partner_org_id") or "").strip()
            }
        )
        name_by_id: dict = {}
        logo_by_id: dict = {}
        if org_ids:
            async for org in db.partner_organizations.find(
                {"id": {"$in": org_ids}}, {"_id": 0, "id": 1, "name": 1}
            ):
                if org.get("id"):
                    name_by_id[str(org["id"])] = str(org.get("name") or "").strip()
            async for branding in db.partner_org_branding.find(
                {"partner_org_id": {"$in": org_ids}},
                {"_id": 0, "partner_org_id": 1, "media": 1},
            ):
                oid = str(branding.get("partner_org_id") or "").strip()
                media = branding.get("media") or {}
                logo = str((media or {}).get("logo_url") or "").strip()
                if oid and logo:
                    logo_by_id[oid] = logo

        platform_logo = ""
        try:
            from utils.platform_branding import get_platform_branding

            platform = await get_platform_branding(db)
            platform_logo = str(
                platform.get("navbar_logo") or platform.get("footer_logo") or ""
            ).strip()
        except Exception:
            platform_logo = ""

        # Also pull per-branch academy logos when stored on the source docs
        branch_ids = [str(b.get("id") or "").strip() for b in items if b.get("id")]
        academy_logo_by_id: dict = {}
        if branch_ids:
            async for raw in db.branches.find(
                {"id": {"$in": branch_ids}},
                {"_id": 0, "id": 1, "site_branding": 1},
            ):
                bid = str(raw.get("id") or "").strip()
                site = raw.get("site_branding") or {}
                if isinstance(site, dict):
                    logo = str(site.get("logo_url") or "").strip()
                    if bid and logo:
                        academy_logo_by_id[bid] = logo

        for b in items:
            org_id = str(b.get("partner_org_id") or "").strip()
            bid = str(b.get("id") or "").strip()
            if org_id:
                pname = name_by_id.get(org_id) or str(b.get("partner_org_name") or "").strip()
                b["partner_org_name"] = pname or None
                b["brand_name"] = pname or "Partner"
                b["logo_url"] = (
                    academy_logo_by_id.get(bid)
                    or logo_by_id.get(org_id)
                    or platform_logo
                    or None
                )
            else:
                b["partner_org_id"] = None
                b["partner_org_name"] = None
                b["brand_name"] = "Rock Martial Arts"
                b["logo_url"] = academy_logo_by_id.get(bid) or platform_logo or None
        return items

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
        # Live fees from academy/batch setup (same source as payment-info / course detail).
        from controllers.course_controller import (
            _collect_coach_ids_from_branch_schedule,
            _public_branch_batches_for_course,
            _user_display_names_by_ids,
        )

        coach_ids = _collect_coach_ids_from_branch_schedule(branch)
        coach_names = await _user_display_names_by_ids(db, coach_ids)

        for course in courses:
            pricing = course.get("pricing") or {}
            branch_prices = pricing.get("branch_prices") or []
            branch_entry = next((row for row in branch_prices if row.get("branch_id") == branch_id), None)
            src = branch_entry or pricing
            media = course.get("media_resources") or {}
            hero = ((course.get("page_content") or {}).get("hero_section") or {}).get("hero_image")
            course_id = course.get("id")
            fees = {
                "currency": src.get("currency") or pricing.get("currency", "INR"),
                "amount": src.get("amount"),
                "fee_1_month": src.get("fee_1_month"),
                "fee_3_months": src.get("fee_3_months"),
                "fee_6_months": src.get("fee_6_months"),
                "fee_1_year": src.get("fee_1_year"),
                "fee_per_duration": src.get("fee_per_duration"),
            }
            if course_id:
                branch_batches = await _public_branch_batches_for_course(
                    branch, course_id, coach_names, db=db
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
                            if fv <= 0:
                                continue
                            live_amounts.append(fv)
                            if k not in live_fpd or fv < float(live_fpd[k]):
                                live_fpd[k] = fv
                    bf = bat.get("batch_fee")
                    if bf is not None:
                        try:
                            bfv = float(bf)
                            if bfv > 0:
                                live_amounts.append(bfv)
                        except (TypeError, ValueError):
                            pass
                if live_fpd:
                    fees["fee_per_duration"] = live_fpd
                if live_amounts:
                    lowest = min(live_amounts)
                    fees["amount"] = lowest
                    fees["fee_1_month"] = lowest
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
                "pricing": fees,
                "available_durations": duration_rows,
            })
        return cards

    @staticmethod
    async def _public_branch_detail(db, branch: dict) -> dict:
        from utils.google_place_rating import ensure_branch_google_rating

        await ensure_branch_google_rating(db, branch, force=False)
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

        # Branding hierarchy: academy site_branding → partner org → RMAA default.
        from utils.academy_branding import resolve_public_academy_branding

        payload.pop("site_branding", None)
        payload["has_own_branding"] = False
        payload["brand_name"] = "Rock Martial Arts"
        try:
            resolved = await resolve_public_academy_branding(db, branch)
            org_id = resolved.get("partner_org_id")
            payload["partner_org_id"] = org_id
            payload["partner_org_name"] = resolved.get("partner_org_name")
            payload["partner_org_slug"] = resolved.get("partner_org_slug")
            payload["brand_name"] = resolved.get("brand_name") or "Rock Martial Arts"
            payload["has_own_branding"] = bool(resolved.get("has_own_branding"))
            payload["branding_sources"] = resolved.get("branding_sources") or []
            if org_id:
                payload["page_cms_enabled"] = resolved.get("page_cms_enabled")
                payload["public_footer_enabled"] = resolved.get("public_footer_enabled")
            site = resolved.get("site_branding")
            if site and (org_id or "academy" in (resolved.get("branding_sources") or [])):
                # Only expose partner/academy overlay branding on partner academies,
                # or academy-specific branding when present.
                if org_id or "academy" in (resolved.get("branding_sources") or []):
                    payload["site_branding"] = site if org_id else None
                    if org_id:
                        payload["site_branding"] = site
                        if not payload.get("cover_image_url") and site.get("cover_banner_url"):
                            payload["cover_image_url"] = site.get("cover_banner_url")
                        if not payload.get("description") and (
                            site.get("short_description") or site.get("tagline")
                        ):
                            payload["description"] = (
                                site.get("short_description") or site.get("tagline")
                            )
            # Raw academy overlay (for dashboard editors) when present on the branch doc.
            if isinstance(branch.get("site_branding"), dict):
                payload["academy_branding"] = branch.get("site_branding")
        except Exception:
            logger.exception(
                "Failed resolving academy branding for branch %s",
                branch.get("id"),
            )

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
        # Genuine academies only: not stubs, active, publicly enabled; partner org must be active.
        from utils.academy_visibility import merge_public_academy_clause_for_db

        query = await merge_public_academy_clause_for_db(
            db, query, active_only=active_only
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
        from utils.google_place_rating import enrich_branches_google_ratings

        await enrich_branches_google_ratings(db, branches)
        formatted = []
        for branch in branches:
            await _enrich_course_schedule_trainers(db, branch)
            formatted.append(BranchController._public_list_item(branch))
        await BranchController._enrich_public_partner_names(db, formatted)
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
        from utils.academy_visibility import is_academy_publicly_visible_db

        branch = await db.branches.find_one({"id": branch_id})
        if not branch or not await is_academy_publicly_visible_db(
            db, branch, require_active=True
        ):
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
    async def resolve_branch_doc(db, branch_id_or_slug: str):
        """Resolve academy by UUID id or public slug (admin dashboard URLs use name slugs)."""
        key = (branch_id_or_slug or "").strip()
        if not key:
            return None
        branch = await db.branches.find_one({"id": key})
        if branch:
            return branch
        slug = key.lower()
        return await db.branches.find_one({"slug": slug})

    @staticmethod
    async def get_branch_by_slug(slug: str):
        """Get one branch by URL slug for public detail page (no auth). Slug matches branch name normalized."""
        if not slug or not slug.strip():
            raise HTTPException(status_code=404, detail="Branch not found")
        slug = slug.strip().lower()
        db = get_db()
        from utils.academy_visibility import (
            is_academy_publicly_visible_db,
            public_academy_mongo_clause_for_db,
        )

        pub = await public_academy_mongo_clause_for_db(db, active_only=True)
        # Prefer stored slug (S02); fall back to name-derived slug for older rows.
        stored = await db.branches.find_one({"slug": slug})
        if stored and await is_academy_publicly_visible_db(
            db, stored, require_active=True
        ):
            return await BranchController._public_branch_detail(db, stored)
        # If slug looks like UUID, try by id first
        if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", slug, re.I):
            branch = await db.branches.find_one({"id": slug})
            if branch and await is_academy_publicly_visible_db(
                db, branch, require_active=True
            ):
                return await BranchController._public_branch_detail(db, branch)
        # Find by name slug among publicly visible academies only.
        branches = await db.branches.find(pub).to_list(length=500)
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

        branch = await BranchController.resolve_branch_doc(db, branch_id)
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        branch_id = branch["id"]

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only access branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to access this branch")

        from utils.partner_tenant import assert_tenant_doc_access

        assert_tenant_doc_access(branch, current_user, entity="branch")

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

        b_info = branch_with_stats.get("branch") if isinstance(branch_with_stats.get("branch"), dict) else {}
        if not branch_with_stats.get("name") and b_info.get("name"):
            branch_with_stats["name"] = b_info.get("name")
        if not branch_with_stats.get("code") and b_info.get("code"):
            branch_with_stats["code"] = b_info.get("code")

        await _enrich_course_schedule_trainers(db, branch_with_stats)
        enrich_collaboration_flag(branch_with_stats)

        org_id = str(branch_with_stats.get("partner_org_id") or "").strip()
        if org_id:
            org = await db.partner_organizations.find_one(
                {"id": org_id}, {"_id": 0, "id": 1, "name": 1, "slug": 1}
            )
            if org:
                branch_with_stats["partner_org_name"] = org.get("name")
                branch_with_stats["partner_org_slug"] = org.get("slug")
            branch_with_stats["is_partner_branch"] = True
        else:
            branch_with_stats["is_partner_branch"] = False

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

        branch = await BranchController.resolve_branch_doc(db, branch_id)
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        branch_id = branch["id"]

        # Check role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Branch managers can only access branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to access this branch")

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
        
        existing_branch_for_tenant = await BranchController.resolve_branch_doc(db, branch_id)
        if not existing_branch_for_tenant:
            raise HTTPException(status_code=404, detail="Branch not found")
        branch_id = existing_branch_for_tenant["id"]
        from utils.partner_tenant import (
            assert_tenant_doc_access,
            assign_partner_org_to_branch_doc,
            is_partner_super_admin,
        )

        assert_tenant_doc_access(existing_branch_for_tenant, current_user, entity="branch")

        # Branch Manager permission check
        if current_role == "branch_manager" or current_role == UserRole.BRANCH_MANAGER:
            # Branch managers can only update branches they manage
            managed_branches = current_user.get("managed_branches", [])
            if branch_id not in managed_branches:
                raise HTTPException(status_code=403, detail="You don't have permission to update this branch")

        # Coach Admin permission check
        elif current_role == UserRole.COACH_ADMIN:
            # For nested structure, check if user is admin of this branch
            existing_branch = existing_branch_for_tenant
            
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

        role_val = str(current_user.get("role") or "").lower()
        is_super = role_val in ("super_admin", "superadmin")

        # Partner SA must have Super Admin–approved Edit Academy access for this branch.
        if is_partner_super_admin(current_user):
            org_id = str(current_user.get("partner_org_id") or "").strip()
            branch_org = str(existing_for_geo.get("partner_org_id") or "").strip()
            if not org_id or org_id != branch_org:
                raise HTTPException(
                    status_code=403,
                    detail="Partners can only edit their assigned academies",
                )
            from utils.partner_branding_approval import assert_partner_can_edit_branding

            await assert_partner_can_edit_branding(org_id, branch_id=branch_id)
            # Partners never reassign ownership — ignore accidental partner_org_id in payload.
            update_data.pop("partner_org_id", None)
            update_data.pop("is_public", None)
            update_data.pop("allows_collaboration", None)
            update_data.pop("is_collaboration_partner", None)
            # Branding CMS lives on Organization Branding — ignore overlay patches here.
            update_data.pop("site_branding", None)

        # Public visibility — Super Admin only (partners cannot hide/show academies).
        if "is_public" in update_data:
            if not is_super:
                raise HTTPException(
                    status_code=403,
                    detail="Only Super Admin can change public academy visibility",
                )
            update_data["is_public"] = bool(update_data.get("is_public"))

        # Per-academy branding overlay (Rock SA only; partners use Organization Branding)
        ownership_reason = update_data.pop("ownership_change_reason", None)
        if "site_branding" in update_data:
            if not is_super:
                raise HTTPException(
                    status_code=403,
                    detail="Academy branding overlays are managed by Super Admin; partners use Organization Branding",
                )
            from utils.academy_branding import normalize_academy_branding_patch

            raw_branding = update_data.get("site_branding")
            if raw_branding is None:
                update_data["site_branding"] = None
            elif isinstance(raw_branding, dict):
                update_data["site_branding"] = normalize_academy_branding_patch(raw_branding)
            else:
                raise HTTPException(status_code=400, detail="Invalid site_branding payload")

        # Partner org assignment (Rock SA only); partners cannot reassign
        previous_partner_org_id = str(existing_for_geo.get("partner_org_id") or "").strip() or None
        ownership_changed = False
        new_partner_org_id = previous_partner_org_id
        if "partner_org_id" in update_data:
            if not is_super:
                raise HTTPException(
                    status_code=403,
                    detail="Only Super Admin can assign a partner to a branch",
                )
            org_raw = update_data.get("partner_org_id")
            org_id = None if org_raw is None or str(org_raw).strip() == "" else str(org_raw).strip()
            await assign_partner_org_to_branch_doc(
                db,
                update_data,
                org_id,
                exclude_branch_id=branch_id,
            )
            new_partner_org_id = org_id
            ownership_changed = (previous_partner_org_id or None) != (org_id or None)

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
            if "course_schedule" in new_asg:
                from utils.coach_batch_schedule import assert_batch_coach_assignments

                branch_payload = update_data.get("branch") if isinstance(update_data.get("branch"), dict) else {}
                existing_branch = existing_for_geo.get("branch") if isinstance(existing_for_geo.get("branch"), dict) else {}
                branch_name = branch_payload.get("name") or existing_branch.get("name") or "Branch"
                await assert_batch_coach_assignments(
                    db,
                    branch_id,
                    branch_name,
                    new_asg.get("course_schedule") or [],
                )
            if "course_schedule" not in new_asg:
                prev_doc = await db.branches.find_one(
                    {"id": branch_id},
                    {"assignments.course_schedule": 1, "_id": 0},
                )
                prev_cs = (prev_doc or {}).get("assignments", {}).get("course_schedule")
                if prev_cs is not None:
                    new_asg["course_schedule"] = prev_cs
            update_data["assignments"] = prepare_assignments_for_write(new_asg)

        # Keep coordinates object in sync when lat/lng are provided
        if "latitude" in update_data or "longitude" in update_data:
            lat = update_data.get("latitude", existing_for_geo.get("latitude"))
            lng = update_data.get("longitude", existing_for_geo.get("longitude"))
            if lat is not None and lng is not None:
                try:
                    from utils.branch_distance import validate_coordinates

                    la, lo = validate_coordinates(lat, lng)
                    update_data["latitude"] = la
                    update_data["longitude"] = lo
                    update_data["coordinates"] = {"lat": la, "lng": lo}
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))

        update_data["updated_at"] = datetime.utcnow()
        
        result = await db.branches.update_one(
            {"id": branch_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Branch not found")

        if ownership_changed:
            from utils.academy_ownership_audit import record_academy_ownership_change

            binfo = existing_for_geo.get("branch") if isinstance(existing_for_geo.get("branch"), dict) else {}
            await record_academy_ownership_change(
                branch_id=branch_id,
                branch_name=binfo.get("name") or existing_for_geo.get("name"),
                previous_partner_org_id=previous_partner_org_id,
                new_partner_org_id=new_partner_org_id,
                current_user=current_user,
                reason=ownership_reason,
            )

        if "manager_id" in update_data:
            new_mid = update_data.get("manager_id")
            old_mid = existing_for_geo.get("manager_id")
            if old_mid and old_mid != new_mid:
                await db.branch_managers.update_one(
                    {"id": old_mid, "branch_assignment.branch_id": branch_id},
                    {"$set": {"branch_assignment": None}}
                )
            if new_mid:
                branch_payload = update_data.get("branch") if isinstance(update_data.get("branch"), dict) else {}
                existing_branch = existing_for_geo.get("branch") if isinstance(existing_for_geo.get("branch"), dict) else {}
                b_name = branch_payload.get("name") or existing_branch.get("name") or update_data.get("name") or existing_for_geo.get("name") or ""
                b_addr = branch_payload.get("address") or existing_branch.get("address") or {}
                city = b_addr.get("city") or ""
                state = b_addr.get("state") or ""
                loc = f"{city}, {state}".strip(", ")
                await db.branch_managers.update_one(
                    {"id": new_mid},
                    {"$set": {
                        "branch_assignment": {
                            "branch_id": branch_id,
                            "branch_name": b_name,
                            "branch_location": loc
                        }
                    }}
                )

        if "assignments" in update_data:
            refreshed = await db.branches.find_one({"id": branch_id})
            await sync_branch_courses_from_assignments(db, branch_id, refreshed)
            # Additive schedule-update emails when course_schedule was provided
            try:
                new_asg = update_data.get("assignments") or {}
                if isinstance(new_asg, dict) and "course_schedule" in new_asg:
                    from utils.ops_email_hooks import (
                        fire_schedule_update_students,
                        fire_coach_batch_update,
                    )

                    schedule = new_asg.get("course_schedule") or []
                    summary = f"Schedule updated ({len(schedule)} batch(es)) at this branch."
                    # Notify active enrolled students at this branch (best-effort, capped)
                    student_emails = []
                    student_phones = []
                    async for enr in db.enrollments.find(
                        {"branch_id": branch_id, "is_active": True},
                        {"student_id": 1, "course_name": 1},
                    ).limit(200):
                        sid = enr.get("student_id")
                        if not sid:
                            continue
                        u = await db.users.find_one({"id": sid}, {"email": 1, "phone": 1})
                        if not u:
                            continue
                        student_emails.append(u.get("email"))
                        student_phones.append(u.get("phone"))
                    course_label = "your course"
                    if schedule and isinstance(schedule[0], dict):
                        course_label = (
                            schedule[0].get("course_name")
                            or schedule[0].get("title")
                            or course_label
                        )
                    await fire_schedule_update_students(
                        student_emails=student_emails,
                        student_phones=student_phones,
                        course_name=course_label,
                        new_schedule=summary,
                    )
                    coach_emails = []
                    coach_phones = []
                    for row in schedule:
                        if not isinstance(row, dict):
                            continue
                        for cid in row.get("coach_ids") or (
                            [row["coach_id"]] if row.get("coach_id") else []
                        ):
                            c = await db.coaches.find_one({"id": str(cid)})
                            if not c:
                                continue
                            contact = c.get("contact_info") if isinstance(c.get("contact_info"), dict) else {}
                            em = (c.get("email") or contact.get("email") or "").strip()
                            ph = (c.get("phone") or contact.get("phone") or "").strip()
                            coach_emails.append(em or None)
                            coach_phones.append(ph or None)
                    await fire_coach_batch_update(
                        coach_emails=coach_emails,
                        coach_phones=coach_phones,
                        course_name=course_label,
                        change_summary=summary,
                    )
            except Exception:
                logger.exception("ops email after branch schedule update failed")
        
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
    async def calculate_public_distances(
        latitude: float,
        longitude: float,
        branch_ids: Optional[list] = None,
    ):
        """Public: distances from visitor coordinates to branch coordinates."""
        from utils.branch_distance import (
            calculate_branch_distances,
            validate_coordinates,
        )

        try:
            validate_coordinates(latitude, longitude)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        db = get_db()
        query: dict = {"is_active": True}
        ids = [str(x).strip() for x in (branch_ids or []) if str(x).strip()]
        if ids:
            if len(ids) > 50:
                raise HTTPException(status_code=400, detail="Too many branch_ids (max 50)")
            query["id"] = {"$in": ids}

        projection = {
            "id": 1,
            "latitude": 1,
            "longitude": 1,
            "coordinates": 1,
            "is_active": 1,
            "_id": 0,
        }
        branches = await db.branches.find(query, projection).limit(50).to_list(50)
        if ids:
            found = {b.get("id") for b in branches}
            for missing in ids:
                if missing not in found:
                    branches.append({"id": missing})

        return await calculate_branch_distances(latitude, longitude, branches)

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
