"""Onboarding controller: invite-link flow for existing students to set password and course/branch/duration/joining date."""
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException

from utils.database import get_db
from utils.admission_fee_rules import should_charge_admission_fee_for_checkout
from utils.branch_geography import assert_branch_accepts_enrollment
from utils.branch_courses import assert_course_available_at_branch
from utils.auth import hash_password
from models.enrollment_models import Enrollment, PaymentStatus
from models.payment_models import Payment, PaymentType, PaymentMethod, PaymentStatus as PayStatus

# Token valid for 5 days (super admin only)
ONBOARDING_TOKEN_EXPIRY_DAYS = 5


def _utc_now():
    """Current time in UTC (naive) for consistent expiry comparison."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_expired(expires_at) -> bool:
    """True if expires_at is in the past. Handles naive/aware and MongoDB datetime."""
    if expires_at is None:
        return False
    now = _utc_now()
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at < now


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked = local[0] + "***"
    else:
        masked = local[0] + "***" + local[-1]
    return f"{masked}@{domain}"


def _mask_phone(phone: str) -> str:
    if not phone or len(phone) < 4:
        return phone or ""
    return "****" + phone[-4:]


class OnboardingController:
    @staticmethod
    async def generate_link(student_id: str, base_url: str) -> dict:
        """Generate one-time onboarding token and link. Admin only."""
        db = get_db()
        user = await db.users.find_one({"id": student_id, "role": "student"})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        token = secrets.token_urlsafe(32)
        expires_at = _utc_now() + timedelta(days=ONBOARDING_TOKEN_EXPIRY_DAYS)

        await db.users.update_one(
            {"id": student_id},
            {
                "$set": {
                    "onboarding_token": token,
                    "onboarding_token_expires_at": expires_at,
                    "updated_at": _utc_now(),
                }
            },
        )

        link = f"{base_url.rstrip('/')}/onboard?token={token}"
        return {
            "token": token,
            "link": link,
            "expires_at": expires_at,
            "student_email": user.get("email", ""),
            "student_phone": user.get("phone", ""),
        }

    @staticmethod
    async def validate_token(token: str) -> dict:
        """Validate onboarding token and return minimal student info for pre-fill. Public."""
        db = get_db()
        if not token or not token.strip():
            return {"valid": False, "message": "Token is required"}

        user = await db.users.find_one({"onboarding_token": token.strip()})
        if not user:
            return {"valid": False, "message": "Invalid or expired link"}
        if (user.get("role") or "").lower() != "student":
            return {"valid": False, "message": "Invalid or expired link"}

        expires_at = user.get("onboarding_token_expires_at")
        if _is_expired(expires_at):
            await db.users.update_one(
                {"id": user["id"]},
                {"$unset": {"onboarding_token": "", "onboarding_token_expires_at": ""}, "$set": {"updated_at": _utc_now()}},
            )
            return {"valid": False, "message": "This link has expired"}

        dob = user.get("date_of_birth")
        if isinstance(dob, str):
            dob = dob[:10] if dob else None

        return {
            "valid": True,
            "student": {
                "id": user["id"],
                "email": user.get("email"),
                "email_masked": _mask_email(user.get("email", "")),
                "phone_masked": _mask_phone(user.get("phone", "")),
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
                "date_of_birth": dob,
                "gender": user.get("gender"),
            },
            "expires_at": expires_at,
        }

    @staticmethod
    async def submit_onboarding(data: dict) -> dict:
        """Update user (password, profile) and create/update enrollment. Public."""
        db = get_db()
        token = (data.get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="Token is required")

        user = await db.users.find_one({"onboarding_token": token})
        if not user:
            raise HTTPException(status_code=400, detail="Invalid or expired link")
        if (user.get("role") or "").lower() != "student":
            raise HTTPException(status_code=400, detail="Invalid or expired link")

        expires_at = user.get("onboarding_token_expires_at")
        if _is_expired(expires_at):
            await db.users.update_one(
                {"id": user["id"]},
                {"$unset": {"onboarding_token": "", "onboarding_token_expires_at": ""}, "$set": {"updated_at": _utc_now()}},
            )
            raise HTTPException(status_code=400, detail="This link has expired")

        student_id = user["id"]
        password = data.get("password")
        if not password or len(password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

        # Update user: password and profile
        full_name = f"{data.get('first_name', '').strip()} {data.get('last_name', '').strip()}".strip() or user.get("full_name", "")
        update_user = {
            "password": hash_password(password),
            "first_name": (data.get("first_name") or "").strip() or user.get("first_name", ""),
            "last_name": (data.get("last_name") or "").strip() or user.get("last_name", ""),
            "full_name": full_name,
            "has_credentials": True,
            "updated_at": _utc_now(),
        }
        if data.get("date_of_birth") is not None:
            update_user["date_of_birth"] = data["date_of_birth"].isoformat() if hasattr(data["date_of_birth"], "isoformat") else str(data["date_of_birth"])
        if data.get("gender") is not None:
            update_user["gender"] = data["gender"]

        await db.users.update_one(
            {"id": student_id},
            {"$set": update_user, "$unset": {"onboarding_token": "", "onboarding_token_expires_at": ""}},
        )

        # Enrollment: branch_id, course_id, duration_id -> duration_months, joining_date -> start_date
        branch_id = (data.get("branch_id") or "").strip()
        course_id = (data.get("course_id") or "").strip()
        duration_id = (data.get("duration_id") or "").strip()
        joining_date = data.get("joining_date")
        if not branch_id or not course_id or not duration_id:
            raise HTTPException(status_code=400, detail="Branch, course, and duration are required")
        if not joining_date:
            raise HTTPException(status_code=400, detail="Joining date is required")

        if hasattr(joining_date, "isoformat"):
            start_date = datetime.combine(joining_date, datetime.min.time())
        else:
            from datetime import date as date_type
            if isinstance(joining_date, str):
                parts = joining_date.split("T")[0].split("-")
                if len(parts) >= 3:
                    start_date = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                else:
                    raise HTTPException(status_code=400, detail="Invalid joining date format")
            else:
                start_date = datetime.combine(joining_date, datetime.min.time())

        branch = await assert_branch_accepts_enrollment(db, branch_id)
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=400, detail="Course not found")
        await assert_course_available_at_branch(
            db, branch_id, course_id, require_active_branch=False
        )
        duration_doc = await db.durations.find_one({"id": duration_id})
        if not duration_doc:
            raise HTTPException(status_code=400, detail="Duration not found")

        duration_months = duration_doc.get("duration_months", 1)
        end_date = start_date + timedelta(days=duration_months * 30)
        admission_fee = 500.0
        if isinstance(branch.get("admission_fee"), (int, float)):
            admission_fee = float(branch["admission_fee"])

        if not await should_charge_admission_fee_for_checkout(db, student_id, {"beneficiary_type": "self"}):
            admission_fee = 0.0

        fee_amount = course.get("base_fee")
        if fee_amount is None:
            fee_amount = course.get("pricing", {}).get("amount", 0) if isinstance(course.get("pricing"), dict) else 0
        bp = course.get("branch_pricing") or {}
        if branch_id in bp:
            branch_fees = bp[branch_id]
            if isinstance(branch_fees, dict) and duration_id in branch_fees:
                fee_amount = branch_fees[duration_id]
            elif isinstance(branch_fees, (int, float)):
                fee_amount = float(branch_fees)
        if fee_amount is None:
            fee_amount = 0.0

        # Deactivate any existing active enrollment for this student (optional: or keep and add new)
        await db.enrollments.update_many(
            {"student_id": student_id, "is_active": True},
            {"$set": {"is_active": False, "updated_at": _utc_now()}},
        )

        enrollment = Enrollment(
            student_id=student_id,
            course_id=course_id,
            branch_id=branch_id,
            start_date=start_date,
            end_date=end_date,
            fee_amount=fee_amount,
            admission_fee=admission_fee,
            payment_status=PaymentStatus.PENDING,
            next_due_date=start_date + timedelta(days=30),
            is_active=True,
        )
        await db.enrollments.insert_one(enrollment.dict())

        # Optional: create pending payment records so "next month they can pay from application"
        payment_docs = [
            Payment(
                student_id=student_id,
                enrollment_id=enrollment.id,
                amount=fee_amount,
                payment_type=PaymentType.COURSE_FEE,
                payment_method=PaymentMethod.CASH,
                payment_status=PayStatus.PENDING,
                payment_date=_utc_now(),
                due_date=start_date + timedelta(days=30),
            ).dict(),
        ]
        if admission_fee > 0:
            payment_docs.insert(
                0,
                Payment(
                    student_id=student_id,
                    enrollment_id=enrollment.id,
                    amount=admission_fee,
                    payment_type=PaymentType.ADMISSION_FEE,
                    payment_method=PaymentMethod.CASH,
                    payment_status=PayStatus.PENDING,
                    payment_date=_utc_now(),
                    due_date=start_date + timedelta(days=7),
                ).dict(),
            )
        await db.payments.insert_many(payment_docs)

        return {"message": "Onboarding complete. You can now log in.", "user_id": student_id}
