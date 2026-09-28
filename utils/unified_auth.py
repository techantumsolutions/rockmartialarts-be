from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Annotated, List, Optional
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

# Debug: Print the SECRET_KEY being used (first 20 chars only for security)
print(f"🔑 unified_auth.py using SECRET_KEY: {SECRET_KEY[:20]}...")


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

        jwt_managed_branches = payload.get("managed_branches", [])
        if jwt_managed_branches:
            manager_data["managed_branches"] = jwt_managed_branches
            print(f"Using managed branches from JWT token: {jwt_managed_branches}")
        else:
            managed_branches = await db.branches.find({"manager_id": user_id, "is_active": True}).to_list(length=None)
            managed_branch_ids = [branch["id"] for branch in managed_branches]

            manager_data["managed_branches"] = managed_branch_ids
            print(f"Using managed branches from database: {managed_branch_ids}")

            if not managed_branch_ids:
                branch_assignment = manager_data.get("branch_assignment")
                if branch_assignment and branch_assignment.get("branch_id"):
                    fallback_branch = await db.branches.find_one({"id": branch_assignment["branch_id"], "is_active": True})
                    if fallback_branch:
                        manager_data["managed_branches"] = [fallback_branch["id"]]
                        print(f"Using managed branches from branch assignment: {[fallback_branch['id']]}")

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

    return serialize_doc(user)


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

def require_role_unified(allowed_roles: List[UserRole]):
    """
    Role checker that works with regular users, superadmins, coaches, and branch managers
    """
    async def role_checker(current_user: dict = Depends(get_current_user_or_superadmin)):
        # Only block when explicitly inactive. Missing/None must not become 400
        # (superadmin docs often omit is_active; .get("is_active", True) still returns None if key exists).
        if current_user.get("is_active") is False:
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
        else:
            # Fallback for any other roles
            if user_role not in [role.value for role in allowed_roles]:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

        return current_user
    return role_checker
