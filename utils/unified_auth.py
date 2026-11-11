from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Annotated, List, Optional, Union, Any, Dict
from datetime import datetime, timedelta, timezone
import jwt
import os
from dotenv import load_dotenv
from pathlib import Path

from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc

# Load environment variables
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)
# Use the same SECRET_KEY as the main server
SECRET_KEY = os.environ.get('SECRET_KEY', 'student_management_secret_key_2025_secure')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Debug: Print the SECRET_KEY being used (first 20 chars only for security)
print(f"[AUTH] unified_auth.py using SECRET_KEY: {SECRET_KEY[:20]}...")


def create_access_token(
    data: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
    subject: Optional[Union[str, Any]] = None,
    additional_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a JWT access token.

    Supports both data dict (e.g. data={"sub": ...}) and subject/additional_claims.
    """
    to_encode: Dict[str, Any] = {}
    if data:
        to_encode.update(data)
    if subject is not None:
        to_encode["sub"] = str(subject)
    if additional_claims:
        to_encode.update(additional_claims)

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode["exp"] = expire
    if "iat" not in to_encode:
        to_encode["iat"] = datetime.now(timezone.utc)

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def _resolve_user_from_token(db, token: str) -> dict:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    user_id: str = payload.get("sub")
    user_role: str = payload.get("role")

    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

    # Check if it's a superadmin token (JWT may use either role string)
    if user_role in ("superadmin", "super_admin"):
        user = await db.superadmins.find_one({"id": user_id})
        if user is None:
            raise HTTPException(status_code=401, detail="Super admin not found")
        user_data = serialize_doc(user)
        user_data["role"] = "super_admin"
        # Normalize so role checks never treat missing is_active as inactive
        if user_data.get("is_active") is None:
            user_data["is_active"] = True
        return user_data

    # Check if it's a branch manager token
    if user_role == "branch_manager":
        branch_manager = await db.branch_managers.find_one({"id": user_id})
        if branch_manager is None:
            raise HTTPException(status_code=401, detail="Branch manager not found")

        manager_data = serialize_doc(branch_manager)
        manager_data["role"] = "branch_manager"

        # Always resolve from live active branches — never trust stale JWT managed_branches.
        managed_branches = await db.branches.find(
            {"manager_id": user_id, "is_active": True}
        ).to_list(length=None)
        managed_branch_ids = [branch["id"] for branch in managed_branches if branch.get("id")]

        if not managed_branch_ids:
            branch_assignment = manager_data.get("branch_assignment") or {}
            assignment_bid = branch_assignment.get("branch_id") or manager_data.get("branch_id")
            if assignment_bid:
                fallback_branch = await db.branches.find_one(
                    {"id": assignment_bid, "is_active": True}
                )
                if fallback_branch:
                    managed_branch_ids = [fallback_branch["id"]]

        manager_data["managed_branches"] = managed_branch_ids

        if branch_manager.get("is_active") is False:
            raise HTTPException(
                status_code=401,
                detail="Account is inactive. Please contact administrator.",
            )

        if not managed_branch_ids:
            raise HTTPException(
                status_code=403,
                detail=(
                    "BRANCH_INACTIVE: This branch account is disabled. "
                    "Please contact administrator."
                ),
            )

        # Stale X-Active-Branch-Id (deactivated branch) is ignored here: helpers
        # fall back to the first live active branch. FE shows Access Denied + switch.
        return manager_data

    # Check if it's a coach token
    if user_role == "coach":
        coach = await db.coaches.find_one({"id": user_id})
        if coach is None:
            raise HTTPException(status_code=401, detail="Coach not found")
        coach_data = serialize_doc(coach)
        coach_data["role"] = "coach"

        jwt_branch_id = payload.get("branch_id")
        if jwt_branch_id:
            coach_data["branch_id"] = jwt_branch_id
        elif not coach_data.get("branch_id"):
            coach_data["branch_id"] = coach.get("branch_id")

        return coach_data

    user = await db.users.find_one({"id": user_id})
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    user_data = serialize_doc(user)
    # Prefer JWT partner tenant claim when present
    if payload.get("partner_org_id"):
        user_data["partner_org_id"] = payload.get("partner_org_id")
    if payload.get("partner_slug"):
        user_data["partner_slug"] = payload.get("partner_slug")
    return user_data


async def get_current_user_or_superadmin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Unified authentication that handles regular users, superadmins, coaches, and branch managers
    """
    db = get_db()
    try:
        return await _resolve_user_from_token(db, credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


async def get_optional_current_user_or_superadmin(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(optional_security),
    ],
):
    """Same as get_current_user_or_superadmin when Authorization is sent; otherwise None (no error)."""
    if credentials is None or not (credentials.credentials or "").strip():
        return None
    db = get_db()
    try:
        return await _resolve_user_from_token(db, credentials.credentials)
    except (jwt.PyJWTError, HTTPException):
        return None

def require_role_unified(allowed_roles: List[UserRole], allow_inactive_coach: bool = False):
    """
    Role checker that works with regular users, superadmins, coaches, and branch managers
    """
    async def role_checker(current_user: dict = Depends(get_current_user_or_superadmin)):
        # Only block when explicitly inactive. Missing/None must not become 400
        # (superadmin docs often omit is_active; .get("is_active", True) still returns None if key exists).
        if current_user.get("is_active") is False:
            is_coach = current_user.get("role") == "coach"
            is_expired_sub = current_user.get("subscription_status") == "expired"
            if not (is_coach and (allow_inactive_coach or is_expired_sub)):
                raise HTTPException(status_code=400, detail="Inactive user")

        user_role = current_user["role"]

        # Convert role strings to enum values for comparison
        if user_role in ("super_admin", "superadmin"):
            if UserRole.SUPER_ADMIN not in allowed_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif user_role == "branch_manager":
            if UserRole.BRANCH_MANAGER not in allowed_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif user_role == "coach":
            if UserRole.COACH not in allowed_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif user_role == "coach_admin":
            if UserRole.COACH_ADMIN not in allowed_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif user_role == "student":
            if UserRole.STUDENT not in allowed_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif user_role == "partner_super_admin":
            if UserRole.PARTNER_SUPER_ADMIN not in allowed_roles:
                # Partner super admins may use the same APIs as Rock super admins
                # when SUPER_ADMIN is in the allowed list (tenant-scoped in controllers).
                if UserRole.SUPER_ADMIN not in allowed_roles:
                    raise HTTPException(status_code=403, detail="Insufficient permissions")
            # Block disabled / expired partner orgs
            org_id = (current_user.get("partner_org_id") or "").strip()
            if org_id:
                from utils.partner_org_access import partner_org_blocks_access

                _db = get_db()
                org = await _db.partner_organizations.find_one({"id": org_id})
                if not org or partner_org_blocks_access(org):
                    raise HTTPException(
                        status_code=403,
                        detail="This partner account is disabled or expired. Please contact administrator.",
                    )
        else:
            # Fallback for any other roles
            if user_role not in [role.value for role in allowed_roles]:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

        return current_user
    return role_checker
