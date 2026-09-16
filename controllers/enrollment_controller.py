from fastapi import HTTPException, Depends, status
from typing import Optional
from datetime import datetime, timedelta

from models.enrollment_models import EnrollmentCreate, Enrollment, PaymentStatus
from models.enrollment_branch_change_models import EnrollmentBranchChangeRequest, EnrollmentBranchChangeCreate, EnrollmentBranchChangeStatus
from models.payment_models import Payment
from models.student_models import StudentEnrollmentCreate
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.database import db, get_db
from utils.helpers import serialize_doc, send_whatsapp
from utils.admission_fee_rules import should_charge_admission_fee_for_checkout
from utils.branch_geography import assert_branch_accepts_enrollment
from utils.branch_courses import assert_course_available_at_branch

class EnrollmentController:
    @staticmethod
    async def create_enrollment(
        enrollment_data: EnrollmentCreate,
        current_user: dict = None
    ):
        """Create student enrollment"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Get current user role
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        # Branch manager permission check
        if current_role == UserRole.BRANCH_MANAGER:
            # Get branch manager's assigned branch
            branch_manager_branch_id = current_user.get("branch_assignment", {}).get("branch_id") or current_user.get("branch_id")
            if not branch_manager_branch_id or enrollment_data.branch_id != branch_manager_branch_id:
                raise HTTPException(status_code=403, detail="Branch Managers can only create enrollments for their assigned branch.")

        # Coach admin permission check
        if current_role == UserRole.COACH_ADMIN:
            if not current_user.get("branch_id") or enrollment_data.branch_id != current_user["branch_id"]:
                raise HTTPException(status_code=403, detail="Coach Admins can only create enrollments for their own branch.")

        # Validate student, course, and branch exist
        student = await db.users.find_one({"id": enrollment_data.student_id, "role": "student"})
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        course = await db.courses.find_one({"id": enrollment_data.course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        branch = await assert_branch_accepts_enrollment(db, enrollment_data.branch_id) 
        await assert_course_available_at_branch(
            db, enrollment_data.branch_id, enrollment_data.course_id, require_active_branch=False
        )
        # Calculate end date
        end_date = enrollment_data.start_date + timedelta(days=course["duration_months"] * 30)
        
        enrollment = Enrollment(
            **enrollment_data.dict(),
            end_date=end_date,
            next_due_date=enrollment_data.start_date + timedelta(days=30)
        )
        
        await db.enrollments.insert_one(enrollment.dict())
        
        # Create initial payment records
        admission_payment = Payment(
            student_id=enrollment_data.student_id,
            enrollment_id=enrollment.id,
            amount=enrollment_data.admission_fee,
            payment_type="admission_fee",
            payment_method="pending",
            payment_status=PaymentStatus.PENDING,
            due_date=datetime.utcnow() + timedelta(days=7)
        )
        
        course_payment = Payment(
            student_id=enrollment_data.student_id,
            enrollment_id=enrollment.id,
            amount=enrollment_data.fee_amount,
            payment_type="course_fee",
            payment_method="pending", 
            payment_status=PaymentStatus.PENDING,
            due_date=enrollment_data.start_date
        )
        
        await db.payments.insert_many([admission_payment.dict(), course_payment.dict()])
        
        # Send enrollment confirmation
        await send_whatsapp(student["phone"], f"Welcome! You're enrolled in {course['name']}. Start date: {enrollment_data.start_date.date()}")
        
        return {"message": "Enrollment created successfully", "enrollment_id": enrollment.id}

    @staticmethod
    async def get_enrollments(
        student_id: Optional[str] = None,
        course_id: Optional[str] = None,
        branch_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = Depends(get_current_active_user)
    ):
        """Get enrollments with filtering"""
        filter_query = {}
        if student_id:
            filter_query["student_id"] = student_id
        if course_id:
            filter_query["course_id"] = course_id
        if branch_id:
            filter_query["branch_id"] = branch_id
        
        # Role-based filtering
        if current_user["role"] == "student":
            filter_query["student_id"] = current_user["id"]
        elif current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            filter_query["branch_id"] = current_user["branch_id"]
        elif current_user.get("role") == "branch_manager":
            managed = current_user.get("managed_branches") or []
            if not managed:
                return {"enrollments": []}
            req_b = filter_query.get("branch_id")
            if req_b and req_b not in managed:
                raise HTTPException(status_code=403, detail="Access denied for this branch")
            if not req_b:
                filter_query["branch_id"] = {"$in": managed}

        enrollments = await db.enrollments.find(filter_query).skip(skip).limit(limit).to_list(length=limit)
        student_ids = list({e.get("student_id") for e in enrollments if e.get("student_id")})
        users = (
            await db.users.find({"id": {"$in": student_ids}}).to_list(length=len(student_ids))
            if student_ids
            else []
        )
        user_map = {u["id"]: u for u in users}
        enriched = []
        for e in enrollments:
            doc = serialize_doc(e)
            u = user_map.get(e.get("student_id"))
            if u:
                doc["student_name"] = (
                    u.get("full_name")
                    or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
                    or "Student"
                )
            enriched.append(doc)
        return {"enrollments": enriched}

    @staticmethod
    async def get_student_courses(
        student_id: str,
        current_user: dict
    ):
        """Get student's enrolled courses"""
        # Check permission
        if current_user["role"] == "student" and current_user["id"] != student_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        enrollments = await db.enrollments.find({"student_id": student_id, "is_active": True}).to_list(length=100)
        
        # Enrich with course details
        course_ids = [e["course_id"] for e in enrollments]
        courses = await db.courses.find({"id": {"$in": course_ids}}).to_list(length=100)
        
        course_dict = {c["id"]: c for c in courses}
        
        result = []
        for enrollment in enrollments:
            course = course_dict.get(enrollment["course_id"])
            if course:
                result.append({
                    "enrollment": enrollment,
                    "course": course
                })
        
        return {"enrolled_courses": serialize_doc(result)}

    @staticmethod
    async def student_enroll_in_course(
        enrollment_data: StudentEnrollmentCreate,
        current_user: dict = Depends(require_role([UserRole.STUDENT]))
    ):
        """Allow a student to enroll themselves in a course."""
        student_id = current_user["id"]

        # Validate student, course, and branch exist
        student = await db.users.find_one({"id": student_id, "role": "student"})
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        course = await db.courses.find_one({"id": enrollment_data.course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        branch = await assert_branch_accepts_enrollment(db, enrollment_data.branch_id)
        await assert_course_available_at_branch(
            db, enrollment_data.branch_id, enrollment_data.course_id, require_active_branch=False
        )

        # Check if student is already enrolled in this course
        existing_enrollment = await db.enrollments.find_one({
            "student_id": student_id,
            "course_id": enrollment_data.course_id,
            "is_active": True
        })
        if existing_enrollment:
            raise HTTPException(status_code=400, detail="Student already enrolled in this course.")

        # Determine fee_amount based on branch pricing and branch-specific admission fee
        admission_fee = 500.0
        try:
            branch_adm = branch.get("admission_fee")
            if isinstance(branch_adm, (int, float)):
                admission_fee = float(branch_adm)
        except Exception:
            pass

        if not await should_charge_admission_fee_for_checkout(db, student_id, {"beneficiary_type": "self"}):
            admission_fee = 0.0

        # Base course fee (can be overridden by branch_pricing)
        fee_amount = course["base_fee"]
        if enrollment_data.branch_id in course.get("branch_pricing", {}):
            fee_amount = course["branch_pricing"][enrollment_data.branch_id]

        # Calculate end date
        end_date = enrollment_data.start_date + timedelta(days=course["duration_months"] * 30)

        enrollment = Enrollment(
            student_id=student_id,
            course_id=enrollment_data.course_id,
            branch_id=enrollment_data.branch_id,
            start_date=enrollment_data.start_date,
            end_date=end_date,
            fee_amount=fee_amount,
            admission_fee=admission_fee,
            next_due_date=enrollment_data.start_date + timedelta(days=30)
        )

        await db.enrollments.insert_one(enrollment.dict())

        # Create initial payment records (pending)
        admission_payment = Payment(
            student_id=student_id,
            enrollment_id=enrollment.id,
            amount=admission_fee,
            payment_type="admission_fee",
            payment_method="pending",
            payment_status=PaymentStatus.PENDING,
            due_date=datetime.utcnow() + timedelta(days=7)
        )

        course_payment = Payment(
            student_id=student_id,
            enrollment_id=enrollment.id,
            amount=fee_amount,
            payment_type="course_fee",
            payment_method="pending",
            payment_status=PaymentStatus.PENDING,
            due_date=enrollment_data.start_date
        )

        pay_docs = [course_payment.dict()]
        if admission_fee > 0:
            pay_docs.insert(0, admission_payment.dict())
        await db.payments.insert_many(pay_docs)

        # Send enrollment confirmation
        await send_whatsapp(student["phone"], f"Welcome! You're enrolled in {course['name']}. Start date: {enrollment_data.start_date.date()}")

        return {"message": "Enrollment created successfully", "enrollment_id": enrollment.id}

    @staticmethod
    async def get_student_enrollments(
        student_id: str,
        current_user: dict = None,
        active_only: bool = True
    ):
        """Get all enrollments for a specific student - for dashboard edit page"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        # Role-based access control
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user role")

        # Students can only view their own enrollments
        if current_role == UserRole.STUDENT and current_user["id"] != student_id:
            raise HTTPException(status_code=403, detail="You can only view your own enrollments")

        # Coach Admin and Coach can only view enrollments from their own branch
        if current_role in [UserRole.COACH_ADMIN, UserRole.COACH]:
            student = await db.users.find_one({"id": student_id})
            if not student:
                raise HTTPException(status_code=404, detail="Student not found")

            if current_user.get("branch_id") != student.get("branch_id"):
                raise HTTPException(status_code=403, detail="You can only view enrollments from your own branch")

        # Build query
        query = {"student_id": student_id}
        if active_only:
            query["is_active"] = True

        try:
            enrollments = await db.enrollments.find(query).to_list(100)
        except Exception as e:
            print(f"Error fetching enrollments: {e}")
            # Return empty result instead of crashing
            return {"enrollments": [], "message": "No enrollments found"}

        # Enrich enrollment data with course and branch details
        for enrollment in enrollments:
            # Get course details
            course = await db.courses.find_one({"id": enrollment["course_id"]})
            if course:
                enrollment["course_details"] = {
                    "id": course["id"],
                    "title": course.get("title", "Unknown Course"),
                    "category_id": course.get("category_id"),
                    "difficulty_level": course.get("difficulty_level", "Beginner"),
                    "description": course.get("description", "")
                }

            # Get branch details
            branch = await db.branches.find_one({"id": enrollment["branch_id"]})
            if branch:
                enrollment["branch_details"] = {
                    "id": branch["id"],
                    "name": branch.get("branch", {}).get("name", "Unknown Branch"),
                    "location": branch.get("branch", {}).get("address", {}).get("city", ""),
                    "location_id": branch.get("branch", {}).get("address", {}).get("location_id", "")
                }

        return {
            "enrollments": serialize_doc(enrollments),
            "count": len(enrollments),
            "message": "Enrollments retrieved successfully"
        }

    @staticmethod
    async def create_branch_change_request(
        enrollment_id: str,
        body: EnrollmentBranchChangeCreate,
        current_user: dict
    ):
        """Create a branch change request for an enrollment. Request is sent to branch admin for approval."""
        if not db:
            raise HTTPException(status_code=500, detail="Database connection not available")

        enrollment = await db.enrollments.find_one({
            "id": enrollment_id,
            "student_id": current_user["id"],
            "is_active": True
        })
        if not enrollment:
            raise HTTPException(status_code=404, detail="Enrollment not found or you do not have access to it.")

        new_branch = await assert_branch_accepts_enrollment(db, body.new_branch_id)

        if enrollment["branch_id"] == body.new_branch_id:
            raise HTTPException(status_code=400, detail="New branch must be different from current branch.")

        await assert_course_available_at_branch(
            db, body.new_branch_id, enrollment["course_id"], require_active_branch=False
        )

        # Avoid duplicate pending request
        existing = await db.enrollment_branch_change_requests.find_one({
            "enrollment_id": enrollment_id,
            "status": "pending"
        })
        if existing:
            raise HTTPException(
                status_code=400,
                detail="A branch change request for this enrollment is already pending."
            )

        request_doc = EnrollmentBranchChangeRequest(
            enrollment_id=enrollment_id,
            student_id=current_user["id"],
            current_branch_id=enrollment["branch_id"],
            new_branch_id=body.new_branch_id,
            reason=body.reason or "Student requested branch change"
        )
        await db.enrollment_branch_change_requests.insert_one(request_doc.dict())

        return {
            "message": "Branch change request submitted successfully. Your request will be sent to the branch admin for approval. You will be notified once the request is processed.",
            "request_id": request_doc.id
        }
