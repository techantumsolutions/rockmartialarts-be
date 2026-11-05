"""
Email service for sending emails using SMTP
"""

import smtplib
import ssl
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
from pathlib import Path
from dotenv import load_dotenv
import httpx

# Load environment variables
ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self._load_config()

    def _load_config(self):
        """Load SMTP configuration from environment variables"""
        self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = os.getenv('SMTP_USER', '')
        self.smtp_pass = os.getenv('SMTP_PASS', '')
        self.smtp_from = os.getenv('SMTP_FROM', self.smtp_user)

        # Debug logging
        logger.info(f"Loading email config - Host: {self.smtp_host}, Port: {self.smtp_port}")
        logger.info(f"SMTP User: {self.smtp_user}, Has Password: {bool(self.smtp_pass)}")

        # Validate configuration
        if not self.smtp_user or not self.smtp_pass:
            logger.warning("SMTP credentials not configured. Email sending will be disabled.")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Email service enabled with SMTP host: {self.smtp_host}:{self.smtp_port}")
            logger.info(f"SMTP user: {self.smtp_user}")

    def reload_config(self):
        """Reload configuration from environment variables"""
        self._load_config()

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None
    ) -> bool:
        """
        Send an email

        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body
            from_email: Optional sender email (defaults to SMTP_FROM)

        Returns:
            bool: True if email was sent successfully, False otherwise
        """
        # Reload config in case environment variables changed
        if not self.enabled:
            self._load_config()

        if not self.enabled:
            logger.warning(f"Email service disabled. Would send email to {to_email}: {subject}")
            return False
            
        try:
            # Create message
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = from_email or self.smtp_from
            message["To"] = to_email
            
            # Add plain text part
            text_part = MIMEText(body, "plain")
            message.attach(text_part)
            
            # Add HTML part if provided
            if html_body:
                html_part = MIMEText(html_body, "html")
                message.attach(html_part)
            
            # Handle different SMTP configurations
            if self.smtp_host == 'localhost' and self.smtp_port == 1025:
                # Mock SMTP server - no authentication needed
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.sendmail(self.smtp_from, to_email, message.as_string())
                logger.info(f"📧 Email sent via mock SMTP server")
            else:
                # Real SMTP server - use secure connection
                context = ssl.create_default_context()

                # Use SMTP_SSL for port 465, SMTP with STARTTLS for port 587
                if self.smtp_port == 465:
                    with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                        server.login(self.smtp_user, self.smtp_pass)
                        server.sendmail(self.smtp_from, to_email, message.as_string())
                else:
                    with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                        if self.smtp_port == 587:
                            server.starttls(context=context)
                        server.login(self.smtp_user, self.smtp_pass)
                        server.sendmail(self.smtp_from, to_email, message.as_string())
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    async def send_password_reset_email(self, to_email: str, reset_token: str, user_name: str, user_type: str = "student") -> bool:
        """
        Send password reset email with a formatted template

        Args:
            to_email: User's email address
            reset_token: Password reset token
            user_name: User's full name
            user_type: Type of user (student, coach, superadmin)

        Returns:
            bool: True if email was sent successfully
        """
        # Create reset link - SAME FOR ALL USER TYPES (consistent implementation)
        reset_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3022')}/reset-password?token={reset_token}"

        # Customize subject and branding based on user type
        if user_type == "superadmin":
            subject = "🔐 Superadmin Password Reset Request - Rock Martial Arts Academy"
            role_title = "Superadmin"
            theme_color = "#dc2626"  # Red
            emoji = "🔐"
        elif user_type == "coach":
            subject = "🥋 Coach Password Reset Request - Rock Martial Arts Academy"
            role_title = "Coach"
            theme_color = "#ea580c"  # Orange
            emoji = "🥋"
        else:  # student (default)
            subject = "Password Reset Request - Rock Martial Arts Academy"
            role_title = "Student"
            theme_color = "#2563eb"  # Blue
            emoji = "🥋"
        
        # Plain text version - customized by user type
        text_body = f"""
Hello {user_name},

You have requested to reset your {role_title.lower()} password for your Rock Martial Arts Academy account.

Please click on the following link to reset your password:
{reset_link}

This link will expire in 15 minutes for security reasons.

If you did not request this password reset, please ignore this email and your password will remain unchanged.

Best regards,
Rock Martial Arts Academy Team
        """.strip()
        
        # HTML version - role-specific styling and branding
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{role_title} Password Reset Request</title>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e9ecef; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 12px; color: #6c757d; }}
        .btn {{ display: inline-block; padding: 12px 24px; background-color: {theme_color}; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; margin: 20px 0; }}
        .btn:hover {{ opacity: 0.9; }}
        .warning {{ background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .role-header {{ color: {theme_color}; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; color: {theme_color};">{emoji} Rock Martial Arts Academy</h1>
            <p style="margin: 5px 0 0 0; color: #666;">{role_title} Portal</p>
        </div>

        <div class="content">
            <h2 class="role-header">{role_title} Password Reset Request</h2>
            <p>Hello <strong>{user_name}</strong>,</p>

            <p>You have requested to reset your <strong>{role_title.lower()} password</strong> for your Rock Martial Arts Academy account.</p>

            <p>Please click the button below to reset your password:</p>

            <div style="text-align: center;">
                <a href="{reset_link}" class="btn">Reset {role_title} Password</a>
            </div>

            <div class="warning">
                <strong>⚠️ Important:</strong> This link will expire in <strong>15 minutes</strong> for security reasons.
            </div>
            
            <p>If the button doesn't work, you can copy and paste this link into your browser:</p>
            <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 4px;">
                {reset_link}
            </p>
            
            <p>If you did not request this password reset, please ignore this email and your password will remain unchanged.</p>
        </div>
        
        <div class="footer">
            <p>Best regards,<br>Martial Arts Academy Team</p>
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
        """.strip()
        
        return await self.send_email(to_email, subject, text_body, html_body)

    async def send_password_reset_email_webhook(self, to_email: str, reset_token: str, user_name: str, user_type: str = "student") -> bool:
        """
        Send password reset email using webhook service (same as /api/email/send-webhook-email)

        Args:
            to_email: User's email address
            reset_token: Password reset token
            user_name: User's full name
            user_type: Type of user (student, coach, superadmin)

        Returns:
            bool: True if email was sent successfully via webhook
        """
        # Create reset link - SAME FOR ALL USER TYPES (consistent implementation)
        reset_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3022')}/reset-password?token={reset_token}"

        # Customize subject and branding based on user type
        if user_type == "superadmin":
            subject = "🔐 Superadmin Password Reset Request - Rock Martial Arts Academy"
            role_title = "Superadmin"
            theme_color = "#dc2626"  # Red
            emoji = "🔐"
        elif user_type == "coach":
            subject = "🥋 Coach Password Reset Request - Rock Martial Arts Academy"
            role_title = "Coach"
            theme_color = "#ea580c"  # Orange
            emoji = "🥋"
        else:  # student (default)
            subject = "Password Reset Request - Rock Martial Arts Academy"
            role_title = "Student"
            theme_color = "#2563eb"  # Blue
            emoji = "🥋"

        # Plain text version - customized by user type
        text_body = f"""
Hello {user_name},

You have requested to reset your {role_title.lower()} password for your Rock Martial Arts Academy account.

Please click on the following link to reset your password:
{reset_link}

This link will expire in 15 minutes for security reasons.

If you did not request this password reset, please ignore this email and your password will remain unchanged.

Best regards,
Rock Martial Arts Academy Team
        """.strip()

        # HTML version - role-specific styling and branding (same as SMTP version)
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{role_title} Password Reset Request</title>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #ffffff; padding: 30px; border: 1px solid #e9ecef; }}
        .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 12px; color: #6c757d; }}
        .btn {{ display: inline-block; padding: 12px 24px; background-color: {theme_color}; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; margin: 20px 0; }}
        .btn:hover {{ opacity: 0.9; }}
        .warning {{ background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .role-header {{ color: {theme_color}; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; color: {theme_color};">{emoji} Rock Martial Arts Academy</h1>
            <p style="margin: 5px 0 0 0; color: #666;">{role_title} Portal</p>
        </div>

        <div class="content">
            <h2 class="role-header">{role_title} Password Reset Request</h2>
            <p>Hello <strong>{user_name}</strong>,</p>

            <p>You have requested to reset your <strong>{role_title.lower()} password</strong> for your Rock Martial Arts Academy account.</p>

            <p>Please click the button below to reset your password:</p>

            <div style="text-align: center;">
                <a href="{reset_link}" class="btn">Reset {role_title} Password</a>
            </div>

            <div class="warning">
                <strong>⚠️ Important:</strong> This link will expire in <strong>15 minutes</strong> for security reasons.
            </div>

            <p>If the button doesn't work, you can copy and paste this link into your browser:</p>
            <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 4px;">
                {reset_link}
            </p>

            <p>If you did not request this password reset, please ignore this email and your password will remain unchanged.</p>
        </div>

        <div class="footer">
            <p style="margin: 0;">© 2025 Rock Martial Arts Academy. All rights reserved.</p>
            <p style="margin: 5px 0 0 0;">This is an automated message, please do not reply.</p>
        </div>
    </div>
</body>
</html>
        """.strip()

        # Send via webhook (same as /api/email/send-webhook-email implementation)
        webhook_url = "https://ai.alviongs.com/webhook/de77d8d6-ae98-471d-ba19-8d7f58ec8449"

        try:
            logger.info(f"📧 Sending {role_title.lower()} password reset email via webhook to {to_email}")
            logger.info(f"📍 Webhook URL: {webhook_url}")
            logger.info(f"📋 Subject: {subject}")

            # Prepare payload for webhook (same format as /api/email/send-webhook-email)
            webhook_payload = {
                "to_email": to_email,
                "subject": subject,
                "message": text_body,
                "html_message": html_body
            }

            # Send request to webhook (same implementation as /api/email/send-webhook-email)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    webhook_url,
                    json=webhook_payload,
                    headers={"Content-Type": "application/json"}
                )

                logger.info(f"📨 Webhook response status: {response.status_code}")

                if response.status_code != 200:
                    logger.error(f"❌ Webhook failed with status {response.status_code}: {response.text}")
                    return False

                # Parse webhook response
                webhook_response = response.json()
                logger.info(f"✅ {role_title} password reset email sent successfully via webhook")
                logger.info(f"📊 Response: {webhook_response}")

                return True

        except httpx.TimeoutException:
            logger.error("❌ Webhook request timed out")
            return False
        except httpx.RequestError as e:
            logger.error(f"❌ Webhook request failed: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending webhook email: {str(e)}")
            return False

# Global email service instance (lazy-loaded)
_email_service = None

def get_email_service() -> EmailService:
    """Get or create the global email service instance"""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service

# Convenience function for backward compatibility
async def send_email(to_email: str, subject: str, body: str, html_body: Optional[str] = None) -> bool:
    """Send an email using the global email service"""
    service = get_email_service()
    return await service.send_email(to_email, subject, body, html_body)

async def send_password_reset_email(to_email: str, reset_token: str, user_name: str, user_type: str = "student") -> bool:
    """Send password reset email using the global email service"""
    service = get_email_service()
    return await service.send_password_reset_email(to_email, reset_token, user_name, user_type)

async def send_password_reset_email_webhook(to_email: str, reset_token: str, user_name: str, user_type: str = "student") -> bool:
    """Send password reset email using webhook service (same as /api/email/send-webhook-email)"""
    service = get_email_service()
    return await service.send_password_reset_email_webhook(to_email, reset_token, user_name, user_type)

async def send_custom_email_webhook(to_email: str, subject: str, html_message: str, plain_message: str) -> bool:
    """Send custom email. Prefer Resend when configured (webhook host SSL is unreliable)."""
    import logging
    import os

    logger = logging.getLogger(__name__)

    # Prefer Resend — same path used by partner invite / student welcome
    if (os.getenv("RESEND_API_KEY") or "").strip():
        try:
            from utils.resend_email import send_html_email_via_resend

            logger.info(f"📧 Sending custom email via Resend to {to_email}")
            logger.info(f"📋 Subject: {subject}")
            if await send_html_email_via_resend(
                to_email, subject, html_message, plain_message
            ):
                return True
            logger.warning("Resend custom email failed; falling back to webhook")
        except Exception as e:
            logger.error(f"❌ Resend custom email error: {str(e)}; falling back to webhook")

    webhook_url = "https://ai.alviongs.com/webhook/de77d8d6-ae98-471d-ba19-8d7f58ec8449"

    try:
        import httpx

        logger.info(f"📧 Sending custom email via webhook to {to_email}")
        logger.info(f"📍 Webhook URL: {webhook_url}")
        logger.info(f"📋 Subject: {subject}")

        # Prepare payload for webhook
        webhook_payload = {
            "to_email": to_email,
            "subject": subject,
            "message": plain_message,
            "html_message": html_message
        }

        # Send request to webhook (host cert often mismatches; verify=False for this fallback only)
        async with httpx.AsyncClient(timeout=30.0, verify=False, trust_env=False) as client:
            response = await client.post(
                webhook_url,
                json=webhook_payload,
                headers={"Content-Type": "application/json"}
            )

            logger.info(f"📨 Webhook response status: {response.status_code}")

            if response.status_code != 200:
                logger.error(f"❌ Webhook failed with status {response.status_code}: {response.text}")
                return False

            # Parse webhook response
            webhook_response = response.json()
            logger.info(f"✅ Custom email sent successfully via webhook")
            logger.info(f"📊 Response: {webhook_response}")

            return True

    except Exception as e:
        logger.error(f"❌ Failed to send custom email via webhook: {str(e)}")
        return False
