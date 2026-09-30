from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import List, Optional
from datetime import datetime, timedelta
import jwt
import os
import hashlib
import bcrypt
from dotenv import load_dotenv
from pathlib import Path

from models.user_models import UserRole
from models.payment_models import PaymentStatus
from utils.database import get_db
from utils.helpers import serialize_doc

# Load environment variables
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

# Security setup
security = HTTPBearer()
SECRET_KEY = os.environ.get('SECRET_KEY', 'student_management_secret_key_2025_secure')
ALGORITHM = "HS256"

# Debug: Print the SECRET_KEY being used (first 20 chars only for security)
print(f"🔑 auth.py using SECRET_KEY: {SECRET_KEY[:20]}...")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


# Bcrypt 5.0+ raises ValueError if password > 72 bytes. We always truncate.
BCRYPT_MAX_PASSWORD_BYTES = 72


def _to_72_bytes_max(password: str) -> bytes:
    """Bcrypt accepts max 72 bytes. Return bytes to pass to bcrypt."""
    if password is None:
        return b""
    if isinstance(password, bytes):
        return password[:BCRYPT_MAX_PASSWORD_BYTES]
    b = password.encode("utf-8")
    if len(b) <= BCRYPT_MAX_PASSWORD_BYTES:
        return b
    # Long password: use first 72 bytes of SHA-256 digest (deterministic)
    return hashlib.sha256(b).digest()[:BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (no passlib). Never pass >72 bytes."""
    payload = _to_72_bytes_max(password)[:BCRYPT_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash using bcrypt (no passlib)."""
    if plain_password is None and hashed_password is None:
        return False
    if not hashed_password:
        return False
    # Normalize to str and truncate so bcrypt 5.0+ never sees >72 bytes
    if isinstance(plain_password, bytes):
        plain_password = plain_password.decode("utf-8", errors="replace")
    payload = _to_72_bytes_max(plain_password or "")[:BCRYPT_MAX_PASSWORD_BYTES]
    # Guarantee bcrypt never receives >72 bytes (some bcrypt 5.x raise ValueError)
    if len(payload) > BCRYPT_MAX_PASSWORD_BYTES:
        payload = payload[:BCRYPT_MAX_PASSWORD_BYTES]
    try:
        if isinstance(hashed_password, bytes):
            stored = hashed_password
        else:
            stored = hashed_password.encode("utf-8")
        return bcrypt.checkpw(payload, stored)
    except ValueError:
        # bcrypt 5.0+: "password cannot be longer than 72 bytes" -> treat as wrong password
        return False
    except (TypeError, Exception):
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    db = get_db()
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    
    user = await db.users.find_one({"id": user_id})
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return serialize_doc(user)

async def get_current_active_user(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if not current_user.get("is_active", False):
        raise HTTPException(status_code=400, detail="Inactive user")

    # Restrict access for students with overdue payments
    if current_user["role"] == UserRole.STUDENT:
        overdue_payment = await db.payments.find_one({
            "student_id": current_user["id"],
            "payment_status": PaymentStatus.OVERDUE.value
        })
        if overdue_payment:
            raise HTTPException(status_code=403, detail="Access restricted due to overdue payments.")

    return current_user

async def get_current_student_any_status(current_user: dict = Depends(get_current_user)):
    """Student token regardless of is_active. Only for account-level endpoints
    (session status, profile list, switch) so a deactivated profile can still switch away."""
    if str(current_user.get("role") or "").lower() != UserRole.STUDENT.value:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return current_user

def require_role(allowed_roles: List[UserRole]):
    async def role_checker(current_user: dict = Depends(get_current_active_user)):
        if current_user["role"] not in [role.value for role in allowed_roles]:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user
    return role_checker
