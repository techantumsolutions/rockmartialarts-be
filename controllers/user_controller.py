from fastapi import HTTPException, Depends, Request
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from collections import defaultdict
import secrets
import uuid

from models.user_models import UserCreate, UserUpdate, BaseUser, UserRole
from utils.auth import hash_password, require_role, get_current_active_user
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin
from utils.database import get_db
from utils.helpers import serialize_doc, log_activity, send_sms, send_whatsapp
from utils.enrollment_dates import resolve_enrollment_end_date
from utils.subscription_dates import is_subscription_period_over
from utils.student_branch_sync import sync_student_branch_assignment


def _enrollment_date_to_iso(val):
    """Serialize enrollment start/end dates for API JSON (Mongo datetime or ISO string)."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, str):
        return val
    return str(val)


def _parse_enrollment_dt_field(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _enrollment_display_priority(enrollment: dict) -> int:
    """Prefer paid/active over cancelled (aligned with student dashboard enrollment merge)."""
    ps = str(enrollment.get("payment_status") or "").lower()
    st = str(enrollment.get("status") or "").lower()
    if ps in ("cancelled", "canceled", "refunded"):
        return 0
    if st in ("cancelled", "canceled"):
        return 0
    is_active = enrollment.get("is_active", True)
    if ps == "paid" and is_active is not False:
        return 100
    if ps == "paid":
        return 80
    if ps in ("pending", "overdue", "processing"):
        return 60
    if ps == "failed":
        return 40
    return 20


def _enrollment_recency_ts(en: dict) -> float:
    for k in ("start_date", "enrollment_date", "updated_at", "created_at"):
        d = _parse_enrollment_dt_field(en.get(k))
        if d:
            return d.timestamp()
    return 0.0


def _select_primary_enrollment(enrollments: list) -> Optional[dict]:
    if not enrollments:
        return None
    if len(enrollments) == 1:
        return enrollments[0]
    return max(
        enrollments,
        key=lambda e: (_enrollment_display_priority(e), _enrollment_recency_ts(e)),
    )


def _derive_enrollment_status(enrollment: dict) -> str:
    """
    Derive human status from persisted fields when `status` is missing/stale.
    Priority:
    1) explicit `status` (if present)
    2) canceled / paused by payment_status
    3) expired by end_date
    4) active/inactive by is_active
    """
    explicit = str(enrollment.get("status") or "").strip().lower()
    is_active = bool(enrollment.get("is_active", True))
    payment_status = str(enrollment.get("payment_status") or "").strip().lower()
    if payment_status in {"cancelled", "canceled"}:
        return "cancelled"
    if payment_status == "paused":
        return "paused"

    if is_subscription_period_over(enrollment.get("end_date")):
        return "expired"

    if not is_active:
        return "inactive"

    if explicit in {"completed", "paused", "cancelled", "canceled", "inactive", "active"}:
        return explicit.replace("canceled", "cancelled")

    return "active"


class UserController:
    @staticmethod
    async def create_user(
        user_data: UserCreate,
        request: Request,
        current_user: dict = None
    ):
        """Create new user (Super Admin or Coach Admin)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")
        
        # If a coach admin is creating a user, they must be in the same branch
        if current_role == UserRole.COACH_ADMIN:
            if not current_user.get("branch_id") or user_data.branch_id != current_user["branch_id"]:
                raise HTTPException(status_code=403, detail="Coach Admins can only create users for their own branch.")
            # Coach admins cannot create other admins
            if user_data.role in [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN]:
                raise HTTPException(status_code=403, detail="Coach Admins cannot create other admin users.")

        # If a branch manager is creating a user, they must be in the same branch
        if current_role == UserRole.BRANCH_MANAGER:
            # Get branch manager's assigned branch
            branch_manager_branch_id = current_user.get("branch_assignment", {}).get("branch_id") or current_user.get("branch_id")
            if not branch_manager_branch_id or user_data.branch_id != branch_manager_branch_id:
                raise HTTPException(status_code=403, detail="Branch Managers can only create users for their assigned branch.")
            # Branch managers cannot create admin users
            if user_data.role in [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]:
                raise HTTPException(status_code=403, detail="Branch Managers cannot create admin users.")

        # Check if user exists
        db = get_db()
        existing_user = await db.users.find_one({
            "$or": [{"email": user_data.email}, {"phone": user_data.phone}]
        })
        if existing_user:
            raise HTTPException(status_code=400, detail="User already exists")
        
        # Generate password if not provided
        if not user_data.password:
            user_data.password = secrets.token_urlsafe(8)

        hashed_password = hash_password(user_data.password)

        # Generate full name from first and last name
        full_name = f"{user_data.first_name} {user_data.last_name}".strip()

        # Create user dictionary with proper structure (similar to registration API)
        user_dict = {
            "id": str(uuid.uuid4()),
            "email": user_data.email,
            "phone": user_data.phone,
            "first_name": user_data.first_name,
            "last_name": user_data.last_name,
            "full_name": full_name,
            "role": user_data.role.value,  # Convert enum to string
            "is_active": True,
            "date_of_birth": user_data.date_of_birth.isoformat() if user_data.date_of_birth else None,
            "gender": user_data.gender,
            "password": hashed_password,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        if user_data.biometric_id:
            b = str(user_data.biometric_id).strip()
            if b:
                user_dict["biometric_id"] = b
                user_dict["essl_user_id"] = b
        if getattr(user_data, "essl_user_id", None):
            v = str(user_data.essl_user_id).strip()
            if v:
                user_dict["essl_user_id"] = v
                if not user_dict.get("biometric_id"):
                    user_dict["biometric_id"] = v
        # Admin-created students should use onboarding link until they complete it (not website self-registration)
        if user_data.role == UserRole.STUDENT:
            user_dict["has_credentials"] = False
        else:
            user_dict["has_credentials"] = True

        # Set branch_id for staff members
        if user_data.branch_id:
            user_dict["branch_id"] = user_data.branch_id

        # BACKWARD COMPATIBILITY: Store course and branch data in user document
        # This ensures existing frontend integrations continue to work
        if user_data.course:
            user_dict["course"] = {
                "category_id": user_data.course.category_id,
                "course_id": user_data.course.course_id,
                "duration": user_data.course.duration
            }

        if user_data.branch:
            user_dict["branch"] = {
                "location_id": user_data.branch.location_id,
                "branch_id": user_data.branch.branch_id
            }
            # Also set branch_id for easier querying
            if not user_dict.get("branch_id"):
                user_dict["branch_id"] = user_data.branch.branch_id

        if user_data.address is not None:
            user_dict["address"] = user_data.address
        if user_data.emergency_contact is not None:
            user_dict["emergency_contact"] = user_data.emergency_contact
        if user_data.role == UserRole.STUDENT and user_data.student_level:
            user_dict["student_level"] = user_data.student_level

        if user_data.role == UserRole.STUDENT and user_data.course and user_data.branch:
            from utils.branch_courses import assert_course_available_at_branch
            await assert_course_available_at_branch(
                db, user_data.branch.branch_id, user_data.course.course_id
            )

        await db.users.insert_one(user_dict)

        # Create enrollment record if course information is provided (for students)
        enrollment_id = None
        if user_data.course and user_data.branch and user_data.role == UserRole.STUDENT:
            try:
                from models.enrollment_models import Enrollment, PaymentStatus

                start_date = datetime.utcnow()
                end_date = await resolve_enrollment_end_date(
                    db, user_data.course.duration, start_date
                )

                # Create enrollment record in the proper collection
                enrollment = Enrollment(
                    student_id=user_dict["id"],
                    course_id=user_data.course.course_id,
                    branch_id=user_data.branch.branch_id,
                    start_date=start_date,
                    end_date=end_date,
                    fee_amount=0.0,  # Will be updated when payment is processed
                    admission_fee=0.0,  # Will be updated when payment is processed
                    payment_status=PaymentStatus.PENDING,
                    enrollment_date=start_date,
                    is_active=True
                )

                enrollment_doc = enrollment.dict()
                enrollment_doc["duration_id"] = user_data.course.duration
                enrollment_result = await db.enrollments.insert_one(enrollment_doc)
                enrollment_id = enrollment.id

            except Exception as e:
                # Log error but don't fail the user creation if enrollment creation fails
                print(f"❌ Error creating enrollment record: {e}")
                pass
        
        # Send credentials
        await send_sms(user_dict["phone"], f"Account created. Email: {user_dict['email']}, Password: {user_data.password}")

        await log_activity(
            request=request,
            action="admin_create_user",
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"created_user_id": user_dict["id"], "created_user_email": user_dict["email"], "role": user_dict["role"]}
        )

        response_data = {"message": "User created successfully", "user_id": user_dict["id"]}
        if enrollment_id:
            response_data["enrollment_id"] = enrollment_id
            response_data["message"] = "User created and enrolled successfully"

        return response_data

    @staticmethod
    async def get_users(
        role: Optional[UserRole] = None,
        branch_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = None
    ):
        """Get users with filtering - accessible by Super Admin, Coach Admin, and Coach"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        filter_query = {}
        
        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")
        
        # Apply role-based filtering
        if current_role == UserRole.COACH_ADMIN:
            # Coach admins can only see users in their branch
            if current_user.get("branch_id"):
                filter_query["branch_id"] = current_user["branch_id"]
        elif current_role == UserRole.COACH:
            # Coaches can only see students in their branch
            if current_user.get("branch_id"):
                filter_query["branch_id"] = current_user["branch_id"]
            filter_query["role"] = UserRole.STUDENT.value  # Only show students to coaches
        elif current_role == UserRole.BRANCH_MANAGER:
            # Branch managers see only students enrolled in their managed branches
            branch_manager_id = current_user.get("id")
            if branch_manager_id:
                managed_branches = await get_db().branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                managed_branch_ids = [b["id"] for b in managed_branches]
                if not managed_branch_ids:
                    # No branches assigned: return empty list
                    filter_query["id"] = {"$in": []}
                else:
                    enrollments = await get_db().enrollments.find({"branch_id": {"$in": managed_branch_ids}, "is_active": True}).to_list(length=5000)
                    student_ids = list(set(e["student_id"] for e in enrollments))
                    if not student_ids:
                        filter_query["id"] = {"$in": []}
                    else:
                        filter_query["id"] = {"$in": student_ids}
                    filter_query["role"] = UserRole.STUDENT.value
            else:
                filter_query["id"] = {"$in": []}
        
        # Apply additional filters
        if role:
            # Only allow if current user has permission to see this role
            if current_role == UserRole.COACH and role != UserRole.STUDENT:
                raise HTTPException(status_code=403, detail="Coaches can only view student users")
            filter_query["role"] = role.value
            
        if branch_id:
            # Ensure user can only filter by their own branch if not super admin
            if current_role in [UserRole.COACH_ADMIN, UserRole.COACH]:
                if current_user.get("branch_id") != branch_id:
                    raise HTTPException(status_code=403, detail="You can only view users from your own branch")
            filter_query["branch_id"] = branch_id
        
        db = get_db()
        users = await (
            db.users.find(filter_query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        total_count = await db.users.count_documents(filter_query)
        
        for user in users:
            user.pop("password", None)
            user["date_of_birth"] = user.get("date_of_birth")
            user["gender"] = user.get("gender")
        
        return {
            "users": serialize_doc(users),
            "total": total_count,
            "skip": skip,
            "limit": limit,
            "message": f"Retrieved {len(users)} users"
        }

    @staticmethod
    async def get_user(
        user_id: str,
        current_user: dict = None
    ):
        """Get single user by ID - accessible by Super Admin, Coach Admin, and Coach"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        user = await db.users.find_one({"id": user_id})

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        # Role-based access control
        if current_role in [UserRole.COACH_ADMIN, UserRole.COACH]:
            # Coach Admin and Coach can only view users from their own branch
            if current_user.get("branch_id") != user.get("branch_id"):
                raise HTTPException(status_code=403, detail="You can only view users from your own branch")
        elif current_role == UserRole.BRANCH_MANAGER:
            # Branch managers can only view students from branches they manage
            if user.get("role") != "student":
                raise HTTPException(status_code=403, detail="Branch managers can only view student profiles")

            # Check if the student is enrolled in any branch managed by this branch manager
            branch_assignment = current_user.get("branch_assignment", {})
            managed_branch_id = branch_assignment.get("branch_id")

            if not managed_branch_id:
                raise HTTPException(status_code=403, detail="No branch assignment found for branch manager")

            # Check if student has any enrollments in the managed branch
            db = get_db()
            student_enrollments = await db.enrollments.find({
                "student_id": user_id,
                "branch_id": managed_branch_id,
                "is_active": True
            }).to_list(1)

            if not student_enrollments:
                raise HTTPException(status_code=403, detail="You can only view students enrolled in branches you manage")

        # Remove sensitive information
        user.pop("password", None)
        user["date_of_birth"] = user.get("date_of_birth")
        user["gender"] = user.get("gender")

        # For students, also fetch enrollment data to provide complete course information
        enrollments = []
        if user.get("role") == "student":
            try:
                enrollments = await db.enrollments.find({
                    "student_id": user_id,
                    "is_active": True
                }).to_list(100)

                # Enrich enrollment data with course and branch details
                for enrollment in enrollments:
                    # Get course details
                    course = await db.courses.find_one({"id": enrollment["course_id"]})
                    if course:
                        enrollment["course_details"] = {
                            "id": course["id"],
                            "title": course.get("title", "Unknown Course"),
                            "category_id": course.get("category_id"),
                            "difficulty_level": course.get("difficulty_level", "Beginner")
                        }

                    # Get branch details
                    branch = await db.branches.find_one({"id": enrollment["branch_id"]})
                    if branch:
                        enrollment["branch_details"] = {
                            "id": branch["id"],
                            "name": branch.get("branch", {}).get("name", "Unknown Branch"),
                            "location_id": branch.get("branch", {}).get("address", {}).get("city", "")
                        }

            except Exception as e:
                print(f"Error fetching enrollment data for user {user_id}: {e}")
                # Don't fail the request if enrollment fetch fails

        return {
            "user": serialize_doc(user),
            "enrollments": serialize_doc(enrollments),
            "message": "User retrieved successfully"
        }

    @staticmethod
    async def _resolve_enrollment_course_fee(
        user_id: str,
        course_id: str,
        branch_id: str,
        duration_ref: Optional[str],
        batch_ref: Optional[str] = None,
    ) -> Optional[float]:
        """Course fee for enrollment/renewal using batch-aware pricing (no admission)."""
        if not course_id or not branch_id or not duration_ref:
            return None
        try:
            from controllers.payment_controller import PaymentController

            info = await PaymentController.get_course_payment_info(
                course_id,
                branch_id,
                duration_ref,
                batch_ref=(batch_ref or None),
                optional_student_id=user_id,
            )
            fee = info.pricing.course_fee
            return float(fee) if fee is not None else None
        except Exception as exc:
            print(f"⚠️ Could not resolve enrollment course fee: {exc}")
            return None

    @staticmethod
    async def handle_enrollment_updates(user_id: str, course_data: dict, branch_data: dict):
        """Handle enrollment record updates when course/branch data changes"""
        db = get_db()
        branch_id = (branch_data or {}).get("branch_id")
        if not branch_id:
            return

        target_user = await db.users.find_one({"id": user_id})
        old_branch_id = None
        if target_user:
            old_branch_id = target_user.get("branch_id") or (target_user.get("branch") or {}).get("branch_id")

        try:
            existing_enrollments = await db.enrollments.find({
                "student_id": user_id,
                "is_active": True
            }).to_list(100)

            if course_data:
                course_id = course_data.get("course_id")
                duration_ref = course_data.get("duration")
                batch_ref = (course_data.get("batch_ref") or "").strip() or None
                if course_id:
                    fee_amount = await UserController._resolve_enrollment_course_fee(
                        user_id,
                        course_id,
                        branch_id,
                        duration_ref,
                        batch_ref=batch_ref,
                    )

                    existing_enrollment = None
                    for enrollment in existing_enrollments:
                        if enrollment.get("course_id") == course_id:
                            existing_enrollment = enrollment
                            break

                    enrollment_patch: Dict[str, Any] = {
                        "branch_id": branch_id,
                        "updated_at": datetime.utcnow(),
                        "is_active": True,
                    }
                    if duration_ref:
                        enrollment_patch["duration_id"] = duration_ref
                    if batch_ref:
                        enrollment_patch["batch_ref"] = batch_ref
                    if fee_amount is not None:
                        enrollment_patch["fee_amount"] = fee_amount

                    if existing_enrollment:
                        await db.enrollments.update_one(
                            {"id": existing_enrollment["id"]},
                            {"$set": enrollment_patch}
                        )
                        print(f"✅ Updated existing enrollment branch: {existing_enrollment['id']}")
                    else:
                        from models.enrollment_models import Enrollment, PaymentStatus
                        from utils.branch_courses import assert_course_available_at_branch

                        await assert_course_available_at_branch(db, branch_id, course_id)

                        start_date = datetime.utcnow()
                        end_date = await resolve_enrollment_end_date(
                            db, duration_ref, start_date
                        )

                        enrollment = Enrollment(
                            student_id=user_id,
                            course_id=course_id,
                            branch_id=branch_id,
                            start_date=start_date,
                            end_date=end_date,
                            fee_amount=fee_amount if fee_amount is not None else 0.0,
                            admission_fee=0.0,
                            payment_status=PaymentStatus.PENDING,
                            enrollment_date=start_date,
                            is_active=True,
                            batch_ref=batch_ref,
                        )

                        enrollment_doc = enrollment.dict()
                        if duration_ref:
                            enrollment_doc["duration_id"] = duration_ref
                        await db.enrollments.insert_one(enrollment_doc)
                        print(f"✅ Created new enrollment: {enrollment.id}")

                        for old_enrollment in existing_enrollments:
                            if old_enrollment["id"] != enrollment.id:
                                await db.enrollments.update_one(
                                    {"id": old_enrollment["id"]},
                                    {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
                                )

            await sync_student_branch_assignment(
                db, user_id, branch_id, old_branch_id=old_branch_id
            )
        except Exception as e:
            print(f"❌ Error handling enrollment updates: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to update student branch enrollment records",
            ) from e

    @staticmethod
    async def _apply_enrollment_dates(
        user_id: str,
        start_d: Optional[date],
        end_d: Optional[date],
    ):
        """Update start/end on the student's primary active enrollment."""
        db = get_db()
        enr = await db.enrollments.find_one({"student_id": user_id, "is_active": True})
        if not enr:
            raise HTTPException(
                status_code=400,
                detail="No active enrollment found; assign a course before setting dates.",
            )

        def as_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            if isinstance(val, date):
                return datetime.combine(val, datetime.min.time())
            return val

        cur_start = as_dt(enr.get("start_date"))
        cur_end = as_dt(enr.get("end_date"))
        eff_start = datetime.combine(start_d, datetime.min.time()) if start_d is not None else cur_start
        eff_end = datetime.combine(end_d, datetime.min.time()) if end_d is not None else cur_end
        if eff_start and eff_end and eff_end < eff_start:
            raise HTTPException(status_code=400, detail="End date must be on or after start date.")

        sets: Dict[str, Any] = {"updated_at": datetime.utcnow()}
        if start_d is not None:
            sets["start_date"] = eff_start
        if end_d is not None:
            sets["end_date"] = eff_end
        await db.enrollments.update_one({"id": enr["id"]}, {"$set": sets})

    @staticmethod
    async def update_user(
        user_id: str,
        user_update: UserUpdate,
        request: Request,
        current_user: dict = None
    ):
        """Update user (Super Admin or Coach Admin)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        target_user = await get_db().users.find_one({"id": user_id})
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        if current_role == UserRole.COACH_ADMIN:
            # Coach Admins can only update students in their own branch
            if target_user["role"] != UserRole.STUDENT.value:
                raise HTTPException(status_code=403, detail="Coach Admins can only update student profiles.")
            if target_user.get("branch_id") != current_user.get("branch_id"):
                raise HTTPException(status_code=403, detail="Coach Admins can only update students in their own branch.")

        elif current_role == UserRole.BRANCH_MANAGER:
            # Branch Managers can only update students in their managed branches
            print(f"🔍 DEBUG UPDATE: Branch manager attempting to update user {user_id}")
            print(f"🔍 DEBUG UPDATE: Target user role: {target_user.get('role')}")
            print(f"🔍 DEBUG UPDATE: Current user: {current_user}")

            if target_user["role"] != UserRole.STUDENT.value:
                print(f"🔍 DEBUG UPDATE: Blocking - target user is not a student")
                raise HTTPException(status_code=403, detail="Branch Managers can only update student profiles.")

            # TEMPORARY: Allow all student updates for branch managers to test the basic functionality
            print(f"🔍 DEBUG UPDATE: TEMPORARY - Allowing all student updates for branch managers")
            # TODO: Re-enable branch-based filtering after basic functionality is confirmed

            # # Check if the student is in a branch managed by this branch manager
            # branch_manager_id = current_user.get("id")
            # print(f"🔍 DEBUG UPDATE: Branch manager ID: {branch_manager_id}")

            # if not branch_manager_id:
            #     raise HTTPException(status_code=403, detail="Branch manager ID not found")

            # db = get_db()

            # # Find all branches managed by this branch manager
            # managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
            # print(f"🔍 DEBUG UPDATE: Found {len(managed_branches)} managed branches by manager_id")

            # # Fallback: If no branches found by manager_id, try the old branch_assignment approach
            # if not managed_branches:
            #     print(f"🔍 DEBUG UPDATE: No branches found by manager_id, trying branch_assignment fallback")
            #     branch_assignment = current_user.get("branch_assignment")
            #     print(f"🔍 DEBUG UPDATE: Branch assignment: {branch_assignment}")
            #     if branch_assignment and branch_assignment.get("branch_id"):
            #         fallback_branch = await db.branches.find_one({"id": branch_assignment["branch_id"], "is_active": True})
            #         if fallback_branch:
            #             managed_branches = [fallback_branch]
            #             print(f"🔍 DEBUG UPDATE: Using fallback branch: {fallback_branch['id']}")

            # if not managed_branches:
            #     print(f"🔍 DEBUG UPDATE: No branches assigned to this manager")
            #     raise HTTPException(status_code=403, detail="No branches assigned to this manager")

            # managed_branch_ids = [branch["id"] for branch in managed_branches]
            # print(f"🔍 DEBUG UPDATE: Managed branch IDs: {managed_branch_ids}")

            # # Check if the student is enrolled in any of the managed branches
            # student_enrollments = await db.enrollments.find({"student_id": user_id, "is_active": True}).to_list(100)
            # student_branch_ids = [enrollment["branch_id"] for enrollment in student_enrollments if enrollment.get("branch_id")]
            # print(f"🔍 DEBUG UPDATE: Student enrolled in branches: {student_branch_ids}")
            # print(f"🔍 DEBUG UPDATE: Student enrollments: {student_enrollments}")

            # # Check if any of the student's enrollments are in the managed branches
            # has_permission = any(branch_id in managed_branch_ids for branch_id in student_branch_ids)
            # print(f"🔍 DEBUG UPDATE: Has permission to update: {has_permission}")

            # if not has_permission:
            #     print(f"🔍 DEBUG UPDATE: Blocking - student not enrolled in managed branches")
            #     raise HTTPException(status_code=403, detail="Branch Managers can only update students enrolled in their managed branches.")

        # Convert user_update to dict and handle date serialization
        update_dict = user_update.dict(exclude_unset=True)
        enrollment_start_date = update_dict.pop("enrollment_start_date", None)
        enrollment_end_date = update_dict.pop("enrollment_end_date", None)
        enrollment_dates_requested = (
            enrollment_start_date is not None or enrollment_end_date is not None
        )

        update_data = {}

        for k, v in update_dict.items():
            if k == "date_of_birth" and isinstance(v, date):
                # Convert date object to ISO string for MongoDB compatibility
                update_data[k] = v.isoformat()
            elif k == "course" and v:
                # Handle nested course object (v could be dict or CourseInfo object)
                if isinstance(v, dict):
                    update_data["course"] = v
                else:
                    update_data["course"] = {
                        "category_id": v.category_id,
                        "course_id": v.course_id,
                        "duration": v.duration
                    }
            elif k == "branch" and v:
                # Handle nested branch object (v could be dict or BranchInfo object)
                if isinstance(v, dict):
                    update_data["branch"] = v
                    update_data["branch_id"] = v.get("branch_id")  # Top-level for querying/display
                else:
                    update_data["branch"] = {
                        "location_id": v.location_id,
                        "branch_id": v.branch_id
                    }
                    update_data["branch_id"] = v.branch_id  # Top-level for querying/display
            elif k in ["course_category_id", "course_id", "course_duration", "location_id"]:
                # Handle flat fields for backward compatibility
                # Convert flat fields to nested structure
                if k == "course_category_id":
                    if "course" not in update_data:
                        update_data["course"] = {}
                    update_data["course"]["category_id"] = v
                elif k == "course_id":
                    if "course" not in update_data:
                        update_data["course"] = {}
                    update_data["course"]["course_id"] = v
                elif k == "course_duration":
                    if "course" not in update_data:
                        update_data["course"] = {}
                    update_data["course"]["duration"] = v
                elif k == "location_id":
                    if "branch" not in update_data:
                        update_data["branch"] = {}
                    update_data["branch"]["location_id"] = v
            else:
                update_data[k] = v

        if not update_data and not enrollment_dates_requested:
            raise HTTPException(status_code=400, detail="No update data provided")

        if target_user.get("role") == UserRole.STUDENT.value and enrollment_dates_requested:
            await UserController._apply_enrollment_dates(
                user_id, enrollment_start_date, enrollment_end_date
            )

        # Auto-generate full_name if first_name or last_name is being updated
        if "first_name" in update_data or "last_name" in update_data:
            # Get current values from database if not provided in update
            current_first_name = update_data.get("first_name", target_user.get("first_name", ""))
            current_last_name = update_data.get("last_name", target_user.get("last_name", ""))

            # Generate full_name from first_name and last_name
            full_name = f"{current_first_name} {current_last_name}".strip()
            update_data["full_name"] = full_name
            print(f"🔄 Auto-generated full_name: '{full_name}' from first_name: '{current_first_name}', last_name: '{current_last_name}'")

        if not update_data:
            update_data = {}
        update_data["updated_at"] = datetime.utcnow()

        # Sparse unique indexes still index explicit BSON null; clear mapping with $unset instead of $set null.
        unset_fields: Dict[str, str] = {}
        for key in ("biometric_id", "essl_user_id"):
            if key not in update_data:
                continue
            val = update_data[key]
            if val is None or (isinstance(val, str) and not str(val).strip()):
                unset_fields[key] = ""
                del update_data[key]

        mongo_update: Dict[str, Any] = {}
        if update_data:
            mongo_update["$set"] = update_data
        if unset_fields:
            mongo_update["$unset"] = unset_fields

        new_branch_id = update_data.get("branch_id") or (update_data.get("branch") or {}).get("branch_id")
        old_branch_id = target_user.get("branch_id") or (target_user.get("branch") or {}).get("branch_id")
        branch_changed = (
            target_user.get("role") == UserRole.STUDENT.value
            and new_branch_id
            and new_branch_id != old_branch_id
        )

        # Handle enrollment updates if course/branch data is being changed
        if target_user.get("role") == "student" and ("course" in update_data or "branch" in update_data):
            course_data = update_data.get("course", {})
            branch_data = update_data.get("branch", {})

            if branch_data and branch_data.get("branch_id"):
                await UserController.handle_enrollment_updates(user_id, course_data, branch_data)
            elif branch_changed:
                await sync_student_branch_assignment(
                    get_db(), user_id, new_branch_id, old_branch_id=old_branch_id
                )

        result = await get_db().users.update_one(
            {"id": user_id},
            mongo_update,
        )

        if result.matched_count == 0:
            # This case should be rare due to the check above, but it's good practice
            raise HTTPException(status_code=404, detail="User not found")

        if branch_changed and "branch" not in update_data:
            await sync_student_branch_assignment(
                get_db(), user_id, new_branch_id, old_branch_id=old_branch_id
            )
        
        await log_activity(
            request=request,
            action="admin_update_user",
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"updated_user_id": user_id, "update_data": user_update.dict(exclude_unset=True)}
        )

        return {"message": "User updated successfully"}

    @staticmethod
    async def force_password_reset(
        user_id: str,
        request: Request,
        current_user: dict = None
    ):
        """Force a password reset for a user (Admins only)."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
            
        target_user = await get_db().users.find_one({"id": user_id})
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get current user role as enum
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        # Check permissions
        if current_role == UserRole.COACH_ADMIN:
            if target_user.get("branch_id") != current_user.get("branch_id"):
                raise HTTPException(status_code=403, detail="Coach Admins can only reset passwords for users in their own branch.")
            if target_user.get("role") not in [UserRole.STUDENT.value, UserRole.COACH.value]:
                raise HTTPException(status_code=403, detail="Coach Admins can only reset passwords for Students and Coaches.")

        # Generate a new temporary password
        new_password = secrets.token_urlsafe(8)
        hashed_password = hash_password(new_password)
        from utils.family_accounts import sync_account_password
        await sync_account_password(get_db(), target_user, hashed_password)

        # Log the activity
        await log_activity(
            request=request,
            action="admin_force_password_reset",
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"reset_user_id": user_id, "reset_user_email": target_user["email"]}
        )

        # Send the new password to the user
        message = f"Your password has been reset by an administrator. Your new temporary password is: {new_password}"
        await send_sms(target_user["phone"], message)
        await send_whatsapp(target_user["phone"], message)

        return {"message": f"Password for user {target_user['full_name']} has been reset and sent to them."}

    @staticmethod
    async def send_student_notification(
        user_id: str,
        kind: str,
        request: Request,
        current_user: dict = None,
    ):
        """Welcome / payment reminder SMS + WhatsApp (super admin only via route)."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user.get("role") != UserRole.STUDENT.value:
            raise HTTPException(status_code=400, detail="Target user is not a student")

        phone = (user.get("phone") or "").strip()
        if not phone:
            raise HTTPException(status_code=400, detail="Student has no phone number on file")

        name = (user.get("full_name") or "").strip() or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Student"

        enrollments = await db.enrollments.find({"student_id": user_id}).sort([("updated_at", -1)]).to_list(80)
        primary = _select_primary_enrollment(enrollments) if enrollments else None
        course_name = "your course"
        validity_note = ""
        branch_note = ""
        if primary:
            cid = primary.get("course_id")
            if cid:
                cdoc = await db.courses.find_one({"id": cid})
                if cdoc:
                    course_name = cdoc.get("title") or cdoc.get("name") or course_name
            ed = primary.get("end_date")
            if ed:
                iso = _enrollment_date_to_iso(ed)
                if iso:
                    validity_note = f" Validity ends {str(iso)[:10]}."
            bid = primary.get("branch_id")
            if bid:
                bdoc = await db.branches.find_one({"id": bid})
                if bdoc:
                    bn = (bdoc.get("branch") or {}).get("name") or bdoc.get("name")
                    if bn:
                        branch_note = f" Branch: {bn}."

        if kind == "welcome":
            msg = (
                f"Hello {name}, welcome to Rock Martial Arts Academy! "
                f"You are enrolled in {course_name}.{branch_note}{validity_note} "
                f"We are glad to have you — train safe!"
            )
        elif kind == "payment_reminder":
            msg = (
                f"Hi {name}, friendly reminder from Rock Martial Arts Academy regarding fees for {course_name}.{branch_note}{validity_note} "
                f"Please complete payment when you can. Thank you!"
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid notification kind")

        ok_wa = await send_whatsapp(phone, msg)
        ok_sms = await send_sms(phone, msg)

        await log_activity(
            request=request,
            action="admin_student_notify",
            user_id=current_user["id"],
            user_name=current_user.get("full_name"),
            details={"target_student_id": user_id, "kind": kind, "whatsapp_ok": ok_wa, "sms_ok": ok_sms},
        )

        label = "Welcome message sent" if kind == "welcome" else "Payment reminder sent"
        return {
            "message": f"{label} (SMS and WhatsApp channels).",
            "whatsapp": ok_wa,
            "sms": ok_sms,
        }

    @staticmethod
    async def deactivate_user(
        user_id: str,
        request: Request,
        current_user: dict = None
    ):
        """Deactivate user (Super Admin only) — uses shared status history when target is a student."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if str(user.get("role") or "").lower() == "student":
            from utils.student_status_service import update_student_account_status

            out = await update_student_account_status(
                student_id=user_id,
                is_active=False,
                reason="Deactivated via admin deactivate endpoint",
                current_user=current_user,
                source="deactivate_endpoint",
            )
            await log_activity(
                request=request,
                action="admin_deactivate_user",
                user_id=current_user["id"],
                user_name=current_user.get("full_name"),
                details={"deactivated_user_id": user_id, "via": "student_status"},
            )
            return {"message": out.get("message") or "User deactivated successfully", **{k: v for k, v in out.items() if k != "message"}}

        result = await db.users.update_one(
            {"id": user_id},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        await log_activity(
            request=request,
            action="admin_deactivate_user",
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"deactivated_user_id": user_id}
        )

        return {"message": "User deactivated successfully"}

    @staticmethod
    async def update_student_status(
        user_id: str,
        body,
        request: Request,
        current_user: dict = None,
    ):
        """M08-S01: activate/deactivate student account with reason + history."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_status_service import update_student_account_status

        out = await update_student_account_status(
            student_id=user_id,
            is_active=bool(body.is_active),
            reason=getattr(body, "reason", None),
            current_user=current_user,
            source="status_api",
        )
        try:
            await log_activity(
                request=request,
                action="admin_student_status_update",
                user_id=current_user.get("id"),
                user_name=current_user.get("full_name"),
                details={
                    "student_id": user_id,
                    "is_active": out.get("is_active"),
                    "previous_status": out.get("previous_status"),
                    "reason": getattr(body, "reason", None),
                    "changed": out.get("changed"),
                },
            )
        except Exception:
            pass
        return out

    @staticmethod
    async def get_student_status_history(user_id: str, current_user: dict, limit: int = 50):
        """M08-S01: list status history for a student (scoped)."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        db = get_db()
        student = await db.users.find_one({"id": user_id})
        if not student or str(student.get("role") or "").lower() != "student":
            raise HTTPException(status_code=404, detail="Student not found")

        from utils.student_status_service import (
            assert_can_manage_student_status,
            list_student_status_history,
        )

        role = str(current_user.get("role") or "").lower()
        if role == "branch_manager":
            await assert_can_manage_student_status(db, student, current_user)
        elif role not in {"super_admin", "coach_admin"}:
            raise HTTPException(status_code=403, detail="Not allowed")

        rows = await list_student_status_history(user_id, limit=limit)
        return {"student_id": user_id, "history": rows, "total": len(rows)}

    @staticmethod
    async def get_student_biometric_mapping(user_id: str, current_user: dict):
        """M09-S02: get student biometric mapping."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_biometric_mapping_service import get_mapping

        return await get_mapping(user_id, current_user)

    @staticmethod
    async def set_student_biometric_mapping(user_id: str, body, request: Request, current_user: dict = None):
        """M09-S02: create/update biometric mapping with duplicate checks."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_biometric_mapping_service import set_mapping

        out = await set_mapping(user_id, body, current_user)
        try:
            mapping = out.get("mapping") or {}
            await log_activity(
                request=request,
                action="admin_student_biometric_mapping_set",
                user_id=current_user.get("id"),
                user_name=current_user.get("full_name"),
                details={
                    "student_id": user_id,
                    "biometric_id": mapping.get("biometric_id"),
                    "essl_user_id": mapping.get("essl_user_id"),
                },
            )
        except Exception:
            pass
        return out

    @staticmethod
    async def clear_student_biometric_mapping(user_id: str, request: Request, current_user: dict = None):
        """M09-S02: remove biometric mapping."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_biometric_mapping_service import clear_mapping

        out = await clear_mapping(user_id, current_user)
        try:
            await log_activity(
                request=request,
                action="admin_student_biometric_mapping_clear",
                user_id=current_user.get("id"),
                user_name=current_user.get("full_name"),
                details={"student_id": user_id},
            )
        except Exception:
            pass
        return out

    @staticmethod
    async def list_student_biometric_mappings(
        current_user: dict,
        q: Optional[str] = None,
        branch_id: Optional[str] = None,
        mapped: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ):
        """M09-S02: list students with mapping status."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_biometric_mapping_service import list_mappings

        return await list_mappings(
            current_user,
            q=q,
            branch_id=branch_id,
            mapped=mapped,
            skip=skip,
            limit=limit,
        )

    @staticmethod
    async def get_student_id_card(user_id: str, current_user: dict):
        """M08-S03: view current student ID card (or empty if not generated)."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_id_card_service import get_id_card_for_student

        return await get_id_card_for_student(user_id, current_user)

    @staticmethod
    async def generate_student_id_card(
        user_id: str,
        request: Request,
        current_user: dict = None,
        regenerate: bool = False,
    ):
        """M08-S03: generate or regenerate student ID card + QR token."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_id_card_service import get_or_create_id_card

        out = await get_or_create_id_card(
            user_id, current_user, regenerate=bool(regenerate)
        )
        try:
            card = out.get("card") or {}
            await log_activity(
                request=request,
                action="admin_student_id_card_generate",
                user_id=current_user.get("id"),
                user_name=current_user.get("full_name"),
                details={
                    "student_id": user_id,
                    "card_number": card.get("card_number"),
                    "regenerate": bool(regenerate),
                },
            )
        except Exception:
            pass
        return out

    @staticmethod
    async def revoke_student_id_card(
        user_id: str,
        request: Request,
        current_user: dict = None,
    ):
        """M08-S03: revoke active student ID card / QR."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_id_card_service import revoke_id_card

        out = await revoke_id_card(user_id, current_user)
        try:
            await log_activity(
                request=request,
                action="admin_student_id_card_revoke",
                user_id=current_user.get("id"),
                user_name=current_user.get("full_name"),
                details={"student_id": user_id, **out},
            )
        except Exception:
            pass
        return out

    @staticmethod
    async def delete_user(
        user_id: str,
        request: Request,
        current_user: dict = None
    ):
        """Permanently delete user (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Check if user exists
        user = await get_db().users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Don't allow deletion of super admin users
        if user.get("role") == "super_admin":
            raise HTTPException(status_code=403, detail="Cannot delete super admin users")

        # If current user is a branch manager, ensure they can only delete students from their branch
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            # Get branch manager's assigned branch ID
            branch_assignment = current_user.get("branch_assignment")
            direct_branch_id = current_user.get("branch_id")

            manager_branch_id = None
            if branch_assignment and branch_assignment.get("branch_id"):
                manager_branch_id = branch_assignment["branch_id"]
            elif direct_branch_id:
                manager_branch_id = direct_branch_id

            if not manager_branch_id:
                raise HTTPException(status_code=403, detail="No branch assigned to this manager")

            # Check if student belongs to branch manager's branch
            user_branch_id = user.get("branch_id")
            belongs_to_branch = False

            # First check if user has direct branch_id assignment
            if user_branch_id == manager_branch_id:
                belongs_to_branch = True
            else:
                # Query enrollments collection for this student
                student_enrollments = await get_db().enrollments.find({
                    "student_id": user_id,
                    "is_active": True
                }).to_list(length=100)

                # Check if any enrollment is for the branch manager's branch
                for enrollment in student_enrollments:
                    if enrollment.get("branch_id") == manager_branch_id:
                        belongs_to_branch = True
                        break

            if not belongs_to_branch:
                # Provide more detailed error message
                error_msg = f"You can only delete students from your assigned branch. "
                if user_branch_id:
                    error_msg += f"Student is assigned to branch {user_branch_id}, but you manage branch {manager_branch_id}."
                else:
                    error_msg += f"Student has no branch assignment or enrollments in your branch ({manager_branch_id})."
                raise HTTPException(status_code=403, detail=error_msg)

        # Delete user from database
        result = await get_db().users.delete_one({"id": user_id})

        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        # Log the deletion activity
        await log_activity(
            request=request,
            action="admin_delete_user",
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"deleted_user_id": user_id, "deleted_user_email": user.get("email", "N/A")}
        )

        return {"message": "User deleted successfully"}

    @staticmethod
    async def get_student_details(
        current_user: dict,
        unassigned_only: bool = False,
        branch_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ):
        """Get detailed student information with course enrollment data (Authenticated endpoint).
        When unassigned_only=True, returns only students with no active branch enrollment (for Assign to Branch modal).
        Optional is_active filters account status (users.is_active) — does not touch enrollments."""

        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Role-based access control
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        # Unassigned-only: students with no active enrollment in any branch (for Assign to Branch dropdown)
        if unassigned_only:
            if current_role not in (UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER):
                return {"message": "No students found", "students": [], "total": 0}
            assigned_student_ids = await db.enrollments.distinct("student_id", {"is_active": True})
            # Include all student users (active/inactive); admins may need to assign/enroll newly
            # registered students before account activation is toggled.
            query = {"role": "student", "id": {"$nin": assigned_student_ids}}
            if is_active is not None:
                query["is_active"] = bool(is_active)
            students_cursor = db.users.find(query).sort("created_at", -1)
            students = await students_cursor.to_list(1000)
            if not students:
                return {"message": "No unassigned students found", "students": [], "total": 0}
            # Enrich minimally for dropdown (id, full_name, email, etc.)
            enriched_students = []
            for student in students:
                student_id = student["id"]
                age = None
                if student.get("date_of_birth"):
                    if isinstance(student["date_of_birth"], str):
                        try:
                            birth_date = datetime.strptime(student["date_of_birth"], "%Y-%m-%d").date()
                        except ValueError:
                            birth_date = None
                    elif isinstance(student["date_of_birth"], date):
                        birth_date = student["date_of_birth"]
                    else:
                        birth_date = None
                    if birth_date:
                        today = date.today()
                        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
                full_name = student.get("full_name", f"{student.get('first_name', '')} {student.get('last_name', '')}".strip())
                enriched_students.append({
                    "id": student_id,
                    "student_id": student_id,
                    "full_name": full_name or "Unknown",
                    "student_name": full_name or "Unknown",
                    "first_name": student.get("first_name", ""),
                    "last_name": student.get("last_name", ""),
                    "email": student.get("email"),
                    "phone": student.get("phone"),
                    "role": student.get("role", "student"),
                    "gender": student.get("gender", "Not specified"),
                    "age": age,
                    "date_of_birth": student.get("date_of_birth"),
                    "is_active": student.get("is_active", True),
                    "created_at": student.get("created_at"),
                    "has_credentials": bool(student.get("has_credentials", True)),
                    "start_date": None,
                    "end_date": None,
                    "branch_id": None,
                    "branch_info": None,
                    "courses": [],
                    "course_info": None,
                    "student_level": student.get("student_level"),
                })
            return {
                "message": "Unassigned students",
                "students": serialize_doc(enriched_students),
                "total": len(enriched_students),
            }

        # Build query based on user role (normal listing)
        # Same users collection as public registration; include active + inactive students
        # so super admin can edit/assign/enroll newly registered accounts.
        query = {"role": "student"}
        if is_active is not None:
            query["is_active"] = bool(is_active)

        # Apply branch filtering for non-super-admin users
        if current_role != UserRole.SUPER_ADMIN:
            if current_role == UserRole.BRANCH_MANAGER:
                # Branch managers can see students from their managed branches
                # We need to find all branches managed by this branch manager, not just one branch_id

                # Get all branches where this branch manager is the manager
                branch_manager_id = current_user.get("id")
                print(f"🔍 DEBUG: Branch manager ID from current_user: {branch_manager_id}")
                print(f"🔍 DEBUG: Current user data: {current_user}")

                if not branch_manager_id:
                    return {"message": "No students found", "students": [], "total": 0}

                # Find all branches managed by this branch manager
                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                print(f"🔍 DEBUG: Found {len(managed_branches)} managed branches")

                # Also try to find branches by checking all branches and their manager_id
                all_branches = await db.branches.find({"is_active": True}).to_list(length=None)
                print(f"🔍 DEBUG: Total active branches in database: {len(all_branches)}")
                for branch in all_branches[:3]:  # Show first 3 branches for debugging
                    print(f"🔍 DEBUG: Branch {branch.get('id', 'NO_ID')} has manager_id: {branch.get('manager_id', 'NO_MANAGER_ID')}")

                # Fallback: If no branches found by manager_id, try the old branch_assignment approach
                if not managed_branches:
                    print(f"🔍 DEBUG: No branches found by manager_id, trying branch_assignment fallback")
                    branch_assignment = current_user.get("branch_assignment")
                    if branch_assignment and branch_assignment.get("branch_id"):
                        print(f"🔍 DEBUG: Found branch_assignment: {branch_assignment}")
                        # Try to find the branch by ID from branch_assignment
                        fallback_branch = await db.branches.find_one({"id": branch_assignment["branch_id"], "is_active": True})
                        if fallback_branch:
                            managed_branches = [fallback_branch]
                            print(f"🔍 DEBUG: Using fallback branch: {fallback_branch['id']}")

                if not managed_branches:
                    return {"message": "No students found", "students": [], "total": 0}

                # Get all branch IDs managed by this branch manager
                managed_branch_ids = [branch["id"] for branch in managed_branches]
                print(f"Branch manager {branch_manager_id} manages branches for students: {managed_branch_ids}")

                # Store for later use in enrollment filtering
                managed_branch_ids_for_students = managed_branch_ids
            else:
                # Coaches: students with active enrollments at the coach's branch
                user_branch_id = current_user.get("branch_id")
                if not user_branch_id:
                    raise HTTPException(status_code=403, detail="User not assigned to any branch")
                coach_student_ids = await db.enrollments.distinct(
                    "student_id",
                    {"branch_id": user_branch_id, "is_active": True},
                )
                if not coach_student_ids:
                    return {"message": "No students found", "students": [], "total": 0}
                query["id"] = {"$in": coach_student_ids}

        # Get students (newest first)
        students_cursor = db.users.find(query).sort("created_at", -1)
        students = await students_cursor.to_list(1000)

        if not students:
            return {
                "message": "No students found",
                "students": [],
                "total": 0
            }

        # Enrich student data with course and enrollment information
        enriched_students = []

        # For branch managers, we need to filter students based on their enrollments
        if current_role == UserRole.BRANCH_MANAGER and 'managed_branch_ids_for_students' in locals():
            print(f"🔍 DEBUG: Filtering students for branch manager with managed_branch_ids: {managed_branch_ids_for_students}")

            # Get all enrollments for all managed branches
            branch_enrollments = await db.enrollments.find({"branch_id": {"$in": managed_branch_ids_for_students}, "is_active": True}).to_list(1000)
            branch_student_ids = list(set([enrollment["student_id"] for enrollment in branch_enrollments]))

            print(f"Found {len(branch_enrollments)} enrollments across {len(managed_branch_ids_for_students)} managed branches")
            print(f"Unique student IDs with enrollments: {len(branch_student_ids)}")

            # Debug: Show some enrollment details
            if branch_enrollments:
                print(f"🔍 DEBUG: Sample enrollment: {branch_enrollments[0]}")

            # Debug: Check total enrollments in database
            total_enrollments = await db.enrollments.find({"is_active": True}).to_list(1000)
            print(f"🔍 DEBUG: Total active enrollments in database: {len(total_enrollments)}")

            print(f"🔍 DEBUG: Students before filtering: {len(students)}")
            # Filter students to only include those with enrollments in the managed branches
            students = [student for student in students if student["id"] in branch_student_ids]
            print(f"🔍 DEBUG: Students after filtering: {len(students)}")

        for student in students:
            student_id = student["id"]

            # Calculate age from date_of_birth
            age = None
            if student.get("date_of_birth"):
                if isinstance(student["date_of_birth"], str):
                    try:
                        birth_date = datetime.strptime(student["date_of_birth"], "%Y-%m-%d").date()
                    except ValueError:
                        birth_date = None
                elif isinstance(student["date_of_birth"], date):
                    birth_date = student["date_of_birth"]
                else:
                    birth_date = None

                if birth_date:
                    today = date.today()
                    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

            # Get course information from multiple sources
            courses_info = []

            # All enrollment rows (inactive/cancelled included); one display row per course+branch using same
            # primary rules as student dashboard merge (paid beats cancelled — avoids mismatched dates/status).
            all_enrollments = await (
                db.enrollments.find({"student_id": student_id})
                .sort([("updated_at", -1), ("enrollment_date", -1)])
                .to_list(100)
            )

            groups = defaultdict(list)
            for e in all_enrollments:
                groups[(e.get("course_id"), e.get("branch_id"))].append(e)

            for (_cid, _bid), group in groups.items():
                enrollment = _select_primary_enrollment(group)
                if not enrollment or not enrollment.get("is_active", True):
                    continue
                course = await db.courses.find_one({"id": enrollment["course_id"]})
                if not course:
                    continue
                duration_days = None
                if enrollment.get("start_date") and enrollment.get("end_date"):
                    start_date = enrollment["start_date"]
                    end_date = enrollment["end_date"]
                    if isinstance(start_date, str):
                        start_date = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    if isinstance(end_date, str):
                        end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    duration_days = (end_date - start_date).days

                duration_label = None
                did = enrollment.get("duration_id")
                if did:
                    ddoc = await db.durations.find_one({"id": did})
                    if not ddoc:
                        ddoc = await db.durations.find_one({"code": did})
                    if ddoc:
                        duration_label = ddoc.get("name") or ddoc.get("code")

                level = course.get("difficulty_level", "Beginner")

                courses_info.append({
                    "enrollment_id": enrollment.get("id"),
                    "course_id": enrollment["course_id"],
                    "course_name": course.get("title", "Unknown Course"),
                    "level": level,
                    "duration": duration_label or (f"{duration_days} days" if duration_days is not None else "Not specified"),
                    "start_date": _enrollment_date_to_iso(enrollment.get("start_date")),
                    "end_date": _enrollment_date_to_iso(enrollment.get("end_date")),
                    "enrollment_date": enrollment.get("enrollment_date"),
                    "payment_status": enrollment.get("payment_status", "pending"),
                    "is_active": enrollment.get("is_active", True),
                    "branch_id": enrollment.get("branch_id"),
                })

            # DEPRECATED: Legacy fallback for students with course data in user documents
            # This will be removed after data migration is complete
            if not courses_info and student.get("course"):
                course_info = student["course"]
                branch_info = student.get("branch", {})

                # Get course details from courses collection
                course_id = course_info.get("course_id")
                course = await db.courses.find_one({"id": course_id})
                if course:
                    # Get branch details
                    branch_name = "Not specified"
                    if branch_info.get("branch_id"):
                        branch = await db.branches.find_one({"id": branch_info["branch_id"]})
                        if branch:
                            branch_name = branch.get("name", "Unknown Branch")

                    # Get duration details - handle both UUID and string formats
                    duration_name = course_info.get("duration", "Not specified")
                    if course_info.get("duration"):
                        # If it's already a readable string, use it directly
                        if isinstance(course_info["duration"], str) and not course_info["duration"].startswith(("uuid-", "duration-")):
                            duration_name = course_info["duration"]
                        else:
                            # Try to look up in durations collection
                            duration = await db.durations.find_one({"id": course_info["duration"]})
                            if duration:
                                duration_name = duration.get("name", duration_name)

                    courses_info.append({
                        "course_name": course.get("title", "Unknown Course"),
                        "level": course.get("difficulty_level", "Beginner"),
                        "duration": duration_name,
                        "enrollment_date": student.get("created_at"),
                        "payment_status": "paid",  # Assume paid for registration-based students
                        "source": "legacy_user_document"  # Mark as legacy data for migration tracking
                    })

            # Resolve branch_info — primary active enrollment only (matches Branch column)
            branch_info_response = None
            branch_id_for_name = None
            active_enrollments_only = [
                e for e in all_enrollments if e.get("is_active", True)
            ]
            row_primary = (
                _select_primary_enrollment(active_enrollments_only)
                if active_enrollments_only
                else None
            )
            if row_primary:
                branch_id_for_name = row_primary.get("branch_id")
            if not branch_id_for_name and student.get("branch", {}).get("branch_id"):
                branch_id_for_name = student["branch"]["branch_id"]
            if not branch_id_for_name and student.get("branch_id"):
                branch_id_for_name = student["branch_id"]
            if branch_id_for_name:
                branch_doc = await db.branches.find_one({"id": branch_id_for_name})
                if branch_doc:
                    branch_info_response = {
                        "branch_id": branch_id_for_name,
                        "location_id": branch_doc.get("location_id", ""),
                        "branch_name": branch_doc.get("branch", {}).get("name", "Unknown Branch")
                    }

            primary_enrollment = row_primary
            start_date_out = _enrollment_date_to_iso(primary_enrollment.get("start_date")) if primary_enrollment else None
            end_date_out = _enrollment_date_to_iso(primary_enrollment.get("end_date")) if primary_enrollment else None

            # Prepare student details response
            student_details = {
                "id": student_id,
                "student_id": student_id,
                "full_name": student.get("full_name", f"{student.get('first_name', '')} {student.get('last_name', '')}").strip(),
                "student_name": student.get("full_name", f"{student.get('first_name', '')} {student.get('last_name', '')}").strip(),
                "first_name": student.get("first_name", ""),
                "last_name": student.get("last_name", ""),
                "email": student.get("email"),
                "phone": student.get("phone"),
                "role": student.get("role", "student"),
                "gender": student.get("gender", "Not specified"),
                "age": age,
                "date_of_birth": student.get("date_of_birth"),
                "is_active": student.get("is_active", True),
                "created_at": student.get("created_at"),
                "has_credentials": bool(student.get("has_credentials", True)),
                "start_date": start_date_out,
                "end_date": end_date_out,
                "primary_enrollment_id": primary_enrollment.get("id") if primary_enrollment else None,
                "subscription_payment_status": primary_enrollment.get("payment_status") if primary_enrollment else None,
                "branch_id": branch_id_for_name or student.get("branch_id"),
                "branch_info": branch_info_response,
                "address": student.get("address"),
                "courses": courses_info,
                "enrollments": courses_info,  # For compatibility with frontend
                "action": "view_profile",  # Default action - can be customized based on requirements
                "student_level": student.get("student_level"),
            }

            enriched_students.append(student_details)

        if (
            branch_id
            and branch_id != "all"
            and current_role == UserRole.SUPER_ADMIN
        ):
            enriched_students = [
                s
                for s in enriched_students
                if (s.get("branch_info") or {}).get("branch_id") == branch_id
                or s.get("branch_id") == branch_id
            ]

        return {
            "message": f"Retrieved {len(enriched_students)} student details successfully",
            "students": serialize_doc(enriched_students),
            "total": len(enriched_students)
        }

    @staticmethod
    async def get_user_enrollments(user_id: str, current_user: dict = None):
        """Get enrollment history for a specific student"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Verify user exists and is a student
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        if user.get("role") != "student":
            raise HTTPException(status_code=400, detail="User is not a student")

        # Permission check: coaches can only view students from their branch
        current_role = current_user.get("role")
        if current_role in ["coach", "coach_admin"] and current_user.get("branch_id"):
            if user.get("branch_id") != current_user["branch_id"]:
                raise HTTPException(status_code=403, detail="You can only view students from your branch")
        elif current_role == "branch_manager":
            # Branch managers can only view enrollments for students in branches they manage
            branch_assignment = current_user.get("branch_assignment", {})
            managed_branch_id = branch_assignment.get("branch_id")

            if not managed_branch_id:
                raise HTTPException(status_code=403, detail="No branch assignment found for branch manager")

            # Check if student has any enrollments in the managed branch
            student_enrollments = await db.enrollments.find({
                "student_id": user_id,
                "branch_id": managed_branch_id,
                "is_active": True
            }).to_list(1)

            if not student_enrollments:
                raise HTTPException(status_code=403, detail="You can only view enrollments for students in branches you manage")

        try:
            # Get enrollments for this student
            enrollments = await db.enrollments.find({
                "student_id": user_id
            }).sort("created_at", -1).to_list(length=100)

            # Enhance enrollments with course and branch information
            enhanced_enrollments = []
            for enrollment in enrollments:
                # Get course details
                course = await db.courses.find_one({"id": enrollment.get("course_id")})

                # Get branch details
                branch = await db.branches.find_one({"id": enrollment.get("branch_id")})

                derived_status = _derive_enrollment_status(enrollment)
                enhanced_enrollment = serialize_doc(enrollment)
                enhanced_enrollment.update({
                    "course_name": course.get("title", course.get("name", "Unknown Course")) if course else "Unknown Course",
                    "course_difficulty": course.get("difficulty_level", "Beginner") if course else "Beginner",
                    "branch_name": branch.get("branch", {}).get("name", "Unknown Branch") if branch else "Unknown Branch",
                    "enrollment_date": enrollment.get("enrollment_date", enrollment.get("created_at", "")),
                    "start_date": _enrollment_date_to_iso(enrollment.get("start_date")),
                    "end_date": _enrollment_date_to_iso(enrollment.get("end_date")),
                    "completion_date": enrollment.get("completion_date"),
                    "status": derived_status,
                    "progress": enrollment.get("progress", 0),
                    "is_active": enrollment.get("is_active", True),
                    "payment_status": enrollment.get("payment_status"),
                    "updated_at": _enrollment_date_to_iso(enrollment.get("updated_at")),
                    "created_at": _enrollment_date_to_iso(enrollment.get("created_at")),
                })
                enhanced_enrollments.append(enhanced_enrollment)

            return {
                "enrollments": enhanced_enrollments,
                "total": len(enhanced_enrollments)
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching enrollment history: {str(e)}")

    @staticmethod
    async def get_user_payments(user_id: str, current_user: dict = None):
        """Get payment history for a specific student"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Verify user exists and is a student
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        if user.get("role") != "student":
            raise HTTPException(status_code=400, detail="User is not a student")

        # Permission check: coaches can only view students from their branch
        current_role = current_user.get("role")
        if current_role in ["coach", "coach_admin"] and current_user.get("branch_id"):
            if user.get("branch_id") != current_user["branch_id"]:
                raise HTTPException(status_code=403, detail="You can only view students from your branch")
        elif current_role == "branch_manager":
            # Branch managers can only view payments for students in branches they manage
            branch_assignment = current_user.get("branch_assignment", {})
            managed_branch_id = branch_assignment.get("branch_id")

            if not managed_branch_id:
                raise HTTPException(status_code=403, detail="No branch assignment found for branch manager")

            # Check if student has any enrollments in the managed branch
            student_enrollments = await db.enrollments.find({
                "student_id": user_id,
                "branch_id": managed_branch_id,
                "is_active": True
            }).to_list(1)

            if not student_enrollments:
                raise HTTPException(status_code=403, detail="You can only view payments for students in branches you manage")

        try:
            # Get payments for this student with course and enrollment information
            pipeline = [
                {"$match": {"student_id": user_id}},
                {
                    "$lookup": {
                        "from": "enrollments",
                        "localField": "enrollment_id",
                        "foreignField": "id",
                        "as": "enrollment_info"
                    }
                },
                {"$unwind": {"path": "$enrollment_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "courses",
                        "localField": "enrollment_info.course_id",
                        "foreignField": "id",
                        "as": "course_info"
                    }
                },
                {"$unwind": {"path": "$course_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$project": {
                        "id": 1,
                        "student_id": 1,
                        "enrollment_id": 1,
                        "amount": 1,
                        "payment_type": 1,
                        "payment_method": 1,
                        "payment_status": 1,
                        "transaction_id": 1,
                        "payment_date": 1,
                        "due_date": 1,
                        "notes": 1,
                        "course_name": {"$ifNull": ["$course_info.title", "$course_info.name"]},
                        "course_difficulty": "$course_info.difficulty_level",
                        "enrollment_date": "$enrollment_info.enrollment_date",
                        "created_at": 1,
                        "updated_at": 1
                    }
                },
                {"$sort": {"created_at": -1}}
            ]

            payments = await db.payments.aggregate(pipeline).to_list(length=100)

            # Convert to serializable format
            enhanced_payments = []
            for payment in payments:
                enhanced_payment = {}
                for key, value in payment.items():
                    if key == "_id":
                        continue
                    elif hasattr(value, 'isoformat'):  # datetime objects
                        enhanced_payment[key] = value.isoformat()
                    else:
                        enhanced_payment[key] = value

                # Add formatted payment description
                course_name = enhanced_payment.get("course_name", "Course")
                payment_type = enhanced_payment.get("payment_type", "payment")
                enhanced_payment["description"] = f"{course_name} - {payment_type.replace('_', ' ').title()}"

                enhanced_payments.append(enhanced_payment)

            return {
                "payments": enhanced_payments,
                "total": len(enhanced_payments)
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching payment history: {str(e)}")
