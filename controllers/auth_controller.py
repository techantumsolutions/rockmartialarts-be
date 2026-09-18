from fastapi import HTTPException, Depends, status, Request, UploadFile
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import secrets
import jwt
import os
import logging
import random
import re
import string
import uuid

from passlib.context import CryptContext

from models.user_models import (
    UserCreate,
    UserLogin,
    ForgotPassword,
    ResetPassword,
    UserUpdate,
    BaseUser,
    UserRole,
    StudentProfileUpdate,
    StudentProfileResponse,
    StudentAddress,
    StudentEmergencyContact,
    StudentMedicalInfo,
    SwitchStudentBody,
    LinkedStudentCreate,
)
from utils.auth import hash_password, verify_password, create_access_token, get_current_active_user, SECRET_KEY, ALGORITHM
from utils.database import get_db
from utils.helpers import serialize_doc, log_activity, send_sms
from utils.email_service import send_password_reset_email, send_password_reset_email_webhook
from utils.enrollment_dates import resolve_enrollment_end_date
from utils.indian_phone import canonical_indian_phone, otp_phone_variants
from utils.subscription_dates import is_subscription_period_over
from utils.reg_checkout_sms import (
    public_sms_failure_hint,
    send_registration_sms,
    sms_provider_expects_delivery,
)
from utils.family_accounts import (
    ensure_account_for_student,
    get_account_by_email,
    get_account_by_phone,
    list_profiles,
    pick_login_student,
    student_login_user_payload,
    sync_account_password,
)

logger = logging.getLogger(__name__)

