from fastapi import APIRouter, Depends, Request, status, File, UploadFile
from controllers.auth_controller import AuthController
from models.user_models import (
    UserCreate,
    UserLogin,
    ForgotPassword,
    ResetPassword,
    StudentProfileUpdate,
    PasswordResetSendOtpBody,
    PasswordResetVerifyOtpBody,
    SwitchStudentBody,
    LinkedStudentCreate,
)
from pydantic import BaseModel, EmailStr
from utils.auth import require_role, get_current_active_user
from models.user_models import UserRole

router = APIRouter()

class CheckUserRequest(BaseModel):
    email: EmailStr


class CheckPhoneRequest(BaseModel):
    phone: str

@router.post("/check-user")
async def check_user(check_user_data: CheckUserRequest):
    """Check if a user exists with the given email address"""
    return await AuthController.check_user_exists(check_user_data.email)


@router.post("/check-phone")
async def check_phone(body: CheckPhoneRequest):
    """Check if a user exists with this phone (registration / public)."""
    return await AuthController.check_phone_exists(body.phone)

@router.post("/register")
async def register_user(user_data: UserCreate, request: Request):
    return await AuthController.register_user(user_data, request)

@router.post("/login")
async def login(user_credentials: UserLogin, request: Request):
    return await AuthController.login(user_credentials, request)

@router.post("/forgot-password")
async def forgot_password(forgot_password_data: ForgotPassword):
    return await AuthController.forgot_password(forgot_password_data)

@router.post("/reset-password")
async def reset_password(reset_password_data: ResetPassword):
    return await AuthController.reset_password(reset_password_data)


@router.post("/password-reset/send-otp")
async def password_reset_send_otp(body: PasswordResetSendOtpBody):
    """Student mobile OTP — begins password reset (SMS)."""
    return await AuthController.password_reset_send_otp(body.phone)


@router.post("/password-reset/verify-otp")
async def password_reset_verify_otp(body: PasswordResetVerifyOtpBody):
    """Verify SMS OTP; returns short-lived reset_token for POST /auth/reset-password."""
    return await AuthController.password_reset_verify_otp(body.phone, body.otp)


@router.get("/me")
async def get_current_user_info(current_user: dict = Depends(AuthController.get_current_user_info)):
    return current_user

# Student Profile Endpoints
@router.get("/profile")
async def get_student_profile(current_user: dict = Depends(require_role([UserRole.STUDENT]))):
    """Get current student's profile information"""
    return await AuthController.get_student_profile(current_user)

@router.put("/profile")
async def update_student_profile(
    profile_update: StudentProfileUpdate,
    current_user: dict = Depends(require_role([UserRole.STUDENT]))
):
    """Update current student's profile information"""
    return await AuthController.update_student_profile(profile_update, current_user)


@router.post("/profile/photo")
async def upload_student_profile_photo(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Upload profile photo (JPG/PNG, max ~2MB)."""
    return await AuthController.upload_student_profile_photo(file, current_user)


@router.get("/profiles")
async def list_linked_profiles(current_user: dict = Depends(require_role([UserRole.STUDENT]))):
    """Linked student profiles on the current family account."""
    return await AuthController.list_linked_profiles(current_user)


@router.get("/linked-students")
async def list_linked_students(current_user: dict = Depends(require_role([UserRole.STUDENT]))):
    """Alias of GET /auth/profiles."""
    return await AuthController.list_linked_profiles(current_user)


@router.post("/switch-student")
async def switch_student(
    body: SwitchStudentBody,
    request: Request,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Mint a JWT whose `sub` is another student on the same account."""
    return await AuthController.switch_student(body, current_user, request)


@router.post("/linked-students")
async def create_linked_student(
    body: LinkedStudentCreate,
    request: Request,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Add another student profile under the logged-in family account."""
    return await AuthController.create_linked_student(body, current_user, request)


@router.post("/students")
async def create_linked_student_alias(
    body: LinkedStudentCreate,
    request: Request,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Alias of POST /auth/linked-students."""
    return await AuthController.create_linked_student(body, current_user, request)
