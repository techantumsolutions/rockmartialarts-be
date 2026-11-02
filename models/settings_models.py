from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, Literal
import uuid

class SystemConfiguration(BaseModel):
    """System configuration settings"""
    system_name: str = Field(..., min_length=1, max_length=100, description="Name of the system")
    system_version: str = Field(default="1.0.0", description="System version (read-only)")
    maintenance_mode: bool = Field(default=False, description="Enable maintenance mode")
    debug_mode: bool = Field(default=False, description="Enable debug mode")

class EmailConfiguration(BaseModel):
    """Email configuration settings"""
    email_enabled: bool = Field(default=True, description="Enable email functionality")
    smtp_host: str = Field(default="", description="SMTP server host")
    smtp_port: str = Field(default="587", description="SMTP server port")
    smtp_username: str = Field(default="", description="SMTP username")
    smtp_security: Literal["none", "tls", "ssl"] = Field(default="tls", description="SMTP security protocol")
    
    @validator('smtp_port')
    def validate_smtp_port(cls, v):
        if v and not v.isdigit():
            raise ValueError('SMTP port must be a number')
        if v and (int(v) < 1 or int(v) > 65535):
            raise ValueError('SMTP port must be between 1 and 65535')
        return v

class NotificationSettings(BaseModel):
    """Notification settings"""
    notifications_enabled: bool = Field(default=True, description="Enable notifications")
    email_notifications: bool = Field(default=True, description="Enable email notifications")
    sms_notifications: bool = Field(default=False, description="Enable SMS notifications")

class SecuritySettings(BaseModel):
    """Security settings"""
    session_timeout: str = Field(default="24", description="Session timeout in hours")
    password_policy: Literal["weak", "medium", "strong"] = Field(default="medium", description="Password policy strength")
    two_factor_auth: bool = Field(default=False, description="Enable two-factor authentication")
    
    @validator('session_timeout')
    def validate_session_timeout(cls, v):
        if not v.isdigit():
            raise ValueError('Session timeout must be a number')
        if int(v) < 1 or int(v) > 168:  # Max 1 week
            raise ValueError('Session timeout must be between 1 and 168 hours')
        return v

class BackupSettings(BaseModel):
    """Backup settings"""
    auto_backup: bool = Field(default=True, description="Enable automatic backups")
    backup_frequency: Literal["daily", "weekly", "monthly"] = Field(default="daily", description="Backup frequency")
    backup_retention: str = Field(default="30", description="Backup retention period in days")
    
    @validator('backup_retention')
    def validate_backup_retention(cls, v):
        if not v.isdigit():
            raise ValueError('Backup retention must be a number')
        if int(v) < 1 or int(v) > 365:
            raise ValueError('Backup retention must be between 1 and 365 days')
        return v

class SystemSettings(BaseModel):
    """Complete system settings model"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    system_configuration: SystemConfiguration
    email_configuration: EmailConfiguration
    notification_settings: NotificationSettings
    security_settings: SecuritySettings
    backup_settings: BackupSettings
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class SystemSettingsCreate(BaseModel):
    """Model for creating system settings"""
    system_configuration: SystemConfiguration
    email_configuration: EmailConfiguration
    notification_settings: NotificationSettings
    security_settings: SecuritySettings
    backup_settings: BackupSettings

class SystemSettingsUpdate(BaseModel):
    """Model for updating system settings"""
    system_configuration: Optional[SystemConfiguration] = None
    email_configuration: Optional[EmailConfiguration] = None
    notification_settings: Optional[NotificationSettings] = None
    security_settings: Optional[SecuritySettings] = None
    backup_settings: Optional[BackupSettings] = None

class SystemSettingsResponse(BaseModel):
    """Response model for system settings"""
    id: str
    system_configuration: SystemConfiguration
    email_configuration: EmailConfiguration
    notification_settings: NotificationSettings
    security_settings: SecuritySettings
    backup_settings: BackupSettings
    created_at: datetime
    updated_at: datetime

class SystemSettingsFlatCreate(BaseModel):
    """Flat model for creating system settings (matches frontend structure)"""
    # System Configuration
    system_name: str = Field(..., min_length=1, max_length=100)
    system_version: str = Field(default="1.0.0")
    maintenance_mode: bool = Field(default=False)
    debug_mode: bool = Field(default=False)

    # Email Configuration
    email_enabled: bool = Field(default=True)
    smtp_host: str = Field(default="")
    smtp_port: str = Field(default="587")
    smtp_username: str = Field(default="")
    smtp_security: Literal["none", "tls", "ssl"] = Field(default="tls")

    # Notification Settings
    notifications_enabled: bool = Field(default=True)
    email_notifications: bool = Field(default=True)
    sms_notifications: bool = Field(default=False)

    # Security Settings
    session_timeout: str = Field(default="24")
    password_policy: Literal["weak", "medium", "strong"] = Field(default="medium")
    two_factor_auth: bool = Field(default=False)

    # Backup Settings
    auto_backup: bool = Field(default=True)
    backup_frequency: Literal["daily", "weekly", "monthly"] = Field(default="daily")
    backup_retention: str = Field(default="30")

    # Pricing (public registration / admission default when branch has no override)
    registration_fee: float = Field(default=500.0, ge=0, description="Default registration fee (INR)")
    coach_registration_fee: float = Field(default=0.0, ge=0, description="Coach registration fee (INR)")

    @validator('smtp_port')
    def validate_smtp_port(cls, v):
        if v and not v.isdigit():
            raise ValueError('SMTP port must be a number')
        if v and (int(v) < 1 or int(v) > 65535):
            raise ValueError('SMTP port must be between 1 and 65535')
        return v

    @validator('session_timeout')
    def validate_session_timeout(cls, v):
        if not v.isdigit():
            raise ValueError('Session timeout must be a number')
        if int(v) < 1 or int(v) > 168:
            raise ValueError('Session timeout must be between 1 and 168 hours')
        return v

    @validator('backup_retention')
    def validate_backup_retention(cls, v):
        if not v.isdigit():
            raise ValueError('Backup retention must be a number')
        if int(v) < 1 or int(v) > 365:
            raise ValueError('Backup retention must be between 1 and 365 days')
        return v

class SystemSettingsFlatResponse(BaseModel):
    """Flat response model for system settings (matches frontend structure)"""
    id: str
    # System Configuration
    system_name: str
    system_version: str
    maintenance_mode: bool
    debug_mode: bool

    # Email Configuration
    email_enabled: bool
    smtp_host: str
    smtp_port: str
    smtp_username: str
    smtp_security: str

    # Notification Settings
    notifications_enabled: bool
    email_notifications: bool
    sms_notifications: bool

    # Security Settings
    session_timeout: str
    password_policy: str
    two_factor_auth: bool

    # Backup Settings
    auto_backup: bool
    backup_frequency: str
    backup_retention: str

    registration_fee: float
    coach_registration_fee: float = 0.0

    created_at: datetime
    updated_at: datetime