COL_PASSWORD_RESET_OTP = "auth_password_reset_otp"
PASSWORD_RESET_OTP_TTL_MIN = max(1, min(15, int(os.getenv("PASSWORD_RESET_OTP_TTL_MIN", "5"))))
PASSWORD_RESET_RESEND_SEC = int(os.getenv("PASSWORD_RESET_RESEND_COOLDOWN_SEC", "30"))
PASSWORD_RESET_MAX_OTP_ATTEMPTS = max(3, min(10, int(os.getenv("PASSWORD_RESET_OTP_MAX_ATTEMPTS", "5"))))
_pwd_otp_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _normalize_payment_state(payment_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Normalize payment state from mixed legacy fields (`payment_status` or raw `status`)."""
    if not payment_doc:
        return None
    ps = str(payment_doc.get("payment_status") or "").strip().lower()
    if ps:
        return ps
    raw = str(payment_doc.get("status") or "").strip().lower()
    if raw in {"success", "captured", "paid", "completed"}:
        return "paid"
    if raw in {"authorized", "processing"}:
        return "processing"
    if raw in {"initiated", "created", "pending"}:
        return "pending"
    if raw in {"failed", "error"}:
        return "failed"
    if raw in {"cancelled", "canceled", "refunded"}:
        return "cancelled"
    return None


def _derive_enrollment_status_row(enrollment: Dict[str, Any], payment_status: str) -> str:
    """Consistent UI status for student profile/dashboard consumers."""
    explicit = str(enrollment.get("status") or "").strip().lower()
    if explicit in {"cancelled", "canceled"}:
        return "cancelled"
    if explicit == "paused":
        return "paused"
    if payment_status in {"cancelled", "canceled"}:
        return "cancelled"
    if payment_status == "paused":
        return "paused"
    if is_subscription_period_over(enrollment.get("end_date")):
        return "expired"
    if not bool(enrollment.get("is_active", True)):
        return "inactive"
    return "active"


def _student_phone_match_variants(canonical: str) -> List[str]:
    """All phone string variants that may appear on student `users.phone` documents."""
    national = canonical[3:] if canonical.startswith("+91") and len(canonical) == 13 else ""
    if len(national) != 10:
        c2 = canonical_indian_phone(canonical)
        national = c2[3:] if c2 else ""
    if len(national) != 10:
        return [canonical]
    ordered = [
        canonical,
        national,
        f"91{national}",
        f"+91{national}",
        f"+91-{national}",
        f"+91 {national}",
        f"0{national}",
        f"91-{national}",
    ]
    seen: Dict[str, None] = {}
    for x in ordered:
        if x:
            seen.setdefault(x, None)
    return list(seen.keys())


async def _find_student_user_by_canonical_phone(db, canonical: str) -> Optional[Dict[str, Any]]:
    """Resolve the login account by phone, then its default student. Avoid picking a random child."""
    variants = _student_phone_match_variants(canonical)
    acc = await get_account_by_phone(db, canonical)
    if acc:
        student = await pick_login_student(db, acc)
        if student:
            return student
    return await db.users.find_one({"role": UserRole.STUDENT.value, "phone": {"$in": variants}})


def _pw_reset_otp_dlt_template_id() -> Optional[str]:
    tid = os.getenv("PASSWORD_RESET_DLT_OTP_TEMPLATE_ID", "").strip()
    if tid:
        return tid
    return (os.getenv("DLT_OTP_TEMPLATE_ID", "").strip() or os.getenv("SMS_OTP_TEMPLATE_ID", "").strip() or None)


def _pw_reset_otp_sms_body(otp: str) -> str:
    body = os.getenv("PASSWORD_RESET_OTP_MESSAGE", "").strip()
    if not body:
        body = os.getenv("DLT_OTP_MESSAGE", "").strip()
    if body:
        if "%s" in body:
            try:
                return body % otp
            except Exception:
                logger.exception("PASSWORD_RESET_OTP_MESSAGE / DLT_OTP_MESSAGE %%s formatting failed")
        return body.replace("{otp}", otp).replace("{OTP}", otp)
    return (
        "ROCK MARTIAL ARTS ACADEMY: Your password reset OTP is "
        f"{otp}. Valid for {PASSWORD_RESET_OTP_TTL_MIN} minutes. Do not share this code."
    )


class AuthController:
    @staticmethod
    async def register_user(user_data: UserCreate, request: Request):
        """Register a new student (public endpoint)"""
        db = get_db()
        
        # Check if user exists
        existing_user = await db.users.find_one({
            "$or": [{"email": user_data.email}, {"phone": user_data.phone}]
        })
        if existing_user:
            raise HTTPException(status_code=400, detail="User with this email or phone already exists")
        
        # Generate password if not provided
        if not user_data.password:
            user_data.password = secrets.token_urlsafe(8)
        
        # Hash password
        hashed_password = hash_password(user_data.password)
        
        # Generate full name from first and last name
        full_name = f"{user_data.first_name} {user_data.last_name}".strip()
        
        # Create user dictionary with nested structure exactly as requested
        user_dict = {
            "id": str(uuid.uuid4()),
            "email": user_data.email,
            "phone": user_data.phone,
            "first_name": user_data.first_name,
            "last_name": user_data.last_name,
            "full_name": full_name,
            "role": user_data.role.value,  # Convert enum to string
            "is_active": True,
            # Self-service registration: student already has login credentials
            "has_credentials": True,
            "date_of_birth": user_data.date_of_birth.isoformat() if user_data.date_of_birth else None,
            "gender": user_data.gender,
            "password": hashed_password,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        # Set branch_id for staff members
        if user_data.branch_id:
            user_dict["branch_id"] = user_data.branch_id

        # Biometric / ESSL mapping: omit keys when unset — unique sparse indexes still index BSON null.
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

        if getattr(user_data, "address", None) is not None:
            user_dict["address"] = user_data.address
        if getattr(user_data, "emergency_contact", None) is not None:
            user_dict["emergency_contact"] = user_data.emergency_contact
        if user_data.role == UserRole.STUDENT and user_data.student_level:
            user_dict["student_level"] = user_data.student_level
        if user_data.role == UserRole.STUDENT:
            user_dict["relationship"] = user_data.relationship or "self"
            if user_data.account_id:
                user_dict["account_id"] = user_data.account_id

        if user_data.role == UserRole.STUDENT and user_data.course and user_data.branch:
            from utils.branch_courses import assert_course_available_at_branch
            await assert_course_available_at_branch(
                db, user_data.branch.branch_id, user_data.course.course_id
            )

        result = await db.users.insert_one(user_dict)

        if user_data.role == UserRole.STUDENT:
            try:
                await ensure_account_for_student(db, user_dict)
            except Exception:
                logger.exception("Failed to create family account for new student")

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
                # Log error but don't fail the registration if enrollment creation fails
                print(f"❌ Error creating enrollment record: {e}")
                pass
        
        # Send credentials via SMS (mock)
        course_info = "No course selected"
        branch_info = "No branch assigned"

        if enrollment_id and user_data.course and user_data.branch:
            course_info = f"Course: {user_data.course.course_id} ({user_data.course.duration})"
            branch_info = f"Branch: {user_data.branch.branch_id}"
        elif user_data.branch_id:
            branch_info = f"Branch: {user_data.branch_id}"

        sms_message = (
            f"Welcome {user_dict['full_name']}!\n"
            f"Your account has been created.\n"
            f"Email: {user_dict['email']}\n"
            f"Password: {user_data.password}\n"
            f"Date of Birth: {user_dict['date_of_birth']}\n"
            f"Gender: {user_dict['gender']}\n"
            f"{course_info}\n"
            f"{branch_info}"
        )
        await send_sms(user_dict["phone"], sms_message)
        
        await log_activity(
            request=request,
            action="user_registration",
            user_id=user_dict["id"],
            user_name=user_dict["full_name"],
            details={"email": user_dict["email"], "role": user_dict["role"]}
        )

        response_data = {"message": "User registered successfully", "user_id": user_dict["id"]}
        if enrollment_id:
            response_data["enrollment_id"] = enrollment_id
            response_data["message"] = "User registered and enrolled successfully"

        return response_data

    @staticmethod
    async def create_user_silent(user_data: UserCreate):
        """Create user and optional enrollment without sending SMS. Used by bulk import."""
        db = get_db()
        existing_user = await db.users.find_one({
            "$or": [{"email": user_data.email}, {"phone": user_data.phone}]
        })
        if existing_user:
            raise HTTPException(status_code=400, detail=f"User with email {user_data.email} or phone {user_data.phone} already exists")
        if not user_data.password:
            user_data.password = secrets.token_urlsafe(8)
        hashed_password = hash_password(user_data.password)
        full_name = f"{user_data.first_name} {user_data.last_name}".strip()
        user_dict = {
            "id": str(uuid.uuid4()),
            "email": user_data.email,
            "phone": user_data.phone,
            "first_name": user_data.first_name,
            "last_name": user_data.last_name,
            "full_name": full_name,
            "role": user_data.role.value,
            "is_active": True,
            # Bulk / silent admin provisioning: invite onboarding until they complete the link
            "has_credentials": False,
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
        if user_data.branch_id:
            user_dict["branch_id"] = user_data.branch_id
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
            if not user_dict.get("branch_id"):
                user_dict["branch_id"] = user_data.branch.branch_id
        if getattr(user_data, "address", None) is not None:
            user_dict["address"] = user_data.address
        if getattr(user_data, "emergency_contact", None) is not None:
            user_dict["emergency_contact"] = user_data.emergency_contact
        if user_data.role == UserRole.STUDENT:
            user_dict["relationship"] = user_data.relationship or "self"
            if user_data.account_id:
                user_dict["account_id"] = user_data.account_id
        await db.users.insert_one(user_dict)
        if user_data.role == UserRole.STUDENT:
            try:
                await ensure_account_for_student(db, user_dict)
            except Exception:
                logger.exception("Failed to create family account for silent student create")
        enrollment_id = None
        if user_data.course and user_data.branch and user_data.role == UserRole.STUDENT:
            try:
                from models.enrollment_models import Enrollment, PaymentStatus
                start_date = datetime.utcnow()
                end_date = await resolve_enrollment_end_date(
                    db, user_data.course.duration, start_date
                )
                enrollment = Enrollment(
                    student_id=user_dict["id"],
                    course_id=user_data.course.course_id,
                    branch_id=user_data.branch.branch_id,
                    start_date=start_date,
                    end_date=end_date,
                    fee_amount=0.0,
                    admission_fee=0.0,
                    payment_status=PaymentStatus.PENDING,
                    enrollment_date=start_date,
                    is_active=True
                )
                enrollment_doc = enrollment.dict()
                enrollment_doc["duration_id"] = user_data.course.duration
                await db.enrollments.insert_one(enrollment_doc)
                enrollment_id = enrollment.id
            except Exception as e:
                print(f"❌ Error creating enrollment record: {e}")
        return {"user_id": user_dict["id"], "enrollment_id": enrollment_id}

    @staticmethod
    async def _reject_login(request: Request, email: str, reason: str, user: Optional[dict] = None, status_code: int = 401, detail: str = "Incorrect email or password"):
        try:
            await log_activity(
                request=request,
                action="login_attempt",
                status="failure",
                user_id=(user or {}).get("id"),
                user_name=(user or {}).get("full_name", ""),
                details={"email": email, "reason": reason},
            )
        except Exception:
            pass
        logger.warning("POST /api/auth/login: rejected (%s)", reason)
        raise HTTPException(status_code=status_code, detail=detail)

    @staticmethod
    async def login(user_credentials: UserLogin, request: Request):
        """User login. Students authenticate against `accounts` when present so one login can hold multiple profiles."""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=503, detail="Database not initialized")

            logger.info("POST /api/auth/login: attempt")
            email = (user_credentials.email or "").strip().lower()
            acc = await get_account_by_email(db, email)
            user = None
            profiles: List[Dict[str, Any]] = []
            account_id = None

            if acc:
                stored_hash = acc.get("password")
                if not stored_hash or not verify_password(user_credentials.password, stored_hash):
                    await AuthController._reject_login(request, user_credentials.email, "Incorrect email or password")
                if acc.get("is_active") is False:
                    await AuthController._reject_login(
                        request, user_credentials.email, "Account is deactivated",
                        status_code=400, detail="Account is deactivated",
                    )
                user = await pick_login_student(db, acc)
                if not user:
                    await AuthController._reject_login(request, user_credentials.email, "No active student on account")
                account_id = acc["id"]
                if not user.get("account_id"):
                    await db.users.update_one(
                        {"id": user["id"]},
                        {"$set": {"account_id": account_id, "relationship": user.get("relationship") or "self"}},
                    )
                    user["account_id"] = account_id
                profiles = await list_profiles(db, account_id)
            else:
                user = await db.users.find_one({"email": user_credentials.email})
                stored_hash = user.get("password") if user else None
                if user and not stored_hash:
                    stored_hash = user.get("password_hash")
                if not user or not stored_hash or not verify_password(user_credentials.password, stored_hash):
                    await AuthController._reject_login(request, user_credentials.email, "Incorrect email or password")
                if user.get("is_active") is False:
                    await AuthController._reject_login(
                        request, user_credentials.email, "Account is deactivated", user,
                        status_code=400, detail="Account is deactivated",
                    )
                if user.get("role") == "student":
                    acc = await ensure_account_for_student(db, user)
                    account_id = acc["id"]
                    profiles = await list_profiles(db, account_id)

            token_data = {"sub": user["id"], "role": user.get("role", "student")}
            if account_id:
                token_data["account_id"] = account_id
            access_token = create_access_token(data=token_data)

            try:
                await log_activity(
                    request=request,
                    action="login_success",
                    user_id=user["id"],
                    user_name=user.get("full_name", ""),
                    details={"email": user.get("email")},
                )
            except Exception:
                pass

            logger.info("POST /api/auth/login: success user_id=%s", user.get("id"))
            payload = {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 86400,
                "user": student_login_user_payload(user),
            }
            if user.get("role") == "student":
                payload["profiles"] = profiles
                payload["account_id"] = account_id
                payload["active_student_id"] = user["id"]
            return payload
        except HTTPException:
            raise
        except Exception as e:
            logging.exception("Login failed")
            raise HTTPException(status_code=500, detail=f"Login error: {str(e)}")

    @staticmethod
    async def forgot_password(forgot_password_data: ForgotPassword):
        """Initiate password reset process with email functionality"""
        db = get_db()
        acc = await get_account_by_email(db, forgot_password_data.email)
        if acc:
            user = await pick_login_student(db, acc)
        else:
            user = await db.users.find_one({"email": forgot_password_data.email})
        if not user:
            # Don't reveal that the user does not exist
            return {"message": "If an account with that email exists, a password reset link has been sent."}

        # Generate a short-lived token for password reset
        reset_token = create_access_token(
            data={"sub": user["id"], "scope": "password_reset"},
            expires_delta=timedelta(minutes=15)
        )

        # Send password reset email: try SMTP first, then webhook if SMTP not configured or fails
        user_name = user.get("full_name", f"{user.get('first_name', '')} {user.get('last_name', '')}").strip()
        email_sent = await send_password_reset_email(
            to_email=user["email"],
            reset_token=reset_token,
            user_name=user_name or "User",
            user_type="student"
        )
        if not email_sent:
            email_sent = await send_password_reset_email_webhook(
                to_email=user["email"],
                reset_token=reset_token,
                user_name=user_name or "User",
                user_type="student"
            )

        # Log the password reset attempt
        logging.info(f"Password reset requested for {user['email']}. Email sent: {email_sent}")

        # Also send SMS as backup (if phone number exists)
        if user.get("phone"):
            sms_message = f"Password reset requested for your account. Check your email ({user['email']}) for reset instructions. If you didn't request this, please ignore."
            await send_sms(user["phone"], sms_message)

        response = {"message": "If an account with that email exists, a password reset link has been sent."}

        # Include token in response for testing purposes
        if os.environ.get("TESTING") == "True":
            response["reset_token"] = reset_token
            response["email_sent"] = email_sent

        return response

    @staticmethod
    async def reset_password(reset_password_data: ResetPassword):
        """Reset password using a token"""
        try:
            payload = jwt.decode(
                reset_password_data.token,
                SECRET_KEY,
                algorithms=[ALGORITHM]
            )
            if payload.get("scope") != "password_reset":
                raise HTTPException(status_code=401, detail="Invalid token scope")

            user_id = payload.get("sub")
            if not user_id:
                raise HTTPException(status_code=401, detail="Invalid token")

        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        new_hashed_password = hash_password(reset_password_data.new_password)
        db = get_db()
        user = await db.users.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        await sync_account_password(db, user, new_hashed_password)

        return {"message": "Password has been reset successfully."}

    @staticmethod
    async def password_reset_send_otp(raw_phone: str) -> Dict[str, Any]:
        """Send OTP via SMS for student password reset (registered mobile only)."""
        db = get_db()
        canonical = canonical_indian_phone(raw_phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid mobile number format.")

        user = await _find_student_user_by_canonical_phone(db, canonical)
        if not user:
            raise HTTPException(status_code=404, detail="Mobile number not found")

        now = datetime.utcnow()
        variants = otp_phone_variants(canonical)
        filt: Dict[str, Any] = {"phone": {"$in": variants}}
        existing = await db[COL_PASSWORD_RESET_OTP].find_one(filt)

        if existing and existing.get("last_sent_at"):
            delta = (now - existing["last_sent_at"]).total_seconds()
            if delta < PASSWORD_RESET_RESEND_SEC:
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {int(PASSWORD_RESET_RESEND_SEC - delta)}s before resending OTP.",
                )

        otp = "".join(random.choices(string.digits, k=6))
        code_hash = _pwd_otp_ctx.hash(otp)
        expires = now + timedelta(minutes=PASSWORD_RESET_OTP_TTL_MIN)
        otp_doc = {
            "phone": canonical,
            "code_hash": code_hash,
            "expires_at": expires,
            "last_sent_at": now,
            "failed_attempts": 0,
            "user_id": user["id"],
        }
        if existing:
            await db[COL_PASSWORD_RESET_OTP].update_one(
                {"_id": existing["_id"]},
                {"$set": otp_doc},
            )
        else:
            await db[COL_PASSWORD_RESET_OTP].insert_one(otp_doc)

        msg = _pw_reset_otp_sms_body(otp)
        tid = _pw_reset_otp_dlt_template_id()
        ok, sms_err = send_registration_sms(canonical, msg, template_id=tid)
        allow_stub = os.getenv("PASSWORD_RESET_ALLOW_SMS_STUB", "").lower() in ("1", "true", "yes")
        expects_sms = sms_provider_expects_delivery()

        if expects_sms and not allow_stub and not ok:
            await db[COL_PASSWORD_RESET_OTP].delete_many(filt)
            hint = public_sms_failure_hint(sms_err)
            detail = (
                "Could not send OTP SMS. Check SMS gateway configuration (same as registration OTP)."
            )
            if hint:
                detail = f"{detail} {hint}"
            raise HTTPException(status_code=503, detail=detail)
        if expects_sms and allow_stub and not ok:
            logger.warning(
                "Password reset OTP SMS failed; PASSWORD_RESET_ALLOW_SMS_STUB=true — OTP still stored."
            )

        return {
            "message": "OTP sent to your registered mobile number",
            "expires_in_seconds": PASSWORD_RESET_OTP_TTL_MIN * 60,
        }

    @staticmethod
    async def password_reset_verify_otp(raw_phone: str, otp: str) -> Dict[str, Any]:
        """Validate OTP and return a short-lived JWT accepted by POST /auth/reset-password."""
        db = get_db()
        canonical = canonical_indian_phone(raw_phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid mobile number format.")

        variants = otp_phone_variants(canonical)
        doc = await db[COL_PASSWORD_RESET_OTP].find_one({"phone": {"$in": variants}})
        if not doc:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        if doc.get("expires_at") and datetime.utcnow() > doc["expires_at"]:
            await db[COL_PASSWORD_RESET_OTP].delete_one({"_id": doc["_id"]})
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        if not _pwd_otp_ctx.verify(otp, doc["code_hash"]):
            await db[COL_PASSWORD_RESET_OTP].update_one(
                {"_id": doc["_id"]},
                {"$inc": {"failed_attempts": 1}},
            )
            fresh = await db[COL_PASSWORD_RESET_OTP].find_one({"_id": doc["_id"]})
            attempts = int(fresh.get("failed_attempts") or 0) if fresh else PASSWORD_RESET_MAX_OTP_ATTEMPTS
            if attempts >= PASSWORD_RESET_MAX_OTP_ATTEMPTS:
                await db[COL_PASSWORD_RESET_OTP].delete_one({"_id": doc["_id"]})
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        user_id = doc.get("user_id")
        if not user_id:
            await db[COL_PASSWORD_RESET_OTP].delete_one({"_id": doc["_id"]})
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        user = await db.users.find_one({"id": user_id, "role": UserRole.STUDENT.value})
        if not user:
            await db[COL_PASSWORD_RESET_OTP].delete_one({"_id": doc["_id"]})
            raise HTTPException(status_code=404, detail="Mobile number not found")

        await db[COL_PASSWORD_RESET_OTP].delete_one({"_id": doc["_id"]})

        reset_token = create_access_token(
            data={"sub": user_id, "scope": "password_reset"},
            expires_delta=timedelta(minutes=15),
        )
        return {"reset_token": reset_token, "expires_in": 15 * 60}

    @staticmethod
    async def get_current_user_info(current_user: dict = Depends(get_current_active_user)):
        """Get current user information"""
        user_info = current_user.copy()
        user_info.pop("password", None)
        user_info["date_of_birth"] = current_user.get("date_of_birth")
        user_info["gender"] = current_user.get("gender")
        if current_user.get("role") == "student":
            try:
                db = get_db()
                acc = await ensure_account_for_student(db, current_user)
                user_info["account_id"] = acc["id"]
                user_info["relationship"] = current_user.get("relationship") or "self"
                user_info["profiles"] = await list_profiles(db, acc["id"])
                user_info["active_student_id"] = current_user.get("id")
            except Exception:
                logger.exception("Failed to attach family profiles to /auth/me")
                user_info["profiles"] = []
        return user_info

    @staticmethod
    async def update_profile(user_update: UserUpdate, current_user: dict = Depends(get_current_active_user)):
        """Update user profile"""
        update_data = {}
        for k, v in user_update.dict().items():
            if v is None:
                continue
            if k in ("biometric_id", "essl_user_id"):
                if isinstance(v, str) and not v.strip():
                    continue
                update_data[k] = v.strip() if isinstance(v, str) else v
            else:
                update_data[k] = v

        # Auto-generate full_name if first_name or last_name is being updated
        if user_update.first_name is not None or user_update.last_name is not None:
            # Get current values from current_user if not provided in update
            current_first_name = user_update.first_name if user_update.first_name is not None else current_user.get("first_name", "")
            current_last_name = user_update.last_name if user_update.last_name is not None else current_user.get("last_name", "")

            # Generate full_name from first_name and last_name
            full_name = f"{current_first_name} {current_last_name}".strip()
            update_data["full_name"] = full_name
            print(f"🔄 Auto-generated full_name: '{full_name}' from first_name: '{current_first_name}', last_name: '{current_last_name}'")

        update_data["updated_at"] = datetime.utcnow()

        # Ensure date_of_birth and gender are included in the update
        if user_update.date_of_birth:
            update_data["date_of_birth"] = user_update.date_of_birth
        if user_update.gender:
            update_data["gender"] = user_update.gender

        db = get_db()
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": update_data}
        )
        return {"message": "Profile updated successfully"}

    @staticmethod
    async def check_user_exists(email: str):
        """Check if a user exists with the given email address"""
        db = get_db()

        try:
            acc = await get_account_by_email(db, email)
            user = await db.users.find_one({"email": email})

            if acc or user:
                return {
                    "exists": True,
                    "name": (user or {}).get("full_name") or (user or {}).get("name", "User"),
                    "email": email
                }
            else:
                return {
                    "exists": False,
                    "name": "User",
                    "email": email
                }

        except Exception as e:
            logging.error(f"Error checking user existence: {e}")
            # Return False for security (don't reveal system errors)
            return {
                "exists": False,
                "name": "User",
                "email": email
            }

    @staticmethod
    async def check_phone_exists(phone: str):
        """Public registration: true if any LMS user already uses this mobile (last 10 digits)."""
        db = get_db()
        raw = (phone or "").strip()
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) < 10:
            return {"exists": False}
        norm = digits[-10:]
        variants = [
            norm,
            f"91{norm}",
            f"+91{norm}",
            f"+91-{norm}",
            f"0{norm}",
            f"91-{norm}",
            f"+91 {norm}",
        ]
        try:
            acc = await get_account_by_phone(db, norm)
            if acc:
                return {"exists": True}
            u = await db.users.find_one({"phone": {"$in": variants}})
            if u:
                return {"exists": True}
            u = await db.users.find_one({"phone": {"$regex": re.escape(norm) + r"\s*$"}})
            if u:
                return {"exists": True}
            async for doc in db.users.find(
                {"phone": {"$exists": True, "$nin": [None, ""]}},
                {"phone": 1},
            ).limit(5000):
                stored = "".join(c for c in str(doc.get("phone", "")) if c.isdigit())
                if len(stored) >= 10 and stored[-10:] == norm:
                    return {"exists": True}
            return {"exists": False}
        except Exception as e:
            logger.error("Error checking phone existence: %s", e)
            return {"exists": False}

    @staticmethod
    async def get_student_profile(current_user: dict):
        """Get current student's profile information"""
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can access this endpoint")

        db = get_db()

        # Get fresh user data from database
        user = await db.users.find_one({"id": current_user["id"]})
        if not user:
            raise HTTPException(status_code=404, detail="Student not found")

        # Get enrollment information (all enrollments so we can show expiry date even when inactive)
        enrollments_cursor = db.enrollments.find({"student_id": current_user["id"]}).sort("end_date", -1)
        enrollments = await enrollments_cursor.to_list(100)

        # Enrich enrollment data with course and branch details
        enriched_enrollments = []
        latest_end_date = None
        for enrollment in enrollments:
            # Get course details
            course = await db.courses.find_one({"id": enrollment["course_id"]})
            # Get branch details
            branch = await db.branches.find_one({"id": enrollment["branch_id"]})
            raw_payment_status = str(enrollment.get("payment_status", "pending")).lower()
            effective_payment_status = raw_payment_status
            latest_payment = await db.payments.find_one(
                {
                    "student_id": current_user["id"],
                    "enrollment_id": enrollment.get("id"),
                },
                sort=[("created_at", -1)],
            )
            normalized_latest_payment_state = _normalize_payment_state(latest_payment)
            if normalized_latest_payment_state:
                effective_payment_status = normalized_latest_payment_state
            elif raw_payment_status != "paid":
                paid_payment = await db.payments.find_one(
                    {
                        "student_id": current_user["id"],
                        "enrollment_id": enrollment.get("id"),
                        "$or": [
                            {"payment_status": "paid"},
                            {"status": {"$in": ["success", "captured", "paid", "completed"]}},
                        ],
                    },
                    {"id": 1},
                )
                if paid_payment:
                    effective_payment_status = "paid"

            if effective_payment_status != raw_payment_status:
                # Heal stale enrollment status so all screens stay consistent.
                await db.enrollments.update_one(
                    {"id": enrollment.get("id")},
                    {"$set": {"payment_status": effective_payment_status, "updated_at": datetime.utcnow()}},
                )

            duration_id = enrollment.get("duration_id")
            duration_months = None
            if duration_id:
                try:
                    dur_row = await db.durations.find_one({"id": duration_id})
                    if not dur_row:
                        dur_row = await db.durations.find_one({"code": duration_id})
                    if dur_row and dur_row.get("duration_months") is not None:
                        duration_months = int(dur_row["duration_months"])
                except (TypeError, ValueError):
                    duration_months = None

            enriched_enrollment = {
                "id": enrollment["id"],
                "course_id": enrollment["course_id"],
                "course_name": course.get("title", "Unknown Course") if course else "Unknown Course",
                "branch_id": enrollment["branch_id"],
                "branch_name": branch.get("branch", {}).get("name", "Unknown Branch") if branch else "Unknown Branch",
                "enrollment_date": enrollment.get("enrollment_date"),
                "start_date": enrollment.get("start_date"),
                "end_date": enrollment.get("end_date"),
                "next_due_date": enrollment.get("next_due_date") or enrollment.get("end_date"),
                "billing_period_start": enrollment.get("billing_period_start"),
                "billing_period_end": enrollment.get("billing_period_end"),
                "billing_cycle_id": enrollment.get("billing_cycle_id"),
                "payment_status": effective_payment_status,
                "status": _derive_enrollment_status_row(enrollment, effective_payment_status),
                "is_active": enrollment.get("is_active", True),
                "duration_id": duration_id,
                "duration_months": duration_months,
                "fee_amount": enrollment.get("fee_amount"),
                "admission_fee": enrollment.get("admission_fee"),
            }
            enriched_enrollments.append(enriched_enrollment)

            # Subscription banner: max end_date among paid rows only (skip cancelled/pending far-future noise)
            ep = str(effective_payment_status or "").lower()
            end_date = enrollment.get("end_date")
            if ep == "paid" and end_date:
                try:
                    if isinstance(end_date, str):
                        parsed = datetime.fromisoformat(end_date.replace("Z", "+00:00")) if "T" in end_date else datetime.fromisoformat(end_date)
                    else:
                        parsed = end_date
                    if latest_end_date is None or parsed > latest_end_date:
                        latest_end_date = parsed
                except Exception:
                    pass

        # Prepare profile response
        profile_img = user.get("profile_image") or user.get("profile_photo") or user.get("photo")
        profile_data = {
            "id": user["id"],
            "email": user["email"],
            "phone": user["phone"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "full_name": user["full_name"],
            "date_of_birth": user.get("date_of_birth"),
            "gender": user.get("gender"),
            "current_belt": user.get("current_belt") or user.get("belt_rank"),
            "profile_image": profile_img,
            "address": user.get("address"),
            "emergency_contact": user.get("emergency_contact"),
            "medical_info": user.get("medical_info"),
            "is_active": user["is_active"],
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
            "enrollments": enriched_enrollments,
            "subscription_expiry": latest_end_date.isoformat() if latest_end_date else None
        }

        return {
            "message": "Profile retrieved successfully",
            "profile": serialize_doc(profile_data)
        }

    @staticmethod
    async def update_student_profile(profile_update: StudentProfileUpdate, current_user: dict):
        """Update current student's profile information"""
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can access this endpoint")

        db = get_db()

        # Prepare update data
        update_data = {}

        # Handle basic fields
        if profile_update.first_name is not None:
            update_data["first_name"] = profile_update.first_name
        if profile_update.last_name is not None:
            update_data["last_name"] = profile_update.last_name
        if profile_update.phone is not None:
            update_data["phone"] = profile_update.phone
        if profile_update.date_of_birth is not None:
            update_data["date_of_birth"] = profile_update.date_of_birth.isoformat()
        if profile_update.gender is not None:
            update_data["gender"] = profile_update.gender

        # Handle nested objects
        if profile_update.address is not None:
            update_data["address"] = profile_update.address.dict()
        if profile_update.emergency_contact is not None:
            update_data["emergency_contact"] = profile_update.emergency_contact.dict()
        if profile_update.medical_info is not None:
            update_data["medical_info"] = profile_update.medical_info.dict()

        # Update full_name if first_name or last_name changed
        if profile_update.first_name is not None or profile_update.last_name is not None:
            # Get current user data to construct full name
            current_first = profile_update.first_name if profile_update.first_name is not None else current_user.get("first_name", "")
            current_last = profile_update.last_name if profile_update.last_name is not None else current_user.get("last_name", "")
            update_data["full_name"] = f"{current_first} {current_last}".strip()

        if not update_data:
            raise HTTPException(status_code=400, detail="No update data provided")

        # Add updated timestamp
        update_data["updated_at"] = datetime.utcnow()

        # Update user in database
        result = await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Student not found")

        # Log the profile update
        await log_activity(
            request=None,  # No request object available in this context
            action="student_profile_update",
            user_id=current_user["id"],
            user_name=current_user.get("full_name", "Student"),
            details={"updated_fields": list(update_data.keys())}
        )

        return {"message": "Profile updated successfully"}

    MAX_PROFILE_PHOTO_BYTES = 2 * 1024 * 1024  # ~2MB
    PROFILE_PHOTO_TYPES = {"image/jpeg", "image/png"}

    @staticmethod
    async def upload_student_profile_photo(file: UploadFile, current_user: dict):
        """Save student profile photo (JPEG/PNG, max ~2MB); returns public path under /uploads/images/."""
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can upload a profile photo")

        content_type = (file.content_type or "").split(";")[0].strip().lower()
        if content_type not in AuthController.PROFILE_PHOTO_TYPES:
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Use JPG or PNG.",
            )

        data = await file.read()
        if len(data) > AuthController.MAX_PROFILE_PHOTO_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size is {AuthController.MAX_PROFILE_PHOTO_BYTES // (1024 * 1024)} MB.",
            )

        from controllers.upload_controller import UPLOAD_ROOT, _safe_filename

        dest_dir = UPLOAD_ROOT / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = file.filename or "photo.jpg"
        safe_name = _safe_filename(filename)
        ext = safe_name.lower().rsplit(".", 1)[-1] if "." in safe_name else ""
        if ext not in ("jpg", "jpeg", "png"):
            safe_name = f"{safe_name.rsplit('.', 1)[0] if '.' in safe_name else safe_name}.jpg"

        dest_path = dest_dir / safe_name
        dest_path.write_bytes(data)
        file_url = f"/uploads/images/{safe_name}"

        db = get_db()
        now = datetime.utcnow()
        result = await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"profile_image": file_url, "updated_at": now}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Student not found")

        try:
            await log_activity(
                request=None,
                action="student_profile_photo_update",
                user_id=current_user["id"],
                user_name=current_user.get("full_name", "Student"),
                details={"profile_image": file_url},
            )
        except Exception:
            pass

        return {
            "message": "Profile photo updated successfully",
            "profile_image": file_url,
        }

    @staticmethod
    async def create_student_under_account(
        acc: dict,
        *,
        first_name: str,
        last_name: str,
        date_of_birth=None,
        gender: Optional[str] = None,
        relationship: str = "child",
        course=None,
        branch=None,
        send_welcome_sms: bool = False,
    ) -> Dict[str, Any]:
        """Insert a student user that shares login credentials with an existing account."""
        db = get_db()
        full_name = f"{first_name} {last_name}".strip()
        dob_val = None
        if date_of_birth is not None:
            dob_val = date_of_birth.isoformat() if hasattr(date_of_birth, "isoformat") else str(date_of_birth)
        user_dict = {
            "id": str(uuid.uuid4()),
            "email": acc.get("email") or "",
            "phone": acc.get("phone") or "",
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "role": "student",
            "is_active": True,
            "has_credentials": True,
            "date_of_birth": dob_val,
            "gender": gender,
            "password": acc.get("password"),
            "account_id": acc["id"],
            "relationship": relationship or "child",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        if course:
            user_dict["course"] = {
                "category_id": getattr(course, "category_id", None) or (course.get("category_id") if isinstance(course, dict) else None),
                "course_id": getattr(course, "course_id", None) or (course.get("course_id") if isinstance(course, dict) else None),
                "duration": getattr(course, "duration", None) or (course.get("duration") if isinstance(course, dict) else None),
            }
        if branch:
            branch_id = getattr(branch, "branch_id", None) or (branch.get("branch_id") if isinstance(branch, dict) else None)
            location_id = getattr(branch, "location_id", None) or (branch.get("location_id") if isinstance(branch, dict) else None)
            user_dict["branch"] = {"location_id": location_id or branch_id, "branch_id": branch_id}
            user_dict["branch_id"] = branch_id
            course_id = user_dict.get("course", {}).get("course_id") if user_dict.get("course") else None
            if course_id and branch_id:
                from utils.branch_courses import assert_course_available_at_branch
                await assert_course_available_at_branch(db, branch_id, course_id)

        await db.users.insert_one(user_dict)

        enrollment_id = None
        if user_dict.get("course") and user_dict.get("branch"):
            try:
                from models.enrollment_models import Enrollment, PaymentStatus
                start_date = datetime.utcnow()
                end_date = await resolve_enrollment_end_date(
                    db, user_dict["course"]["duration"], start_date
                )
                enrollment = Enrollment(
                    student_id=user_dict["id"],
                    course_id=user_dict["course"]["course_id"],
                    branch_id=user_dict["branch"]["branch_id"],
                    start_date=start_date,
                    end_date=end_date,
                    fee_amount=0.0,
                    admission_fee=0.0,
                    payment_status=PaymentStatus.PENDING,
                    enrollment_date=start_date,
                    is_active=True,
                )
                enrollment_doc = enrollment.dict()
                enrollment_doc["duration_id"] = user_dict["course"]["duration"]
                await db.enrollments.insert_one(enrollment_doc)
                enrollment_id = enrollment.id
            except Exception:
                logger.exception("Failed creating enrollment for linked student")

        if send_welcome_sms and user_dict.get("phone"):
            try:
                await send_sms(
                    user_dict["phone"],
                    f"Welcome {full_name}! A student profile was added to your Rock Martial Arts account.",
                )
            except Exception:
                logger.exception("Failed sending linked-student SMS")

        return {"user_id": user_dict["id"], "enrollment_id": enrollment_id, "user": user_dict}

    @staticmethod
    async def list_linked_profiles(current_user: dict):
        db = get_db()
        if current_user.get("role") != "student":
            return {"profiles": [], "account_id": None, "current_student_id": current_user.get("id")}
        acc = await ensure_account_for_student(db, current_user)
        return {
            "profiles": await list_profiles(db, acc["id"]),
            "account_id": acc["id"],
            "current_student_id": current_user["id"],
            "active_student_id": current_user["id"],
        }

    @staticmethod
    async def switch_student(body: SwitchStudentBody, current_user: dict, request: Request):
        db = get_db()
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can switch profiles")
        acc = await ensure_account_for_student(db, current_user)
        target = await db.users.find_one({
            "id": body.student_id,
            "account_id": acc["id"],
            "role": "student",
        })
        if not target or target.get("is_active") is False:
            raise HTTPException(status_code=403, detail="Student is not linked to this account")

        access_token = create_access_token(data={
            "sub": target["id"],
            "role": "student",
            "account_id": acc["id"],
        })
        profiles = await list_profiles(db, acc["id"])
        try:
            await log_activity(
                request=request,
                action="switch_student",
                user_id=target["id"],
                user_name=target.get("full_name", ""),
                details={"from_student_id": current_user.get("id"), "account_id": acc["id"]},
            )
        except Exception:
            pass
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 86400,
            "user": student_login_user_payload(target),
            "profiles": profiles,
            "account_id": acc["id"],
            "active_student_id": target["id"],
        }

    @staticmethod
    async def create_linked_student(body: LinkedStudentCreate, current_user: dict, request: Request):
        if current_user.get("role") != "student":
            raise HTTPException(status_code=403, detail="Only students can add linked profiles")
        db = get_db()
        acc = await ensure_account_for_student(db, current_user)
        created = await AuthController.create_student_under_account(
            acc,
            first_name=body.first_name,
            last_name=body.last_name,
            date_of_birth=body.date_of_birth,
            gender=body.gender,
            relationship=body.relationship or "child",
            course=body.course,
            branch=body.branch,
            send_welcome_sms=True,
        )
        try:
            await log_activity(
                request=request,
                action="linked_student_created",
                user_id=created["user_id"],
                user_name=created["user"].get("full_name", ""),
                details={"account_id": acc["id"], "added_by": current_user.get("id")},
            )
        except Exception:
            pass
        return {
            "student_id": created["user_id"],
            "enrollment_id": created.get("enrollment_id"),
            "profiles": await list_profiles(db, acc["id"]),
            "message": "Student profile added",
        }
