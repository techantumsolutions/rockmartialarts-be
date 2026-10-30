from fastapi import HTTPException, Request
from typing import Optional, List
from datetime import datetime
import secrets
import uuid
import re

from models.coach_models import CoachCreate, CoachUpdate, Coach, CoachResponse, CoachLogin, CoachLoginResponse
from models.user_models import UserRole
from utils.auth import hash_password, verify_password, create_access_token, SECRET_KEY, ALGORITHM
from utils.database import get_db
from utils.helpers import serialize_doc, log_activity, send_sms, send_whatsapp
from utils.email_service import send_password_reset_email
import jwt
from datetime import timedelta

class CoachController:
    @staticmethod
    def _validate_display_order_value(order: Optional[int]) -> None:
        if order is None:
            return
        if not isinstance(order, int) or order < 0 or order > 999999:
            raise HTTPException(
                status_code=400,
                detail="display_order must be a non-negative integer (0–999999)",
            )

    @staticmethod
    async def _assert_display_order_unique(
        db,
        order: Optional[int],
        exclude_coach_id: Optional[str] = None,
    ) -> None:
        if order is None:
            return
        CoachController._validate_display_order_value(order)
        q: dict = {"display_order": order, "is_active": True}
        if exclude_coach_id:
            q["id"] = {"$ne": exclude_coach_id}
        dup = await db.coaches.find_one(q)
        if dup:
            raise HTTPException(
                status_code=400,
                detail="Display order is already used by another active coach",
            )

    @staticmethod
    async def create_coach(
        coach_data: CoachCreate,
        request: Request,
        current_admin: dict
    ):
        """Create new coach with comprehensive nested structure"""
        db = get_db()

        await CoachController._assert_display_order_unique(db, coach_data.display_order)
        
        # Check if user exists
        full_phone = f"{coach_data.contact_info.country_code}{coach_data.contact_info.phone}"
        existing_coach = await db.coaches.find_one({
            "$or": [
                {"email": coach_data.contact_info.email}, 
                {"phone": full_phone},
                {"contact_info.phone": coach_data.contact_info.phone}
            ]
        })
        if existing_coach:
            raise HTTPException(status_code=400, detail="Coach with this email or phone already exists")
        
        # Generate password if not provided
        if not coach_data.contact_info.password:
            coach_data.contact_info.password = secrets.token_urlsafe(8)
        
        # Hash password
        hashed_password = hash_password(coach_data.contact_info.password)
        
        # Generate full name from first and last name
        full_name = f"{coach_data.personal_info.first_name} {coach_data.personal_info.last_name}".strip()
        
        # Parse and validate date of birth
        try:
            datetime.strptime(coach_data.personal_info.date_of_birth, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        # Import AssignmentDetails for default creation
        from models.coach_models import AssignmentDetails, EmergencyContact

        # Create coach object
        coach = Coach(
            personal_info=coach_data.personal_info,
            contact_info=coach_data.contact_info,
            address_info=coach_data.address_info,
            professional_info=coach_data.professional_info,
            areas_of_expertise=coach_data.areas_of_expertise,
            branch_id=coach_data.branch_id,  # Include branch assignment
            assignment_details=coach_data.assignment_details or AssignmentDetails(),  # Ensure assignment_details exists
            emergency_contact=coach_data.emergency_contact or EmergencyContact(),  # Ensure emergency_contact exists
            email=coach_data.contact_info.email,
            phone=full_phone,
            first_name=coach_data.personal_info.first_name,
            last_name=coach_data.personal_info.last_name,
            full_name=full_name,
            password_hash=hashed_password,
            profile_image_url=coach_data.profile_image_url,
            about_short=coach_data.about_short,
            featured_on_homepage=coach_data.featured_on_homepage,
            homepage_rating=coach_data.homepage_rating,
            display_order=coach_data.display_order,
        )
        
        # Convert to dict for storage (exclude password from contact_info)
        coach_dict = coach.dict()
        # Remove password from nested contact_info for storage
        coach_dict["contact_info"] = {
            "email": coach_data.contact_info.email,
            "country_code": coach_data.contact_info.country_code,
            "phone": coach_data.contact_info.phone
        }
        # M14 additive defaults for admin-created coaches (keep existing UX)
        coach_dict["approval_status"] = "approved"
        coach_dict["registration_source"] = "admin"
        coach_dict["user_id"] = None
        if coach_data.branch_id:
            coach_dict["service_location_ids"] = [coach_data.branch_id]
        else:
            coach_dict["service_location_ids"] = []
        
        # Insert into coaches collection
        result = await db.coaches.insert_one(coach_dict)
        
        # Send credentials via SMS
        sms_message = (
            f"Welcome {full_name}!\n"
            f"Your coach account has been created.\n"
            f"Email: {coach_data.contact_info.email}\n"
            f"Password: {coach_data.contact_info.password}\n"
            f"Role: Coach\n"
            f"Areas of Expertise: {', '.join(coach_data.areas_of_expertise)}"
        )
        await send_sms(full_phone, sms_message)
        
        # Log activity
        await log_activity(
            request=request,
            action="coach_creation",
            user_id=current_admin["id"],
            user_name=current_admin.get("full_name", "Admin"),
            details={
                "created_coach_id": coach.id,
                "coach_email": coach_data.contact_info.email,
                "areas_of_expertise": coach_data.areas_of_expertise
            }
        )

        return {"message": "Coach created successfully", "coach_id": coach.id}

    @staticmethod
    async def get_coaches(
        skip: int = 0,
        limit: int = 50,
        active_only: bool = True,
        area_of_expertise: Optional[str] = None,
        current_user: dict = None,
        approval_status: Optional[str] = None,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
    ):
        """Get coaches with filters. Branch managers see only coaches in their managed branches."""
        db = get_db()
        
        filter_query = {}
        if active_only:
            filter_query["is_active"] = True
        if area_of_expertise:
            filter_query["areas_of_expertise"] = {"$in": [area_of_expertise]}
        # M14-S02 additive filter (default: no change to existing list behavior)
        if approval_status:
            status = str(approval_status).lower().strip()
            if status == "approved":
                filter_query["$or"] = [
                    {"approval_status": "approved"},
                    {"approval_status": {"$exists": False}},
                    {"approval_status": None},
                    {"approval_status": ""},
                ]
            elif status in ("pending", "rejected"):
                filter_query["approval_status"] = status

        if course_id:
            course = await db.courses.find_one({"id": course_id})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")

            course_conditions = [
                {"assignment_details.courses": course_id},
                {"areas_of_expertise": {"$in": [course_id]}},
            ]
            instructor_id = course.get("instructor_id")
            if instructor_id:
                course_conditions.append({"id": instructor_id})
            filter_query.setdefault("$and", []).append({"$or": course_conditions})

        role = str((current_user or {}).get("role") or "").lower()
        bid = (branch_id or "").strip()
        if bid.lower() == "all":
            bid = ""
        is_branch_manager = role in {"branch_manager", "branch_admin", "branchmanager"}

        if current_user and is_branch_manager:
            from utils.student_status_service import get_managed_branch_ids_for_user

            # Live branches.manager_id for this login. branch_assignment can point at
            # a branch this manager no longer manages.
            managed_branch_ids = [
                str(x) for x in await get_managed_branch_ids_for_user(db, current_user) if x
            ]
            if bid and bid in managed_branch_ids:
                branch_scope = {"branch_id": bid}
            elif managed_branch_ids:
                branch_scope = {"branch_id": {"$in": managed_branch_ids}}
            else:
                branch_scope = {"id": {"$in": []}}
            and_parts = list(filter_query.pop("$and", []) or [])
            if "$or" in filter_query:
                and_parts.append({"$or": filter_query.pop("$or")})
            and_parts.append(branch_scope)
            filter_query["$and"] = and_parts
        elif bid:
            # Super admin and other roles: coaches.branch_id == branches.id
            assigned_scope = {"branch_id": bid}
            and_parts = list(filter_query.pop("$and", []) or [])
            if "$or" in filter_query:
                and_parts.append({"$or": filter_query.pop("$or")})
            and_parts.append(assigned_scope)
            filter_query["$and"] = and_parts

        coaches = await db.coaches.find(filter_query).skip(skip).limit(limit).to_list(length=limit)
        
        # Convert to response format (remove sensitive data)
        coach_responses = []
        for coach in coaches:
            coach_response = CoachResponse(
                id=coach["id"],
                personal_info=coach["personal_info"],
                contact_info=coach["contact_info"],  # Already cleaned of password
                address_info=coach["address_info"],
                professional_info=coach["professional_info"],
                areas_of_expertise=coach["areas_of_expertise"],
                branch_id=coach.get("branch_id"),  # Include branch assignment
                assignment_details=coach.get("assignment_details"),  # Include course assignments
                emergency_contact=coach.get("emergency_contact"),  # Include emergency contact
                full_name=coach["full_name"],
                is_active=coach["is_active"],
                profile_image_url=coach.get("profile_image_url"),
                about_short=coach.get("about_short"),
                featured_on_homepage=coach.get("featured_on_homepage", False),
                homepage_rating=coach.get("homepage_rating"),
                display_order=coach.get("display_order"),
                approval_status=coach.get("approval_status"),
                user_id=coach.get("user_id"),
                service_location_ids=coach.get("service_location_ids"),
                registration_source=coach.get("registration_source"),
                created_at=coach["created_at"],
                updated_at=coach["updated_at"]
            )
            coach_responses.append(coach_response.dict())
        
        total_count = await db.coaches.count_documents(filter_query)
        
        return {
            "coaches": coach_responses,
            "total": total_count,
            "page": skip // limit + 1 if limit > 0 else 1,
            "limit": limit
        }

    @staticmethod
    async def get_coach_by_id(coach_id: str, current_user: dict = None):
        """Get coach by ID"""
        db = get_db()
        
        coach = await db.coaches.find_one({"id": coach_id})
        if not coach:
            raise HTTPException(status_code=404, detail="Coach not found")
        
        # Convert to response format
        coach_response = CoachResponse(
            id=coach["id"],
            personal_info=coach["personal_info"],
            contact_info=coach["contact_info"],
            address_info=coach["address_info"],
            professional_info=coach["professional_info"],
            areas_of_expertise=coach["areas_of_expertise"],
            branch_id=coach.get("branch_id"),  # Include branch assignment
            assignment_details=coach.get("assignment_details"),  # Include course assignments
            emergency_contact=coach.get("emergency_contact"),  # Include emergency contact
            full_name=coach["full_name"],
            is_active=coach["is_active"],
            profile_image_url=coach.get("profile_image_url"),
            about_short=coach.get("about_short"),
            featured_on_homepage=coach.get("featured_on_homepage", False),
            homepage_rating=coach.get("homepage_rating"),
            display_order=coach.get("display_order"),
            approval_status=coach.get("approval_status"),
            user_id=coach.get("user_id"),
            service_location_ids=coach.get("service_location_ids"),
            registration_source=coach.get("registration_source"),
            created_at=coach["created_at"],
            updated_at=coach["updated_at"]
        )
        
        return coach_response.dict()

    @staticmethod
    async def get_public_homepage_coaches(limit: int = 8):
        """Active coaches for homepage: display_order ASC (unset last), then created_at DESC. Max 8."""
        db = get_db()
        safe_limit = max(1, min(int(limit), 8))
        pipeline = [
            {"$match": {
                "is_active": True,
                "$or": [
                    {"approval_status": {"$exists": False}},
                    {"approval_status": "approved"},
                    {"approval_status": None},
                ],
            }},
            {"$addFields": {"_sort_ord": {"$ifNull": ["$display_order", 999999]}}},
            {"$sort": {"_sort_ord": 1, "created_at": -1}},
            {"$limit": safe_limit},
            {"$project": {"_sort_ord": 0}},
        ]
        coaches = await db.coaches.aggregate(pipeline).to_list(length=safe_limit)
        out = []
        for c in coaches:
            pi = c.get("personal_info") or {}
            prof = c.get("professional_info") or {}
            name = (c.get("full_name") or f"{pi.get('first_name', '')} {pi.get('last_name', '')}").strip()
            about = (c.get("about_short") or "").strip()
            if len(about) > 280:
                about = about[:277] + "..."
            out.append({
                "id": c.get("id"),
                "full_name": name or "Coach",
                "designation": prof.get("designation_id") or "",
                "about_short": about,
                "profile_image_url": c.get("profile_image_url"),
                "rating": c.get("homepage_rating"),
            })
        return {"coaches": out}

    @staticmethod
    async def update_coach(
        coach_id: str,
        coach_update: CoachUpdate,
        request: Request,
        current_admin: dict
    ):
        """Update coach information"""
        db = get_db()
        print("Incoming update payload:", coach_update.dict(), coach_update.dict(exclude_unset=True))

        # Check if coach exists
        existing_coach = await db.coaches.find_one({"id": coach_id})
        if not existing_coach:
            raise HTTPException(status_code=404, detail="Coach not found")
        
        #----------------------------added for branch manager to update cocahes in their branch only----------------------------#
        # Branch Manager permission check (like course instructor check)
        if current_admin["role"] != UserRole.SUPER_ADMIN:
            if existing_coach.get("branch_id") != current_admin.get("branch_id"):
                raise HTTPException(
                    status_code=403,
                    detail="You can only update coaches in your branch."
                )
        #----------------------------end of added for branch manager to update cocahes in their branch only----------------------------#
    
        # Prepare update data
        update_data = {}
        
        if coach_update.personal_info:
            update_data["personal_info"] = coach_update.personal_info.dict()
            # Update derived fields
            update_data["first_name"] = coach_update.personal_info.first_name
            update_data["last_name"] = coach_update.personal_info.last_name
            update_data["full_name"] = f"{coach_update.personal_info.first_name} {coach_update.personal_info.last_name}".strip()
            
            # Validate date format if provided
            if coach_update.personal_info.date_of_birth:
                try:
                    datetime.strptime(coach_update.personal_info.date_of_birth, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        if coach_update.contact_info:
            # Check for email conflicts if email is being changed
            if coach_update.contact_info.email != existing_coach["email"]:
                email_conflict = await db.coaches.find_one({
                    "email": coach_update.contact_info.email,
                    "id": {"$ne": coach_id}
                })
                if email_conflict:
                    raise HTTPException(status_code=400, detail="Email already exists")
            
            # Check for phone conflicts
            full_phone = f"{coach_update.contact_info.country_code}{coach_update.contact_info.phone}"
            existing_phone = existing_coach.get("contact_info", {}).get("phone", "")
            if coach_update.contact_info.phone != existing_phone:
                phone_conflict = await db.coaches.find_one({
                    "$or": [
                        {"phone": full_phone},
                        {"contact_info.phone": coach_update.contact_info.phone}
                    ],
                    "id": {"$ne": coach_id}
                })
                if phone_conflict:
                    raise HTTPException(status_code=400, detail="Phone number already exists")
            
            # Update contact info (exclude password from nested structure)
            update_data["contact_info"] = {
                "email": coach_update.contact_info.email,
                "country_code": coach_update.contact_info.country_code,
                "phone": coach_update.contact_info.phone
            }
            update_data["email"] = coach_update.contact_info.email
            update_data["phone"] = f"{coach_update.contact_info.country_code}{coach_update.contact_info.phone}"
            
            # Handle password update if provided
            if coach_update.contact_info.password:
                update_data["password_hash"] = hash_password(coach_update.contact_info.password)
        
        if coach_update.address_info:
            update_data["address_info"] = coach_update.address_info.dict()
        
        if coach_update.professional_info:
            update_data["professional_info"] = coach_update.professional_info.dict()
        
        if coach_update.areas_of_expertise:
            update_data["areas_of_expertise"] = coach_update.areas_of_expertise

        if coach_update.branch_id is not None:  # Allow setting to None to remove branch assignment
            update_data["branch_id"] = coach_update.branch_id
            update_data["service_location_ids"] = [coach_update.branch_id] if coach_update.branch_id else []
            # Keep coach_availability synchronized with new branch assignment
            if coach_update.branch_id:
                try:
                    await db["coach_availability"].update_one(
                        {"coach_id": coach_id},
                        {"$set": {
                            "service_location_ids": [coach_update.branch_id],
                            "weekly_slots.$[].service_location_id": coach_update.branch_id
                        }}
                    )
                except Exception:
                    pass

        if coach_update.assignment_details is not None:  # Allow updating course assignments
            update_data["assignment_details"] = coach_update.assignment_details.dict()

        if coach_update.emergency_contact is not None:  # Allow updating emergency contact
            update_data["emergency_contact"] = coach_update.emergency_contact.dict()

        if coach_update.is_active is not None:  # ✅ add this
            update_data["is_active"] = coach_update.is_active

        patch = coach_update.model_dump(exclude_unset=True)
        if "profile_image_url" in patch:
            update_data["profile_image_url"] = patch["profile_image_url"]
        if "about_short" in patch:
            update_data["about_short"] = patch["about_short"]
        if "featured_on_homepage" in patch:
            update_data["featured_on_homepage"] = patch["featured_on_homepage"]
        if "homepage_rating" in patch:
            update_data["homepage_rating"] = patch["homepage_rating"]

        mongo_unset: dict = {}
        if "display_order" in patch:
            if patch["display_order"] is None:
                mongo_unset["display_order"] = ""
            else:
                await CoachController._assert_display_order_unique(
                    db, patch["display_order"], coach_id
                )
                update_data["display_order"] = patch["display_order"]

        if not update_data and not mongo_unset:
            raise HTTPException(status_code=400, detail="No update data provided")

        update_data["updated_at"] = datetime.utcnow()

        update_doc: dict = {"$set": update_data}
        if mongo_unset:
            update_doc["$unset"] = mongo_unset
        result = await db.coaches.update_one({"id": coach_id}, update_doc)
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Coach not found")
        
        # Log activity
        await log_activity(
            request=request,
            action="coach_update",
            user_id=current_admin["id"],
            user_name=current_admin.get("full_name", "Admin"),
            details={
                "updated_coach_id": coach_id,
                "update_fields": list(update_data.keys())
            }
        )

        return {"message": "Coach updated successfully"}

    @staticmethod
    async def update_coach_profile(
        coach_id: str,
        coach_update: CoachUpdate,
        request: Request,
        current_coach: dict = None
    ):
        """Update coach's own profile - coaches can update their own information"""
        db = get_db()

        # Verify the coach is updating their own profile
        if current_coach and current_coach.get("id") != coach_id and current_coach.get("role") not in [UserRole.SUPER_ADMIN, "super_admin"]:
            raise HTTPException(status_code=403, detail="You can only update your own profile")

        # Check if coach exists
        existing_coach = await db.coaches.find_one({"id": coach_id})
        if not existing_coach:
            raise HTTPException(status_code=404, detail="Coach not found")

        update_data = {}

        # Update personal info
        if coach_update.personal_info:
            update_data["personal_info"] = coach_update.personal_info.dict()

            # Update derived fields - individual name fields and full_name
            update_data["first_name"] = coach_update.personal_info.first_name
            update_data["last_name"] = coach_update.personal_info.last_name
            update_data["full_name"] = f"{coach_update.personal_info.first_name} {coach_update.personal_info.last_name}".strip()

            # Validate date format if provided
            if coach_update.personal_info.date_of_birth:
                dob = coach_update.personal_info.date_of_birth.strip().split("T")[0]
                try:
                    datetime.strptime(dob, "%Y-%m-%d")
                    update_data["personal_info"]["date_of_birth"] = dob
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        if coach_update.contact_info:
            # Check for email conflicts if email is being changed
            new_email = coach_update.contact_info.email.strip().lower() if coach_update.contact_info.email else ""
            existing_email = (existing_coach.get("email") or existing_coach.get("contact_info", {}).get("email", "")).strip().lower()
            if new_email and new_email != existing_email:
                email_conflict = await db.coaches.find_one({
                    "email": {"$regex": f"^{re.escape(new_email)}$", "$options": "i"},
                    "id": {"$ne": coach_id}
                })
                if email_conflict:
                    raise HTTPException(status_code=400, detail="Email already exists")

            # Check for phone conflicts
            full_phone = f"{coach_update.contact_info.country_code}{coach_update.contact_info.phone}".strip()
            existing_phone = existing_coach.get("contact_info", {}).get("phone", "") or ""
            existing_full_phone = existing_coach.get("phone", "") or ""
            if coach_update.contact_info.phone != existing_phone and full_phone != existing_full_phone:
                phone_conflict = await db.coaches.find_one({
                    "$or": [
                        {"phone": full_phone},
                        {"contact_info.phone": coach_update.contact_info.phone}
                    ],
                    "id": {"$ne": coach_id}
                })
                if phone_conflict:
                    raise HTTPException(status_code=400, detail="Phone number already exists")

            # Update contact info (exclude password from nested structure)
            update_data["contact_info"] = {
                "email": coach_update.contact_info.email,
                "country_code": coach_update.contact_info.country_code,
                "phone": coach_update.contact_info.phone
            }
            update_data["email"] = coach_update.contact_info.email
            update_data["phone"] = full_phone

            # Handle password update if provided
            if coach_update.contact_info.password and coach_update.contact_info.password.strip():
                update_data["password_hash"] = hash_password(coach_update.contact_info.password)

        if coach_update.address_info:
            update_data["address_info"] = coach_update.address_info.dict()

        if coach_update.professional_info:
            update_data["professional_info"] = coach_update.professional_info.dict()

        if coach_update.areas_of_expertise is not None:
            update_data["areas_of_expertise"] = coach_update.areas_of_expertise

        if coach_update.emergency_contact is not None:
            update_data["emergency_contact"] = coach_update.emergency_contact.dict()

        patch = coach_update.model_dump(exclude_unset=True) if hasattr(coach_update, "model_dump") else coach_update.dict(exclude_unset=True)
        if "profile_image_url" in patch:
            update_data["profile_image_url"] = patch["profile_image_url"]
        if "about_short" in patch:
            update_data["about_short"] = patch["about_short"]

        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        update_data["updated_at"] = datetime.utcnow()

        # Update coach
        result = await db.coaches.update_one(
            {"id": coach_id},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Coach not found")

        # Log activity
        user_id = current_coach.get("id", coach_id) if current_coach else coach_id
        user_name = current_coach.get("full_name", "Coach") if current_coach else existing_coach.get("full_name", "Coach")
        await log_activity(
            request=request,
            action="coach_profile_update",
            user_id=user_id,
            user_name=user_name,
            details={
                "updated_coach_id": coach_id,
                "updated_fields": list(update_data.keys())
            }
        )

        return {"message": "Profile updated successfully"}

 

    @staticmethod
    async def deactivate_coach(
        coach_id: str,
        request: Request,
        current_admin: dict
    ):
        """Deactivate coach"""
        db = get_db()
        
        result = await db.coaches.update_one(
            {"id": coach_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Coach not found")
        
        # Log activity
        await log_activity(
            request=request,
            action="coach_deactivation",
            user_id=current_admin["id"],
            user_name=current_admin.get("full_name", "Admin"),
            details={"deactivated_coach_id": coach_id}
        )

        return {"message": "Coach deactivated successfully"}

    @staticmethod
    async def activate_coach(
        coach_id: str,
        request: Request,
        current_admin: dict
    ):
        """Activate coach"""
        db = get_db()

        # First, get the coach to check if it exists and get branch info
        coach = await db.coaches.find_one({"id": coach_id})
        if not coach:
            raise HTTPException(status_code=404, detail="Coach not found")

        # If current user is a branch manager, ensure they can only activate coaches from their branch
        current_role = current_admin.get("role")
        if current_role == "branch_manager" or current_role == UserRole.BRANCH_MANAGER:
            manager_branch_id = current_admin.get("branch_id")
            if not manager_branch_id:
                branch_assignment = current_admin.get("branch_assignment")
                if branch_assignment and isinstance(branch_assignment, dict):
                    manager_branch_id = branch_assignment.get("branch_id")

            managed_branches = list(current_admin.get("managed_branches") or [])
            if manager_branch_id and manager_branch_id not in managed_branches:
                managed_branches.append(manager_branch_id)

            if coach.get("branch_id") not in managed_branches:
                raise HTTPException(status_code=403, detail="You can only activate coaches from your assigned branch")

        result = await db.coaches.update_one(
            {"id": coach_id},
            {"$set": {"is_active": True, "updated_at": datetime.utcnow()}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Coach not found")

        # Log activity
        await log_activity(
            request=request,
            action="coach_activation",
            user_id=current_admin["id"],
            user_name=current_admin.get("full_name", "Admin"),
            details={"activated_coach_id": coach_id}
        )

        return {"message": "Coach activated successfully"}

    @staticmethod
    async def get_coaches_by_course(course_id: str, current_user: dict = None):
        """Get coaches assigned to a specific course"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            course = await db.courses.find_one({"id": course_id})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")

            coaches = []

            if course.get("instructor_id"):
                instructor = await db.coaches.find_one({
                    "id": course["instructor_id"],
                    "is_active": True
                })
                if instructor:
                    coaches.append(instructor)

            assigned_coaches = await db.coaches.find({
                "assignment_details.courses": course_id,
                "is_active": True
            }).to_list(length=100)

            coach_ids_seen = {coach["id"] for coach in coaches}
            for assigned_coach in assigned_coaches:
                if assigned_coach["id"] not in coach_ids_seen:
                    coaches.append(assigned_coach)

            formatted_coaches = []
            for coach in coaches:
                formatted_coaches.append({
                    "id": coach["id"],
                    "first_name": coach.get("first_name", ""),
                    "last_name": coach.get("last_name", ""),
                    "full_name": coach.get(
                        "full_name",
                        f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip(),
                    ),
                    "email": coach.get("email", ""),
                    "phone": coach.get("phone", ""),
                    "areas_of_expertise": coach.get("areas_of_expertise", []),
                    "is_active": coach.get("is_active", True),
                    "branch_id": coach.get("branch_id"),
                    "is_instructor": coach["id"] == course.get("instructor_id"),
                    "is_assigned": course_id in coach.get("assignment_details", {}).get("courses", []),
                })

            return {
                "coaches": formatted_coaches,
                "total": len(formatted_coaches),
                "course_id": course_id,
                "course_title": course.get("title", "Unknown Course"),
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching coaches for course: {str(e)}")

    @staticmethod
    async def get_coaches_by_course_public(course_id: str):
        """Get coaches assigned to a specific course - Public endpoint (no authentication required)"""
        db = get_db()

        try:
            course = await db.courses.find_one({"id": course_id})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")

            coaches = []

            if course.get("instructor_id"):
                instructor = await db.coaches.find_one({
                    "id": course["instructor_id"],
                    "is_active": True
                })
                if instructor:
                    coaches.append(instructor)

            assigned_coaches = await db.coaches.find({
                "assignment_details.courses": course_id,
                "is_active": True
            }).to_list(100)

            coach_ids = {c["id"] for c in coaches}
            for coach in assigned_coaches:
                if coach["id"] not in coach_ids:
                    coaches.append(coach)

            return [serialize_doc(coach) for coach in coaches]

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching coaches for course: {str(e)}")

    @staticmethod
    async def get_coach_courses(coach_id: str, current_user: dict = None):
        """Get courses assigned to a specific coach"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Verify coach exists
        coach = await db.coaches.find_one({"id": coach_id})
        if not coach:
            raise HTTPException(status_code=404, detail="Coach not found")

        # Permission check: coaches can only view their own courses unless admin
        if current_user["role"] == UserRole.COACH and current_user["id"] != coach_id:
            raise HTTPException(status_code=403, detail="You can only view your own course assignments")

        try:
            # Get courses where this coach is the instructor
            courses = await db.courses.find({
                "instructor_id": coach_id,
                "settings.active": True
            }).to_list(length=100)

            # Also get courses from coach's assignment_details
            assigned_course_ids = coach.get("assignment_details", {}).get("courses", [])
            if assigned_course_ids:
                assigned_courses = await db.courses.find({
                    "id": {"$in": assigned_course_ids},
                    "settings.active": True
                }).to_list(length=100)

                # Merge courses (avoid duplicates)
                course_ids_seen = {course["id"] for course in courses}
                for assigned_course in assigned_courses:
                    if assigned_course["id"] not in course_ids_seen:
                        courses.append(assigned_course)

            # Batches for this coach's assigned courses, plus batches that name this coach.
            match_ids = {str(coach_id)}
            linked_user_id = str(coach.get("user_id") or "").strip()
            if linked_user_id:
                match_ids.add(linked_user_id)

            coach_emails = set()
            for raw_email in (
                coach.get("email"),
                (coach.get("contact_info") or {}).get("email") if isinstance(coach.get("contact_info"), dict) else None,
            ):
                if isinstance(raw_email, str) and raw_email.strip():
                    coach_emails.add(raw_email.strip().lower())
            if coach_emails:
                # Batch trainer ids are sometimes the branch-manager record for the same person.
                managers = await db.branch_managers.find(
                    {},
                    {"id": 1, "email": 1, "contact_info.email": 1},
                ).to_list(length=200)
                for manager in managers:
                    manager_emails = set()
                    for raw_email in (
                        manager.get("email"),
                        (manager.get("contact_info") or {}).get("email") if isinstance(manager.get("contact_info"), dict) else None,
                    ):
                        if isinstance(raw_email, str) and raw_email.strip():
                            manager_emails.add(raw_email.strip().lower())
                    if coach_emails & manager_emails and manager.get("id"):
                        match_ids.add(str(manager["id"]))

            assigned_course_ids = {course["id"] for course in courses}
            coach_branch_id = str(coach.get("branch_id") or "").strip()

            branch_filter = {"is_active": True}
            role = str((current_user or {}).get("role") or "").lower()
            is_branch_manager = role in {"branch_manager", "branch_admin", "branchmanager"}
            if is_branch_manager:
                from utils.student_status_service import get_managed_branch_ids_for_user

                allowed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
                branch_filter["id"] = {"$in": allowed_branch_ids or ["__none__"]}

            schedule_branches = await db.branches.find(
                branch_filter,
                {"id": 1, "branch.name": 1, "assignments.course_schedule": 1},
            ).to_list(length=500)

            batches_by_course = {}
            for branch in schedule_branches:
                branch_name = ((branch.get("branch") or {}).get("name")) or "Branch"
                schedule = ((branch.get("assignments") or {}).get("course_schedule")) or []
                for entry in schedule:
                    if not isinstance(entry, dict):
                        continue
                    course_id = str(entry.get("course_id") or entry.get("courseId") or "").strip()
                    if not course_id:
                        continue
                    for index, batch in enumerate(entry.get("batches") or []):
                        if not isinstance(batch, dict):
                            continue
                        trainer_id = str(batch.get("coach_id") or batch.get("coachId") or "").strip()
                        trainer_match = bool(trainer_id) and trainer_id in match_ids
                        on_coach_branch = is_branch_manager or (
                            not coach_branch_id or str(branch.get("id") or "") == coach_branch_id
                        )
                        if not (trainer_match or (course_id in assigned_course_ids and on_coach_branch)):
                            continue
                        days = [str(day) for day in (batch.get("days") or []) if day]
                        batch_name = str(batch.get("batch_name") or batch.get("name") or "").strip()
                        b_id = str(batch.get("id") or batch.get("batch_id") or "").strip()
                        batches_by_course.setdefault(course_id, []).append({
                            "id": b_id,
                            "batch_id": b_id,
                            "batch_name": batch_name or f"Batch {index + 1}",
                            "coach_id": trainer_id,
                            "days": days,
                            "start_time": str(batch.get("start_time") or batch.get("startTime") or "").strip(),
                            "end_time": str(batch.get("end_time") or batch.get("endTime") or "").strip(),
                            "branch_id": branch.get("id"),
                            "branch_name": branch_name,
                        })

            known_course_ids = {course["id"] for course in courses}
            missing_course_ids = [cid for cid in batches_by_course if cid not in known_course_ids]
            if missing_course_ids:
                extra_courses = await db.courses.find({"id": {"$in": missing_course_ids}}).to_list(length=200)
                courses.extend(extra_courses)

            # Enhance courses with additional data
            enhanced_courses = []
            for course in courses:
                # Get enrollment count for this course
                enrollment_count = await db.enrollments.count_documents({
                    "course_id": course["id"],
                    "is_active": True
                })

                # Get branch information
                branches = await db.branches.find({
                    "assignments.courses": course["id"],
                    "is_active": True
                }).to_list(length=10)

                course_batches = batches_by_course.get(course["id"], [])
                enhanced_course = serialize_doc(course)

                # Derive assigned_date from earliest batch timestamp or course/coach creation
                assigned_date_val = None
                for b in course_batches:
                    b_id = str(b.get("batch_id") or b.get("id") or "")
                    if "batch-" in b_id:
                        digits = b_id.split("batch-")[-1]
                        if digits.isdigit() and len(digits) >= 10:
                            try:
                                ms = int(digits)
                                if len(digits) == 10:
                                    ms *= 1000
                                assigned_date_val = datetime.fromtimestamp(ms / 1000.0).isoformat()
                                break
                            except Exception:
                                pass
                if not assigned_date_val:
                    assigned_date_val = course.get("assigned_date") or course.get("created_at") or (coach.get("assignment_details") or {}).get("join_date") or coach.get("created_at")

                enhanced_course.update({
                    "name": course.get("title", course.get("name", "Unknown Course")),
                    "course_name": course.get("title", course.get("name", "Unknown Course")),
                    "course_code": course.get("code", ""),
                    "enrolled_students": enrollment_count,
                    "active_students": enrollment_count,
                    "total_students": enrollment_count,
                    "assigned_date": str(assigned_date_val) if assigned_date_val else None,
                    "difficulty_level": course.get("difficulty_level", "Beginner"),
                    "batches": course_batches,
                    "branch_assignments": [
                        {
                            "branch_id": branch["id"],
                            "branch_name": (branch.get("branch") or {}).get("name") or "Branch"
                        }
                        for branch in branches
                    ]
                })
                enhanced_courses.append(enhanced_course)

            return {"courses": enhanced_courses, "total": len(enhanced_courses)}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching coach courses: {str(e)}")

    @staticmethod
    async def _coach_match_ids(db, coach: dict, coach_id: str) -> set:
        """IDs that may appear as batch trainer (coach id, linked user, same-email managers)."""
        match_ids = {str(coach_id)}
        linked_user_id = str(coach.get("user_id") or "").strip()
        if linked_user_id:
            match_ids.add(linked_user_id)

        coach_emails = set()
        for raw_email in (
            coach.get("email"),
            (coach.get("contact_info") or {}).get("email") if isinstance(coach.get("contact_info"), dict) else None,
        ):
            if isinstance(raw_email, str) and raw_email.strip():
                coach_emails.add(raw_email.strip().lower())
        if coach_emails:
            managers = await db.branch_managers.find(
                {},
                {"id": 1, "email": 1, "contact_info.email": 1},
            ).to_list(length=200)
            for manager in managers:
                manager_emails = set()
                for raw_email in (
                    manager.get("email"),
                    (manager.get("contact_info") or {}).get("email") if isinstance(manager.get("contact_info"), dict) else None,
                ):
                    if isinstance(raw_email, str) and raw_email.strip():
                        manager_emails.add(raw_email.strip().lower())
                if coach_emails & manager_emails and manager.get("id"):
                    match_ids.add(str(manager["id"]))
        return match_ids

    @staticmethod
    async def get_coach_students(coach_id: str, current_user: dict = None):
        """Get students enrolled in courses/batches taught by a specific coach."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        coach = await db.coaches.find_one({"id": coach_id})
        if not coach:
            raise HTTPException(status_code=404, detail="Coach not found")

        if current_user["role"] == UserRole.COACH and current_user["id"] != coach_id:
            raise HTTPException(status_code=403, detail="You can only view your own students")

        try:
            course_ids: List[str] = []

            instructor_courses = await db.courses.find({
                "instructor_id": coach_id,
                "settings.active": True
            }).to_list(length=100)
            course_ids.extend([course["id"] for course in instructor_courses])

            assigned_course_ids = coach.get("assignment_details", {}).get("courses", []) or []
            course_ids.extend([cid for cid in assigned_course_ids if cid])
            course_ids = list({str(cid) for cid in course_ids if cid})

            coach_branch_id = str(coach.get("branch_id") or "").strip()
            match_ids = await CoachController._coach_match_ids(db, coach, coach_id)

            # Resolve batches for this coach from branch schedules (for filters + batch names).
            branch_query: dict = {"is_active": True}
            if coach_branch_id:
                branch_query["id"] = coach_branch_id
            schedule_branches = await db.branches.find(
                branch_query,
                {"id": 1, "branch.name": 1, "assignments.course_schedule": 1},
            ).to_list(length=500)

            batch_lookup = {}  # batch_ref -> meta
            assigned_course_batches = []  # batches on assigned courses (fallback filters)
            trainer_batches = []  # batches where this coach is the trainer
            course_ids_from_batches = set()
            # Batches where this coach is the assigned trainer (authoritative for student count)
            coach_trainer_batch_refs = set()
            coach_trainer_batch_names = set()
            coach_trainer_batch_keys = set()  # (course_id, batch_ref)

            for branch in schedule_branches:
                branch_name = ((branch.get("branch") or {}).get("name")) or "Branch"
                schedule = ((branch.get("assignments") or {}).get("course_schedule")) or []
                for entry in schedule:
                    if not isinstance(entry, dict):
                        continue
                    course_id = str(entry.get("course_id") or entry.get("courseId") or "").strip()
                    if not course_id:
                        continue
                    for index, batch in enumerate(entry.get("batches") or []):
                        if not isinstance(batch, dict):
                            continue
                        trainer_id = str(batch.get("coach_id") or batch.get("coachId") or "").strip()
                        trainer_match = bool(trainer_id) and trainer_id in match_ids
                        on_assigned_course = course_id in course_ids
                        if not (trainer_match or on_assigned_course):
                            continue
                        if trainer_match:
                            course_ids_from_batches.add(course_id)
                        batch_ref = str(
                            batch.get("batch_id") or batch.get("id") or f"__index:{index}__"
                        ).strip()
                        batch_name = str(batch.get("batch_name") or batch.get("name") or "").strip() or f"Batch {index + 1}"
                        meta = {
                            "batch_ref": batch_ref,
                            "batch_name": batch_name,
                            "course_id": course_id,
                            "branch_id": branch.get("id"),
                            "branch_name": branch_name,
                        }
                        batch_lookup[batch_ref] = meta
                        # Also index by name for legacy enrollments that store the name
                        if batch_name and batch_name not in batch_lookup:
                            batch_lookup[batch_name] = meta
                        if trainer_match:
                            coach_trainer_batch_refs.add(batch_ref)
                            if batch_name:
                                coach_trainer_batch_names.add(batch_name)
                            coach_trainer_batch_keys.add((course_id, batch_ref))
                            trainer_batches.append(meta)
                        elif on_assigned_course:
                            assigned_course_batches.append(meta)

            # Prefer trainer batches for filters; fall back to assigned-course batches
            batches_for_filters = trainer_batches if trainer_batches else assigned_course_batches

            # Include courses that only appear via trainer-matched batches
            for cid in course_ids_from_batches:
                if cid not in course_ids:
                    course_ids.append(cid)

            if not course_ids:
                return {
                    "students": [],
                    "total": 0,
                    "coach_id": coach_id,
                    "coach_name": coach.get("full_name", ""),
                    "filters": {"courses": [], "batches": []},
                }

            # Atlas (this cluster) rejects $in/$or/$eq on enrollments — use plain equality per course.
            async def _enrollments_for_courses(use_branch: bool) -> list:
                rows = []
                for course_id in course_ids:
                    query: dict = {"course_id": course_id, "is_active": True}
                    if use_branch and coach_branch_id:
                        query["branch_id"] = coach_branch_id
                    rows.extend(await db.enrollments.find(query).to_list(length=1000))
                return rows

            enrollments = await _enrollments_for_courses(use_branch=True)
            if not enrollments and coach_branch_id:
                enrollments = await _enrollments_for_courses(use_branch=False)

            course_by_id = {}
            for course_id in course_ids:
                course = await db.courses.find_one({"id": course_id})
                if course:
                    course_by_id[course_id] = course
            courses_for_filters = [
                {
                    "id": c["id"],
                    "name": c.get("title") or c.get("name") or "Unknown Course",
                }
                for c in course_by_id.values()
            ]

            # Batches grouped by course for single-batch inference when enrollment.batch_ref is empty
            batches_by_course: dict = {}
            for b in batches_for_filters:
                batches_by_course.setdefault(b["course_id"], []).append(b)

            # Deduplicate filter batches by (course_id, batch_ref)
            seen_batch_keys = set()
            unique_batches = []
            for b in batches_for_filters:
                key = (b["course_id"], b["batch_ref"])
                if key in seen_batch_keys:
                    continue
                seen_batch_keys.add(key)
                course = course_by_id.get(b["course_id"])
                unique_batches.append({
                    **b,
                    "course_name": (course.get("title") or course.get("name") or "Unknown Course") if course else "Unknown Course",
                })

            enhanced_students = []
            has_unassigned = False
            seen_student_course = set()
            restrict_to_coach_batches = bool(coach_trainer_batch_refs)

            for enrollment in enrollments:
                student = await db.users.find_one({
                    "id": enrollment["student_id"],
                    "role": "student",
                })
                course = course_by_id.get(enrollment["course_id"]) or await db.courses.find_one(
                    {"id": enrollment["course_id"]}
                )
                if not student or not course:
                    continue

                raw_batch_ref = str(enrollment.get("batch_ref") or "").strip()
                batch_meta = batch_lookup.get(raw_batch_ref) if raw_batch_ref else None

                # Infer batch when enrollment has no batch_ref but the course has exactly one batch
                if not batch_meta and not raw_batch_ref:
                    course_batches = batches_by_course.get(course["id"]) or []
                    # Prefer batches on the enrollment's branch when available
                    enr_branch = str(enrollment.get("branch_id") or "").strip()
                    if enr_branch:
                        branch_batches = [b for b in course_batches if str(b.get("branch_id") or "") == enr_branch]
                        if branch_batches:
                            course_batches = branch_batches
                    if len(course_batches) == 1:
                        batch_meta = course_batches[0]
                        raw_batch_ref = batch_meta.get("batch_ref") or ""

                if batch_meta:
                    resolved_ref = str(batch_meta.get("batch_ref") or raw_batch_ref or "").strip()
                    batch_name = batch_meta.get("batch_name") or resolved_ref or "Unassigned"
                elif raw_batch_ref:
                    resolved_ref = raw_batch_ref
                    batch_name = raw_batch_ref
                else:
                    resolved_ref = "__unassigned__"
                    batch_name = "Unassigned"
                    has_unassigned = True

                # When this coach trains specific batches, only count students in those batches
                if restrict_to_coach_batches:
                    belongs_to_coach = (
                        resolved_ref in coach_trainer_batch_refs
                        or raw_batch_ref in coach_trainer_batch_refs
                        or (batch_name and batch_name in coach_trainer_batch_names)
                        or (course["id"], resolved_ref) in coach_trainer_batch_keys
                    )
                    if not belongs_to_coach:
                        continue

                dedupe_key = (str(student["id"]), str(course["id"]), resolved_ref)
                if dedupe_key in seen_student_course:
                    continue
                seen_student_course.add(dedupe_key)

                contact = student.get("contact_info") if isinstance(student.get("contact_info"), dict) else {}
                enhanced_students.append({
                    "id": student["id"],
                    "student_name": student.get(
                        "full_name",
                        f"{student.get('first_name', '')} {student.get('last_name', '')}".strip(),
                    ) or "Unknown Student",
                    "email": student.get("email") or contact.get("email") or "",
                    "phone": student.get("phone") or contact.get("phone") or "",
                    "course_id": course["id"],
                    "course_name": course.get("title") or course.get("name") or "Unknown Course",
                    "batch_ref": resolved_ref,
                    "batch_name": batch_name,
                    "enrollment_date": enrollment.get("enrollment_date", enrollment.get("created_at", "")),
                    "status": "active" if enrollment.get("is_active", True) else "inactive",
                    "progress": enrollment.get("progress", 0),
                    "branch_id": enrollment.get("branch_id", ""),
                    "enrollment_id": enrollment.get("id"),
                })

            if has_unassigned and not restrict_to_coach_batches:
                unassigned_key = ("__unassigned__", "__unassigned__")
                if unassigned_key not in seen_batch_keys:
                    unique_batches.append({
                        "batch_ref": "__unassigned__",
                        "batch_name": "Unassigned",
                        "course_id": "",
                        "course_name": "",
                        "branch_id": "",
                        "branch_name": "",
                    })

            # Sort by student name then course
            enhanced_students.sort(key=lambda s: ((s.get("student_name") or "").lower(), (s.get("course_name") or "").lower()))

            unique_student_ids = {str(s.get("id")) for s in enhanced_students if s.get("id")}
            return {
                "students": enhanced_students,
                "total": len(enhanced_students),
                "unique_students": len(unique_student_ids),
                "coach_id": coach_id,
                "coach_name": coach.get("full_name", ""),
                "filters": {
                    "courses": courses_for_filters,
                    "batches": unique_batches,
                },
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching coach students: {str(e)}")

    @staticmethod
    async def get_coach_stats():
        """Get coach statistics"""
        db = get_db()

        total_coaches = await db.coaches.count_documents({})
        active_coaches = await db.coaches.count_documents({"is_active": True})
        inactive_coaches = total_coaches - active_coaches

        # Get coaches by expertise areas
        pipeline = [
            {"$unwind": "$areas_of_expertise"},
            {"$group": {"_id": "$areas_of_expertise", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        expertise_stats = await db.coaches.aggregate(pipeline).to_list(length=None)

        return {
            "total_coaches": total_coaches,
            "active_coaches": active_coaches,
            "inactive_coaches": inactive_coaches,
            "expertise_distribution": expertise_stats
        }

    @staticmethod
    async def login_coach(login_data: CoachLogin):
        """Authenticate coach and return JWT token"""
        try:
            db = get_db()
            
            # Find coach by email
            coach = await db.coaches.find_one({"contact_info.email": login_data.email})
            if not coach:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid email or password"
                )
            
            # Verify password
            if not verify_password(login_data.password, coach["password_hash"]):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid email or password"
                )

            # M14: pending/rejected registrations cannot log in (legacy coaches without field = approved)
            approval = str(coach.get("approval_status") or "approved").lower()
            if approval == "pending":
                raise HTTPException(
                    status_code=403,
                    detail="Your coach registration is pending admin approval.",
                )
            if approval == "rejected":
                raise HTTPException(
                    status_code=403,
                    detail="Your coach registration was rejected. Please contact administrator.",
                )

            # M14-S04: expired subscription past grace (only if they have a sub record)
            try:
                from utils.coach_subscription_service import (
                    assert_coach_subscription_allows_login,
                )

                await assert_coach_subscription_allows_login(coach)
            except HTTPException:
                raise
            except Exception:
                pass
            
            # Check if coach is active
            if not coach.get("is_active", True):
                raise HTTPException(
                    status_code=401,
                    detail="Account is inactive. Please contact administrator."
                )
            
            # Create access token
            access_token_expires = 60 * 24  # 24 hours in minutes
            access_token = create_access_token(
                data={
                    "sub": coach["id"],
                    "email": coach["contact_info"]["email"],
                    "role": "coach",
                    "coach_id": coach["id"]
                }
            )
            
            # Prepare coach data for response (without sensitive info)
            coach_data = serialize_doc(coach)
            if "password_hash" in coach_data:
                del coach_data["password_hash"]
            
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "coach": coach_data,
                "expires_in": access_token_expires * 60,  # Convert to seconds
                "message": "Login successful"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Internal server error during coach login: {str(e)}"
            )

    @staticmethod
    async def forgot_password(email: str):
        """Initiate password reset process for coach"""
        db = get_db()
        coach = await db.coaches.find_one({"contact_info.email": email})

        if not coach:
            # Don't reveal that the coach does not exist
            return {"message": "If a coach account with that email exists, a password reset link has been sent."}

        # Generate a short-lived token for password reset (same as student implementation)
        reset_token = create_access_token(
            data={"sub": coach["id"], "scope": "password_reset"},
            expires_delta=timedelta(minutes=15)
        )

        # Send password reset email with coach branding using webhook (same as /api/email/send-webhook-email)
        coach_name = coach.get("full_name", "Coach")
        from utils.email_service import send_password_reset_email_webhook
        email_sent = await send_password_reset_email_webhook(email, reset_token, coach_name, "coach")

        # Log the password reset attempt (same as student implementation)
        import logging
        logging.info(f"Password reset requested for coach {email}. Email sent: {email_sent}")

        # Also send SMS as backup (if phone number exists) - same as student implementation
        coach_phone = coach.get("phone") or coach.get("contact_info", {}).get("phone")
        if coach_phone:
            from utils.helpers import send_sms
            sms_message = f"Coach password reset requested for your account. Check your email ({email}) for reset instructions. If you didn't request this, please ignore."
            await send_sms(coach_phone, sms_message)

        response = {"message": "If a coach account with that email exists, a password reset link has been sent."}

        # Include token in response for testing purposes (same as student implementation)
        import os
        if os.environ.get("TESTING") == "True":
            response["reset_token"] = reset_token
            response["email_sent"] = email_sent

        return response

    @staticmethod
    async def reset_password(token: str, new_password: str):
        """Reset coach password using a token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get("scope") != "password_reset":
                raise HTTPException(status_code=401, detail="Invalid token scope")

            coach_id = payload.get("sub")
            if not coach_id:
                raise HTTPException(status_code=401, detail="Invalid token")

        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        new_hashed_password = hash_password(new_password)
        db = get_db()
        result = await db.coaches.update_one(
            {"id": coach_id},
            {"$set": {"password": new_hashed_password, "updated_at": datetime.utcnow()}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Coach not found")

        return {"message": "Password has been reset successfully"}

    @staticmethod
    async def send_credentials_email(
        coach_id: str,
        request: Request,
        current_admin: dict
    ):
        """Send login credentials to coach via email with secure password reset token"""
        db = get_db()

        # Get coach details
        coach = await db.coaches.find_one({"id": coach_id})
        if not coach:
            raise HTTPException(status_code=404, detail="Coach not found")

        # Get coach email
        coach_email = coach.get("email") or coach.get("contact_info", {}).get("email")
        if not coach_email:
            raise HTTPException(status_code=400, detail="Coach email not found")

        # Get coach name
        coach_name = coach.get("full_name") or f"{coach.get('personal_info', {}).get('first_name', '')} {coach.get('personal_info', {}).get('last_name', '')}".strip()
        if not coach_name:
            coach_name = "Coach"

        # Generate a password reset token for secure access (same as forgot password)
        reset_token = create_access_token(
            data={"sub": coach["id"], "scope": "password_reset"},
            expires_delta=timedelta(minutes=15)
        )

        # Create password reset link
        reset_link = f"http://localhost:3022/coach/reset-password?token={reset_token}"

        # Prepare email content
        subject = "Your Coach Login Credentials - Marshalarts Academy"

        # Create HTML email content (consistent with forgot password styling)
        html_message = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Coach Login Credentials</title>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e9ecef; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 12px; color: #6c757d; }}
        .btn {{ display: inline-block; padding: 12px 24px; background-color: #ea580c; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; margin: 20px 0; }}
        .btn:hover {{ opacity: 0.9; }}
        .info-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .warning {{ background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .role-header {{ color: #ea580c; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; color: #ea580c;">🥋 Marshalarts Academy</h1>
            <p style="margin: 5px 0 0 0; color: #666;">Coach Portal</p>
        </div>

        <div class="content">
            <h2 class="role-header">Welcome to the Coach Portal!</h2>
            <p>Hello <strong>{coach_name}</strong>,</p>

            <p>Your coach account has been created successfully. You can now access the coach portal using your login credentials.</p>

            <div class="info-box">
                <p><strong>📧 Email:</strong> {coach_email}</p>
                <p><strong>🔑 Password Setup:</strong> Click the button below to set up your password securely</p>
                <div style="text-align: center;">
                    <a href="{reset_link}" class="btn">Set Up Your Password</a>
                </div>
            </div>

            <div class="warning">
                <strong>🔒 Security Notice:</strong> This password setup link will expire in 15 minutes for security. After setting your password, you can log in at the coach portal.
            </div>

            <h3 style="color: #ea580c;">Next Steps:</h3>
            <ul>
                <li><strong>Step 1:</strong> Click "Set Up Your Password" button above</li>
                <li><strong>Step 2:</strong> Create a secure password for your account</li>
                <li><strong>Step 3:</strong> Visit the <a href="http://localhost:3022/coach/login" style="color: #ea580c;">Coach Portal</a> to log in</li>
                <li><strong>Step 4:</strong> Complete your profile setup and review your assigned courses</li>
            </ul>

            <p><strong>Important:</strong> The password setup link expires in 15 minutes. If it expires, please contact your administrator for a new link.</p>

            <p>If you have any questions or need assistance, please contact our support team.</p>
        </div>

        <div class="footer">
            <p style="margin: 0;">© 2025 Marshalarts Academy. All rights reserved.</p>
            <p style="margin: 5px 0 0 0;">This is an automated message, please do not reply.</p>
        </div>
    </div>
</body>
</html>""".strip()

        # Plain text version (consistent with forgot password format)
        plain_message = f"""🥋 Welcome to Marshalarts Academy - Coach Portal

Hello {coach_name},

Your coach account has been created successfully. You can now set up your password and access the coach portal.

📧 Email: {coach_email}
🔑 Password Setup: {reset_link}
🔗 Coach Portal: http://localhost:3022/coach/login

🔒 SECURITY NOTICE: The password setup link above will expire in 15 minutes for security. After setting your password, you can log in at the coach portal.

Next Steps:
1. Click the password setup link above
2. Create a secure password for your account
3. Visit the Coach Portal to log in with your email and new password
4. Complete your profile setup and review your assigned courses

IMPORTANT: The password setup link expires in 15 minutes. If it expires, please contact your administrator for a new link.

If you have any questions or need assistance, please contact our support team.

Best regards,
Marshalarts Academy Team

© 2025 Marshalarts Academy. All rights reserved.
This is an automated message, please do not reply.""".strip()

        try:
            # Send email using custom webhook service (same as forgot password implementation)
            from utils.email_service import send_custom_email_webhook
            email_sent = await send_custom_email_webhook(
                coach_email,
                subject,
                html_message,
                plain_message
            )

            # Log the credentials email attempt (same as forgot password implementation)
            import logging
            logging.info(f"Coach credentials email requested for {coach_email}. Email sent: {email_sent}")

            # Log the activity in database
            await log_activity(
                request=request,
                action="send_coach_credentials",
                user_id=current_admin.get("id"),
                user_name=current_admin.get("full_name", "Admin"),
                details={
                    "coach_id": coach_id,
                    "coach_name": coach_name,
                    "coach_email": coach_email,
                    "email_sent": email_sent
                }
            )

            # Prepare response (consistent with forgot password format)
            response = {
                "message": "Login credentials have been sent to the coach's email address",
                "email_sent": email_sent,
                "coach_email": coach_email
            }

            # Include additional info for testing purposes (same as forgot password implementation)
            import os
            if os.environ.get("TESTING") == "True":
                response["coach_id"] = coach_id
                response["coach_name"] = coach_name
                response["reset_token"] = reset_token
                response["reset_link"] = reset_link

            return response

        except Exception as e:
            # Enhanced error logging (consistent with forgot password implementation)
            import logging
            logging.error(f"Failed to send coach credentials email to {coach_email}: {str(e)}")

            raise HTTPException(
                status_code=500,
                detail=f"Failed to send credentials email: {str(e)}"
            )
