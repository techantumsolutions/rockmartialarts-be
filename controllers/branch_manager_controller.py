from fastapi import HTTPException, Request
from typing import Optional, List
from datetime import datetime
import secrets
import uuid

from models.branch_manager_models import (
    BranchManagerCreate, BranchManagerUpdate, BranchManager, BranchManagerResponse,
    BranchManagerLogin, BranchManagerLoginResponse, BranchAssignment, BranchManagerResetPassword
)
from models.user_models import UserRole
from utils.auth import hash_password, verify_password, create_access_token, SECRET_KEY, ALGORITHM
from utils.database import get_db
from utils.helpers import serialize_doc, log_activity
from utils.email_service import send_password_reset_email_webhook, send_custom_email_webhook
import jwt
from datetime import timedelta

class BranchManagerController:
    @staticmethod
    async def create_branch_manager(
        manager_data: BranchManagerCreate,
        request: Request,
        current_admin: dict
    ):
        """Create new branch manager with comprehensive nested structure"""
        db = get_db()
        
        # Check if user exists
        full_phone = f"{manager_data.contact_info.country_code}{manager_data.contact_info.phone}"
        existing_manager = await db.branch_managers.find_one({
            "$or": [
                {"email": manager_data.contact_info.email}, 
                {"phone": full_phone},
                {"contact_info.phone": manager_data.contact_info.phone}
            ]
        })
        if existing_manager:
            raise HTTPException(status_code=400, detail="Branch manager with this email or phone already exists")
        
        # Generate password if not provided
        if not manager_data.contact_info.password:
            manager_data.contact_info.password = secrets.token_urlsafe(8)
        
        # Hash password
        hashed_password = hash_password(manager_data.contact_info.password)
        
        # Generate full name from first and last name
        full_name = f"{manager_data.personal_info.first_name} {manager_data.personal_info.last_name}".strip()
        
        # Parse and validate date of birth if provided
        if manager_data.personal_info.date_of_birth:
            try:
                datetime.strptime(manager_data.personal_info.date_of_birth, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        from utils.partner_tenant import assert_partner_owns_branch, stamp_tenant

        # Get branch information if branch_id is provided
        branch_assignment = None
        if manager_data.branch_id:
            await assert_partner_owns_branch(db, current_admin, manager_data.branch_id)
            branch = await db.branches.find_one({"id": manager_data.branch_id})
            if branch:
                branch_assignment = BranchAssignment(
                    branch_id=manager_data.branch_id,
                    branch_name=branch.get("branch", {}).get("name", ""),
                    branch_location=f"{branch.get('branch', {}).get('address', {}).get('city', '')}, {branch.get('branch', {}).get('address', {}).get('state', '')}"
                )
        
        # Create branch manager object
        branch_manager = BranchManager(
            personal_info=manager_data.personal_info,
            contact_info=manager_data.contact_info,
            address_info=manager_data.address_info,
            professional_info=manager_data.professional_info,
            branch_assignment=branch_assignment,
            emergency_contact=manager_data.emergency_contact,
            email=manager_data.contact_info.email,
            phone=full_phone,
            first_name=manager_data.personal_info.first_name,
            last_name=manager_data.personal_info.last_name,
            full_name=full_name,
            password_hash=hashed_password,
            notes=manager_data.notes
        )
        
        # Convert to dict for storage (exclude password from contact_info)
        manager_dict = branch_manager.dict()
        # Remove password from nested contact_info for storage
        manager_dict["contact_info"] = {
            "email": manager_data.contact_info.email,
            "country_code": manager_data.contact_info.country_code,
            "phone": manager_data.contact_info.phone
        }
        stamp_tenant(manager_dict, current_admin)
        
        # Insert into branch_managers collection
        result = await db.branch_managers.insert_one(manager_dict)
        
        # Log activity
        await log_activity(
            request=request,
            action="create_branch_manager",
            user_id=current_admin.get("id"),
            user_name=current_admin.get("full_name", "Admin"),
            details={
                "branch_manager_id": branch_manager.id,
                "branch_manager_name": full_name,
                "branch_manager_email": manager_data.contact_info.email,
                "branch_id": manager_data.branch_id
            }
        )
        
        return {
            "message": "Branch manager created successfully",
            "branch_manager": {
                "id": branch_manager.id,
                "full_name": full_name,
                "email": manager_data.contact_info.email,
                "branch_assignment": branch_assignment.dict() if branch_assignment else None
            }
        }
    
    @staticmethod
    async def get_branch_managers(
        skip: int = 0,
        limit: int = 50,
        active_only: bool = True,
        current_user: dict = None,
        all_tenants: bool = False,
    ):
        """Get list of branch managers with pagination"""
        db = get_db()
        
        from utils.partner_tenant import tenant_or_rock_owned_filter, is_partner_super_admin

        # Build query filter
        query_filter = {}
        if active_only:
            query_filter["is_active"] = True

        if all_tenants and not is_partner_super_admin(current_user):
            pass
        else:
            query_filter = tenant_or_rock_owned_filter(query_filter, current_user)
        
        # Get total count
        total_count = await db.branch_managers.count_documents(query_filter)
        
        # Get branch managers with pagination
        cursor = db.branch_managers.find(query_filter).skip(skip).limit(limit).sort("created_at", -1)
        managers = await cursor.to_list(length=limit)
        
        # Serialize and clean up response
        serialized_managers = []
        for manager in managers:
            manager_data = serialize_doc(manager)
            # Remove sensitive information
            if "password_hash" in manager_data:
                del manager_data["password_hash"]
            if "contact_info" in manager_data and "password" in manager_data["contact_info"]:
                del manager_data["contact_info"]["password"]
            serialized_managers.append(manager_data)
        
        return {
            "branch_managers": serialized_managers,
            "total_count": total_count,
            "skip": skip,
            "limit": limit
        }
    
    @staticmethod
    async def get_branch_manager(
        manager_id: str,
        current_user: dict = None
    ):
        """Get specific branch manager by ID"""
        db = get_db()
        
        manager = await db.branch_managers.find_one({"id": manager_id})
        if not manager:
            raise HTTPException(status_code=404, detail="Branch manager not found")

        from utils.partner_tenant import assert_tenant_doc_access

        assert_tenant_doc_access(manager, current_user, entity="branch manager")
        
        # Serialize and clean up response
        manager_data = serialize_doc(manager)
        # Remove sensitive information
        if "password_hash" in manager_data:
            del manager_data["password_hash"]
        if "contact_info" in manager_data and "password" in manager_data["contact_info"]:
            del manager_data["contact_info"]["password"]
        
        return manager_data

    @staticmethod
    async def update_branch_manager(
        manager_id: str,
        manager_data: BranchManagerUpdate,
        request: Request,
        current_admin: dict
    ):
        """Update existing branch manager"""
        db = get_db()

        # Check if manager exists
        existing_manager = await db.branch_managers.find_one({"id": manager_id})
        if not existing_manager:
            raise HTTPException(status_code=404, detail="Branch manager not found")

        from utils.partner_tenant import assert_tenant_doc_access

        assert_tenant_doc_access(existing_manager, current_admin, entity="branch manager")

        # Prepare update data
        update_data = {"updated_at": datetime.utcnow()}

        # Update personal info
        if manager_data.personal_info:
            update_data["personal_info"] = manager_data.personal_info.dict()
            # Update computed fields
            update_data["first_name"] = manager_data.personal_info.first_name
            update_data["last_name"] = manager_data.personal_info.last_name
            update_data["full_name"] = f"{manager_data.personal_info.first_name} {manager_data.personal_info.last_name}".strip()

        # Update contact info
        if manager_data.contact_info:
            # Check for email conflicts
            if manager_data.contact_info.email != existing_manager.get("email"):
                email_conflict = await db.branch_managers.find_one({
                    "email": manager_data.contact_info.email,
                    "id": {"$ne": manager_id}
                })
                if email_conflict:
                    raise HTTPException(status_code=400, detail="Email already exists for another branch manager")

            update_data["contact_info"] = {
                "email": manager_data.contact_info.email,
                "country_code": manager_data.contact_info.country_code,
                "phone": manager_data.contact_info.phone
            }
            update_data["email"] = manager_data.contact_info.email
            update_data["phone"] = f"{manager_data.contact_info.country_code}{manager_data.contact_info.phone}"

        # Update password if provided
        if manager_data.password:
            update_data["password_hash"] = hash_password(manager_data.password)

        # Update address info
        if manager_data.address_info:
            update_data["address_info"] = manager_data.address_info.dict()

        # Update professional info
        if manager_data.professional_info:
            update_data["professional_info"] = manager_data.professional_info.dict()

        # Update branch assignment
        if manager_data.branch_assignment:
            update_data["branch_assignment"] = manager_data.branch_assignment.dict()

        # Update emergency contact
        if manager_data.emergency_contact:
            update_data["emergency_contact"] = manager_data.emergency_contact.dict()

        # Update other fields
        if manager_data.is_active is not None:
            update_data["is_active"] = manager_data.is_active

        if manager_data.notes is not None:
            update_data["notes"] = manager_data.notes

        # Perform update
        result = await db.branch_managers.update_one(
            {"id": manager_id},
            {"$set": update_data}
        )

        if result.modified_count == 0:
            raise HTTPException(status_code=400, detail="No changes were made")

        # Log activity
        await log_activity(
            request=request,
            action="update_branch_manager",
            user_id=current_admin.get("id"),
            user_name=current_admin.get("full_name", "Admin"),
            details={
                "branch_manager_id": manager_id,
                "updated_fields": list(update_data.keys())
            }
        )

        # Return success message without fetching updated data to avoid ObjectId issues
        return {
            "message": "Branch manager updated successfully",
            "branch_manager": {
                "id": manager_id,
                "message": "Profile updated successfully"
            }
        }

    @staticmethod
    async def delete_branch_manager(
        manager_id: str,
        request: Request,
        current_admin: dict
    ):
        """Delete branch manager with proper relationship handling"""
        db = get_db()

        # Check if manager exists
        manager = await db.branch_managers.find_one({"id": manager_id})
        if not manager:
            raise HTTPException(status_code=404, detail="Branch manager not found")

        from utils.partner_tenant import assert_tenant_doc_access

        assert_tenant_doc_access(manager, current_admin, entity="branch manager")

        # Check if manager is assigned to any branches
        assigned_branches = await db.branches.find({"manager_id": manager_id}).to_list(length=100)

        if assigned_branches:
            branch_names = [branch.get("branch", {}).get("name", "Unknown") for branch in assigned_branches]
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete branch manager. They are currently assigned to {len(assigned_branches)} branch(es): {', '.join(branch_names)}. Please reassign these branches to another manager first."
            )

        # Delete the manager
        result = await db.branch_managers.delete_one({"id": manager_id})

        if result.deleted_count == 0:
            raise HTTPException(status_code=400, detail="Failed to delete branch manager")

        # Log activity
        await log_activity(
            request=request,
            action="delete_branch_manager",
            user_id=current_admin.get("id"),
            user_name=current_admin.get("full_name", "Admin"),
            details={
                "branch_manager_id": manager_id,
                "branch_manager_name": manager.get("full_name", ""),
                "branch_manager_email": manager.get("email", "")
            }
        )

        return {"message": "Branch manager deleted successfully"}

    @staticmethod
    async def send_credentials_email(
        manager_id: str,
        request: Request,
        current_admin: dict
    ):
        """Send login credentials to branch manager via email with secure password reset token"""
        db = get_db()

        # Get branch manager details
        manager = await db.branch_managers.find_one({"id": manager_id})
        if not manager:
            raise HTTPException(status_code=404, detail="Branch manager not found")

        # Get manager email
        manager_email = manager.get("email") or manager.get("contact_info", {}).get("email")
        if not manager_email:
            raise HTTPException(status_code=400, detail="Branch manager email not found")

        # Get manager name
        manager_name = manager.get("full_name") or f"{manager.get('personal_info', {}).get('first_name', '')} {manager.get('personal_info', {}).get('last_name', '')}".strip()
        if not manager_name:
            manager_name = "Branch Manager"

        # Generate secure password reset token (same as forgot password implementation)
        reset_token = secrets.token_urlsafe(32)
        reset_token_expiry = datetime.utcnow() + timedelta(hours=24)  # 24 hour expiry

        # Store reset token in database (same as forgot password implementation)
        await db.branch_managers.update_one(
            {"id": manager_id},
            {
                "$set": {
                    "reset_token": reset_token,
                    "reset_token_expiry": reset_token_expiry,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        # Get branch information for context
        branch_info = ""
        if manager.get("branch_assignment"):
            branch_name = manager["branch_assignment"].get("branch_name", "")
            branch_location = manager["branch_assignment"].get("branch_location", "")
            if branch_name:
                branch_info = f"Branch: {branch_name}"
                if branch_location:
                    branch_info += f" ({branch_location})"

        # Determine frontend URL for password setup and login (use port 3022)
        import os
        frontend_url = (os.getenv("FRONTEND_URL") or "http://localhost:3022").strip().rstrip("/")
        if ":8003" in frontend_url:
            frontend_url = frontend_url.replace(":8003", ":3022")
        reset_link = f"{frontend_url}/branch-manager/reset-password?token={reset_token}"
        login_link = f"{frontend_url}/branch-manager/login"

        # Prepare email content (same structure as coach credentials email)
        subject = "Your Branch Manager Login Credentials - Rock Martial Arts Academy"

        # Plain text message
        plain_message = f"""
Dear {manager_name},

Your Branch Manager account has been created successfully at Rock Martial Arts Academy.

Account Details:
- Email: {manager_email}
- Role: Branch Manager
{f"- {branch_info}" if branch_info else ""}

To set up your password and access your account, please click the link below:
{reset_link}

This link will expire in 24 hours for security reasons.

Once you've set your password, you can log in at:
{login_link}

If you have any questions or need assistance, please contact our support team.

Best regards,
Rock Martial Arts Academy Team

This is an automated message. Please do not reply to this email.
        """.strip()

        # HTML message
        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Branch Manager Login Credentials</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 28px;">Welcome to Rock Martial Arts Academy</h1>
        <p style="color: #f0f0f0; margin: 10px 0 0 0; font-size: 16px;">Branch Manager Account Created</p>
    </div>

    <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; border: 1px solid #ddd;">
        <p style="font-size: 16px; margin-bottom: 20px;">Dear <strong>{manager_name}</strong>,</p>

        <p style="font-size: 16px; margin-bottom: 20px;">Your Branch Manager account has been created successfully at Rock Martial Arts Academy.</p>

        <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid #667eea; margin: 20px 0;">
            <h3 style="color: #667eea; margin-top: 0;">Account Details:</h3>
            <p style="margin: 5px 0;"><strong>Email:</strong> {manager_email}</p>
            <p style="margin: 5px 0;"><strong>Role:</strong> Branch Manager</p>
            {f"<p style='margin: 5px 0;'><strong>{branch_info}</strong></p>" if branch_info else ""}
        </div>

        <p style="font-size: 16px; margin: 20px 0;">To set up your password and access your account, please click the button below:</p>

        <div style="text-align: center; margin: 30px 0;">
            <a href="{reset_link}"
               style="background: #667eea; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                Set Up Password
            </a>
        </div>

        <p style="font-size: 14px; color: #666; margin: 20px 0;">This link will expire in 24 hours for security reasons.</p>

        <p style="font-size: 16px; margin: 20px 0;">Once you've set your password, you can log in at:</p>
        <p style="font-size: 16px; margin: 10px 0;"><a href="{login_link}" style="color: #667eea;">{login_link}</a></p>

        <p style="font-size: 16px; margin: 20px 0;">If you have any questions or need assistance, please contact our support team.</p>
    </div>

    <div style="text-align: center; margin-top: 30px; padding: 20px; color: #666; font-size: 14px;">
        <p style="margin: 0;">Best regards,<br>Rock Martial Arts Academy Team</p>
        <p style="margin: 10px 0 0 0;">This is an automated message. Please do not reply to this email.</p>
    </div>
</body>
</html>
        """.strip()

        try:
            # Send email using custom webhook service (same as coach credentials implementation)
            email_sent = await send_custom_email_webhook(
                manager_email,
                subject,
                html_message,
                plain_message
            )

            # Log the credentials email attempt (same as coach implementation)
            import logging
            logging.info(f"Branch manager credentials email requested for {manager_email}. Email sent: {email_sent}")

            # Log the activity in database
            await log_activity(
                request=request,
                action="send_branch_manager_credentials",
                user_id=current_admin.get("id"),
                user_name=current_admin.get("full_name", "Admin"),
                details={
                    "branch_manager_id": manager_id,
                    "branch_manager_name": manager_name,
                    "branch_manager_email": manager_email,
                    "email_sent": email_sent
                }
            )

            if email_sent:
                return {
                    "message": "Login credentials sent successfully to branch manager's email",
                    "email": manager_email
                }
            else:
                raise HTTPException(status_code=500, detail="Failed to send credentials email")

        except Exception as e:
            import logging
            logging.error(f"Error sending branch manager credentials email: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to send credentials email: {str(e)}")

    @staticmethod
    async def reset_password(reset_password_data: BranchManagerResetPassword):
        """Reset branch manager password using a token"""
        token = (reset_password_data.token or "").strip()
        new_password = reset_password_data.new_password
        if not token:
            raise HTTPException(status_code=400, detail="Reset token is required")
        if not new_password or len(new_password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

        db = get_db()
        manager = await db.branch_managers.find_one({"reset_token": token})
        if not manager:
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                if payload.get("sub"):
                    manager = await db.branch_managers.find_one({"id": payload["sub"]})
            except Exception:
                pass

        if not manager:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")

        expiry = manager.get("reset_token_expiry")
        if expiry and isinstance(expiry, datetime) and expiry < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Reset token has expired. Please request a new one.")

        new_hashed_password = hash_password(new_password)
        await db.branch_managers.update_one(
            {"id": manager["id"]},
            {
                "$set": {
                    "password_hash": new_hashed_password,
                    "password": new_hashed_password,
                    "updated_at": datetime.utcnow(),
                },
                "$unset": {
                    "reset_token": "",
                    "reset_token_expiry": "",
                },
            },
        )

        manager_email = manager.get("email") or manager.get("contact_info", {}).get("email")
        if manager_email:
            await db.users.update_many(
                {"email": manager_email},
                {
                    "$set": {
                        "password_hash": new_hashed_password,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

        return {"message": "Password has been set successfully. You can now log in."}

    @staticmethod
    async def login_branch_manager(login_data: BranchManagerLogin):
        """Authenticate branch manager and return JWT token"""
        try:
            db = get_db()

            # Find branch manager by email
            manager = await db.branch_managers.find_one({"email": login_data.email})
            if not manager:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid email or password"
                )

            # Verify password
            if not verify_password(login_data.password, manager["password_hash"]):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid email or password"
                )

            # Check if branch manager is active
            if not manager.get("is_active", True):
                raise HTTPException(
                    status_code=401,
                    detail="Account is inactive. Please contact administrator."
                )

            # Find all branches managed by this branch manager
            all_managed = await db.branches.find({"manager_id": manager["id"]}).to_list(length=None)
            managed_branches = [b for b in all_managed if b.get("is_active", True)]
            managed_branch_ids = [branch["id"] for branch in managed_branches]

            # Disabled partner/own branches must not allow dashboard login
            if all_managed and not managed_branch_ids:
                partner_disabled = any(
                    b.get("is_collaboration_partner") or b.get("allows_collaboration")
                    for b in all_managed
                )
                raise HTTPException(
                    status_code=401,
                    detail=(
                        "This partner account is disabled. Please contact administrator."
                        if partner_disabled
                        else "This branch account is disabled. Please contact administrator."
                    ),
                )

            print(f"Branch manager {manager['id']} manages branches: {managed_branch_ids}")

            # Create access token with managed branches
            access_token_expires = 60 * 24  # 24 hours in minutes
            access_token = create_access_token(
                data={
                    "sub": manager["id"],
                    "email": manager["email"],
                    "role": "branch_manager",
                    "branch_manager_id": manager["id"],
                    "managed_branches": managed_branch_ids
                }
            )

            # Prepare branch manager data for response (without sensitive info)
            manager_data = serialize_doc(manager)
            if "password_hash" in manager_data:
                del manager_data["password_hash"]
            if "reset_token" in manager_data:
                del manager_data["reset_token"]
            if "reset_token_expiry" in manager_data:
                del manager_data["reset_token_expiry"]

            # Add managed branches to the response
            manager_data["managed_branches"] = managed_branch_ids

            return {
                "access_token": access_token,
                "token_type": "bearer",
                "branch_manager": manager_data,
                "expires_in": access_token_expires * 60,  # Convert to seconds
                "message": "Login successful"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Internal server error during branch manager login: {str(e)}"
            )

    @staticmethod
    async def update_branch_manager_profile(manager_id: str, profile_data):
        """Update branch manager profile with simple fields"""
        try:
            print(f"DEBUG: Controller method called with manager_id: {manager_id}")
            print(f"DEBUG: Profile data: {profile_data}")

            from models.branch_manager_models import BranchManagerProfileUpdate

            db = get_db()

            # Check if manager exists
            existing_manager = await db.branch_managers.find_one({"id": manager_id})
            if not existing_manager:
                raise HTTPException(status_code=404, detail="Branch manager not found")

            print(f"DEBUG: Found existing manager: {existing_manager.get('full_name', 'Unknown')}")

            # Prepare simple update data - avoid nested updates for now
            update_data = {}

            if profile_data.full_name:
                update_data["full_name"] = profile_data.full_name

            if profile_data.email:
                # Check for email conflicts
                email_conflict = await db.branch_managers.find_one({
                    "email": profile_data.email,
                    "id": {"$ne": manager_id}
                })
                if email_conflict:
                    raise HTTPException(status_code=400, detail="Email already exists for another branch manager")

                update_data["email"] = profile_data.email

            if profile_data.phone:
                update_data["phone"] = profile_data.phone

            # Add timestamp
            update_data["updated_at"] = datetime.utcnow()

            print(f"DEBUG: Update data prepared: {update_data}")

            # Perform update
            result = await db.branch_managers.update_one(
                {"id": manager_id},
                {"$set": update_data}
            )

            print(f"DEBUG: Update result - matched: {result.matched_count}, modified: {result.modified_count}")

            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Branch manager not found")

            # Return simple success response to avoid serialization issues
            return {
                "status": "success",
                "message": "Profile updated successfully",
                "data": {
                    "id": manager_id,
                    "full_name": profile_data.full_name,
                    "email": profile_data.email,
                    "phone": profile_data.phone
                }
            }

        except Exception as e:
            print(f"DEBUG: Exception in controller: {str(e)}")
            print(f"DEBUG: Exception type: {type(e)}")
            raise
