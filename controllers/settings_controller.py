from fastapi import HTTPException, status
from datetime import datetime
from typing import Optional, Dict, Any
from bson import ObjectId

from models.settings_models import (
    SystemSettings, SystemSettingsCreate, SystemSettingsUpdate, SystemSettingsResponse,
    SystemSettingsFlatCreate, SystemSettingsFlatResponse,
    SystemConfiguration, EmailConfiguration, NotificationSettings, SecuritySettings, BackupSettings
)
from utils.database import get_db
from utils.database import serialize_doc

class SettingsController:
    @staticmethod
    async def get_settings(current_user: dict) -> SystemSettingsFlatResponse:
        """Get current system settings"""
        try:
            db = get_db()
            settings_collection = db.system_settings
            
            # Get the settings document (there should only be one)
            settings_doc = await settings_collection.find_one({})
            
            if not settings_doc:
                # Create default settings if none exist
                default_settings = SettingsController._get_default_settings()
                settings_doc = await SettingsController._create_default_settings()
            
            # Convert to flat response format
            flat_response = SettingsController._convert_to_flat_response(settings_doc)
            return flat_response
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve system settings: {str(e)}"
            )
    
    @staticmethod
    async def update_settings(settings_data: SystemSettingsFlatCreate, current_user: dict) -> SystemSettingsFlatResponse:
        """Update system settings"""
        try:
            db = get_db()
            settings_collection = db.system_settings
            
            # Convert flat data to structured format
            structured_settings = SettingsController._convert_flat_to_structured(settings_data)
            
            # Update timestamp
            structured_settings["updated_at"] = datetime.utcnow()
            
            # Check if settings exist
            existing_settings = await settings_collection.find_one({})
            
            if existing_settings:
                # Update existing settings
                result = await settings_collection.update_one(
                    {"_id": existing_settings["_id"]},
                    {"$set": structured_settings}
                )
                
                if result.modified_count == 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to update settings"
                    )
                
                # Get updated document
                updated_doc = await settings_collection.find_one({"_id": existing_settings["_id"]})
            else:
                # Create new settings
                structured_settings["created_at"] = datetime.utcnow()
                result = await settings_collection.insert_one(structured_settings)
                updated_doc = await settings_collection.find_one({"_id": result.inserted_id})
            
            # Convert to flat response format
            flat_response = SettingsController._convert_to_flat_response(updated_doc)
            return flat_response
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update system settings: {str(e)}"
            )
    
    @staticmethod
    async def reset_settings(current_user: dict) -> SystemSettingsFlatResponse:
        """Reset settings to default values"""
        try:
            db = get_db()
            settings_collection = db.system_settings
            
            # Get default settings
            default_settings = SettingsController._get_default_settings()
            default_settings["updated_at"] = datetime.utcnow()
            
            # Check if settings exist
            existing_settings = await settings_collection.find_one({})
            
            if existing_settings:
                # Update with default values
                result = await settings_collection.update_one(
                    {"_id": existing_settings["_id"]},
                    {"$set": default_settings}
                )
                updated_doc = await settings_collection.find_one({"_id": existing_settings["_id"]})
            else:
                # Create new default settings
                default_settings["created_at"] = datetime.utcnow()
                result = await settings_collection.insert_one(default_settings)
                updated_doc = await settings_collection.find_one({"_id": result.inserted_id})
            
            # Convert to flat response format
            flat_response = SettingsController._convert_to_flat_response(updated_doc)
            return flat_response
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to reset system settings: {str(e)}"
            )
    
    @staticmethod
    def _get_default_settings() -> Dict[str, Any]:
        """Get default system settings"""
        return {
            # System Configuration
            "system_name": "Marshalats Learning Management System",
            "system_version": "1.0.0",
            "maintenance_mode": False,
            "debug_mode": False,
            
            # Email Configuration
            "email_enabled": True,
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_username": "",
            "smtp_security": "tls",
            
            # Notification Settings
            "notifications_enabled": True,
            "email_notifications": True,
            "sms_notifications": False,
            
            # Security Settings
            "session_timeout": "24",
            "password_policy": "medium",
            "two_factor_auth": False,
            
            # Backup Settings
            "auto_backup": True,
            "backup_frequency": "daily",
            "backup_retention": "30",

            # Pricing
            "registration_fee": 500.0,
            "coach_registration_fee": 0.0,
            "coach_registration_fee_note": "",
            "partner_registration_fee": 999.0,
            "home_training_fee": 0.0,
            "school_training_fee": 0.0,
            "college_training_fee": 0.0,
            "corporate_training_fee": 0.0,
            "residential_training_fee": 0.0,
        }
    
    @staticmethod
    async def _create_default_settings() -> Dict[str, Any]:
        """Create default settings in database"""
        db = get_db()
        settings_collection = db.system_settings
        
        default_settings = SettingsController._get_default_settings()
        default_settings["created_at"] = datetime.utcnow()
        default_settings["updated_at"] = datetime.utcnow()
        
        result = await settings_collection.insert_one(default_settings)
        return await settings_collection.find_one({"_id": result.inserted_id})
    
    @staticmethod
    def _convert_flat_to_structured(flat_data: SystemSettingsFlatCreate) -> Dict[str, Any]:
        """Convert flat settings data to structured format for database storage"""
        return {
            # System Configuration
            "system_name": flat_data.system_name,
            "system_version": flat_data.system_version,
            "maintenance_mode": flat_data.maintenance_mode,
            "debug_mode": flat_data.debug_mode,
            
            # Email Configuration
            "email_enabled": flat_data.email_enabled,
            "smtp_host": flat_data.smtp_host,
            "smtp_port": flat_data.smtp_port,
            "smtp_username": flat_data.smtp_username,
            "smtp_security": flat_data.smtp_security,
            
            # Notification Settings
            "notifications_enabled": flat_data.notifications_enabled,
            "email_notifications": flat_data.email_notifications,
            "sms_notifications": flat_data.sms_notifications,
            
            # Security Settings
            "session_timeout": flat_data.session_timeout,
            "password_policy": flat_data.password_policy,
            "two_factor_auth": flat_data.two_factor_auth,
            
            # Backup Settings
            "auto_backup": flat_data.auto_backup,
            "backup_frequency": flat_data.backup_frequency,
            "backup_retention": flat_data.backup_retention,

            "registration_fee": float(flat_data.registration_fee),
            "coach_registration_fee": float(flat_data.coach_registration_fee or 0.0),
            "coach_registration_fee_note": (flat_data.coach_registration_fee_note or "").strip(),
            "partner_registration_fee": float(
                flat_data.partner_registration_fee
                if flat_data.partner_registration_fee is not None
                else 999.0
            ),
            "home_training_fee": float(flat_data.home_training_fee or 0.0),
            "school_training_fee": float(flat_data.school_training_fee or 0.0),
            "college_training_fee": float(flat_data.college_training_fee or 0.0),
            "corporate_training_fee": float(flat_data.corporate_training_fee or 0.0),
            "residential_training_fee": float(flat_data.residential_training_fee or 0.0),
        }
    
    @staticmethod
    def _convert_to_flat_response(settings_doc: Dict[str, Any]) -> SystemSettingsFlatResponse:
        """Convert database document to flat response format"""
        serialized_doc = serialize_doc(settings_doc)
        
        return SystemSettingsFlatResponse(
            id=serialized_doc["id"],
            # System Configuration
            system_name=serialized_doc.get("system_name", "Marshalats Learning Management System"),
            system_version=serialized_doc.get("system_version", "1.0.0"),
            maintenance_mode=serialized_doc.get("maintenance_mode", False),
            debug_mode=serialized_doc.get("debug_mode", False),
            
            # Email Configuration
            email_enabled=serialized_doc.get("email_enabled", True),
            smtp_host=serialized_doc.get("smtp_host", ""),
            smtp_port=serialized_doc.get("smtp_port", "587"),
            smtp_username=serialized_doc.get("smtp_username", ""),
            smtp_security=serialized_doc.get("smtp_security", "tls"),
            
            # Notification Settings
            notifications_enabled=serialized_doc.get("notifications_enabled", True),
            email_notifications=serialized_doc.get("email_notifications", True),
            sms_notifications=serialized_doc.get("sms_notifications", False),
            
            # Security Settings
            session_timeout=serialized_doc.get("session_timeout", "24"),
            password_policy=serialized_doc.get("password_policy", "medium"),
            two_factor_auth=serialized_doc.get("two_factor_auth", False),
            
            # Backup Settings
            auto_backup=serialized_doc.get("auto_backup", True),
            backup_frequency=serialized_doc.get("backup_frequency", "daily"),
            backup_retention=serialized_doc.get("backup_retention", "30"),

            registration_fee=float(serialized_doc.get("registration_fee", 500.0)),
            coach_registration_fee=float(serialized_doc.get("coach_registration_fee", 0.0)),
            coach_registration_fee_note=serialized_doc.get("coach_registration_fee_note", ""),
            partner_registration_fee=float(serialized_doc.get("partner_registration_fee", 999.0)),
            home_training_fee=float(serialized_doc.get("home_training_fee", 0.0)),
            school_training_fee=float(serialized_doc.get("school_training_fee", 0.0)),
            college_training_fee=float(serialized_doc.get("college_training_fee", 0.0)),
            corporate_training_fee=float(serialized_doc.get("corporate_training_fee", 0.0)),
            residential_training_fee=float(serialized_doc.get("residential_training_fee", 0.0)),
            
            created_at=serialized_doc.get("created_at", datetime.utcnow()),
            updated_at=serialized_doc.get("updated_at", datetime.utcnow())
        )

    @staticmethod
    async def get_public_registration_settings() -> dict:
        """Public: default registration fee and training program fees for checkout (no auth)."""
        try:
            db = get_db()
            doc = await db.system_settings.find_one({}) or {}
            def _get_f(key, d=0.0):
                val = doc.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        return d
                return d

            fee = _get_f("registration_fee", 500.0)
            coach_fee = _get_f("coach_registration_fee", 0.0)
            coach_fee_note = str(doc.get("coach_registration_fee_note") or "").strip()
            partner_fee = _get_f("partner_registration_fee", 999.0)
            home_fee = _get_f("home_training_fee", 0.0)
            school_fee = _get_f("school_training_fee", 0.0)
            college_fee = _get_f("college_training_fee", 0.0)
            corporate_fee = _get_f("corporate_training_fee", 0.0)
            residential_fee = _get_f("residential_training_fee", 0.0)

            return {
                "registrationFee": fee,
                "coachRegistrationFee": coach_fee,
                "coachRegistrationFeeNote": coach_fee_note,
                "partnerRegistrationFee": partner_fee,
                "homeTrainingFee": home_fee,
                "schoolTrainingFee": school_fee,
                "collegeTrainingFee": college_fee,
                "corporateTrainingFee": corporate_fee,
                "residentialTrainingFee": residential_fee,
                "registration_fee": fee,
                "coach_registration_fee": coach_fee,
                "coach_registration_fee_note": coach_fee_note,
                "partner_registration_fee": partner_fee,
                "home_training_fee": home_fee,
                "school_training_fee": school_fee,
                "college_training_fee": college_fee,
                "corporate_training_fee": corporate_fee,
                "residential_training_fee": residential_fee,
            }
        except Exception:
            return {
                "registrationFee": 500.0,
                "coachRegistrationFee": 0.0,
                "coachRegistrationFeeNote": "",
                "partnerRegistrationFee": 999.0,
                "homeTrainingFee": 0.0,
                "schoolTrainingFee": 0.0,
                "collegeTrainingFee": 0.0,
                "corporateTrainingFee": 0.0,
                "residentialTrainingFee": 0.0,
                "registration_fee": 500.0,
                "coach_registration_fee": 0.0,
                "coach_registration_fee_note": "",
                "partner_registration_fee": 999.0,
                "home_training_fee": 0.0,
                "school_training_fee": 0.0,
                "college_training_fee": 0.0,
                "corporate_training_fee": 0.0,
                "residential_training_fee": 0.0,
            }

    @staticmethod
    async def get_default_coach_registration_fee() -> float:
        """Read coach_registration_fee from system_settings (single document)."""
        try:
            db = get_db()
            doc = await db.system_settings.find_one({})
            if doc and doc.get("coach_registration_fee") is not None:
                return float(doc["coach_registration_fee"])
        except Exception:
            pass
        return 0.0

    @staticmethod
    async def get_default_partner_registration_fee() -> float:
        """Read partner_registration_fee from system_settings (fallback env / 999)."""
        try:
            db = get_db()
            doc = await db.system_settings.find_one({})
            if doc and doc.get("partner_registration_fee") is not None:
                return float(doc["partner_registration_fee"])
        except Exception:
            pass
        try:
            import os

            return float(os.getenv("PARTNER_ONBOARDING_FEE_INR", "999") or "999")
        except (TypeError, ValueError):
            return 999.0

    @staticmethod
    async def get_default_registration_fee() -> float:
        """Read registration_fee from system_settings (single document)."""
        try:
            db = get_db()
            doc = await db.system_settings.find_one({})
            if doc and doc.get("registration_fee") is not None:
                return float(doc["registration_fee"])
        except Exception:
            pass
        return 500.0
