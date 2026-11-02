from datetime import datetime
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


def build_student_welcome_email_html(
    student_name: str,
    setup_link: str,
    creator_name: Optional[str] = None,
    creator_role: Optional[str] = None,
) -> str:
    """Build a modern, responsive HTML email template for student password setup."""
    display_name = student_name.strip() if student_name else "Student"
    creator_badge = ""
    if creator_name and creator_role:
        role_label = creator_role.replace("_", " ").title()
        creator_badge = f"""
        <div style="background-color: #f3f4f6; border-left: 4px solid #ef4444; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; color: #4b5563; font-size: 14px;">
                <strong>Created by:</strong> {creator_name} ({role_label})
            </p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to Rock Martial Arts</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937;">
    <div style="max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #111827 0%, #1f2937 100%); padding: 32px 24px; text-align: center;">
            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">
                ROCK MARTIAL ARTS ACADEMY
            </h1>
            <p style="margin: 8px 0 0 0; color: #ef4444; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                Student Account Setup
            </p>
        </div>

        <!-- Content -->
        <div style="padding: 36px 32px;">
            <h2 style="margin: 0 0 16px 0; color: #111827; font-size: 20px; font-weight: 600;">
                Hello {display_name},
            </h2>
            <p style="margin: 0 0 16px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                Welcome to <strong>Rock Martial Arts Academy</strong>! Your student account has been successfully created.
            </p>
            {creator_badge}
            <p style="margin: 0 0 24px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                Please set up your personal password to activate your account and access your student dashboard, training schedule, and attendance records.
            </p>

            <!-- Action Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{setup_link}" style="display: inline-block; background-color: #ef4444; color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px; box-shadow: 0 2px 4px rgba(239, 68, 68, 0.3);">
                    Set Up Your Password
                </a>
            </div>

            <!-- Expiration Notice -->
            <div style="background-color: #fffbeb; border: 1px solid #fef3c7; border-radius: 8px; padding: 14px 16px; margin: 24px 0;">
                <p style="margin: 0; color: #92400e; font-size: 13px; line-height: 1.5;">
                    ⏳ <strong>Important:</strong> This password setup link will expire in <strong>24 hours</strong>. If it expires, please contact your academy coach or administrator to generate a new setup link.
                </p>
            </div>

            <p style="margin: 24px 0 8px 0; color: #6b7280; font-size: 13px;">
                If the button above doesn't work, copy and paste this URL into your browser:
            </p>
            <p style="margin: 0; color: #ef4444; font-size: 12px; word-break: break-all; background-color: #f3f4f6; padding: 10px; border-radius: 6px;">
                {setup_link}
            </p>
        </div>

        <!-- Footer -->
        <div style="background-color: #f9fafb; border-top: 1px solid #e5e7eb; padding: 24px; text-align: center;">
            <p style="margin: 0 0 6px 0; color: #6b7280; font-size: 12px;">
                &copy; {2026} Rock Martial Arts Academy. All rights reserved.
            </p>
            <p style="margin: 0; color: #9ca3af; font-size: 11px;">
                If you did not expect this email, please disregard it.
            </p>
        </div>
    </div>
</body>
</html>
"""


async def send_student_welcome_setup_email(
    to_email: str,
    student_name: str,
    setup_link: str,
    creator_name: Optional[str] = None,
    creator_role: Optional[str] = None,
) -> bool:
    """
    Send the 24-hour student password setup link via Resend API.
    Falls back gracefully if RESEND_API_KEY is not configured or in case of error.
    """
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )

    if not api_key:
        logger.warning(
            "RESEND_API_KEY not configured. Password setup email not sent. Setup link for %s: %s",
            to_email,
            setup_link,
        )
        return False

    html_content = build_student_welcome_email_html(
        student_name=student_name,
        setup_link=setup_link,
        creator_name=creator_name,
        creator_role=creator_role,
    )

    payload = {
        "from": from_email,
        "to": [to_email.strip()],
        "subject": "Welcome to Rock Martial Arts - Set Up Your Account Password",
        "html": html_content,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if 200 <= resp.status_code < 300:
                logger.info(
                    "Password setup email successfully sent via Resend to %s (status=%d)",
                    to_email,
                    resp.status_code,
                )
                return True
            else:
                logger.error(
                    "Resend API error sending setup email to %s: HTTP %d: %s",
                    to_email,
                    resp.status_code,
                    resp.text,
                )
                return False
    except Exception as exc:
        logger.exception("Failed to send setup email via Resend to %s: %s", to_email, exc)
        return False



def build_coach_approval_email_html(
    coach_name: str,
    setup_link: str,
    expiry_hours: int = 24,
) -> str:
    """Build a modern, responsive HTML email template for coach password setup upon approval."""
    display_name = coach_name.strip() if coach_name else "Coach"

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Coach Application Approved - Rock Martial Arts Academy</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937;">
    <div style="max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #111827 0%, #1f2937 100%); padding: 32px 24px; text-align: center;">
            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">
                ROCK MARTIAL ARTS ACADEMY
            </h1>
            <p style="margin: 8px 0 0 0; color: #FFB70F; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                Coach Application Approved
            </p>
        </div>

        <!-- Content -->
        <div style="padding: 36px 32px;">
            <h2 style="margin: 0 0 16px 0; color: #111827; font-size: 20px; font-weight: 600;">
                Congratulations {display_name}!
            </h2>
            <p style="margin: 0 0 16px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                We are excited to inform you that your application to join <strong>Rock Martial Arts Academy</strong> as a coach has been <strong>approved</strong> by our administration team.
            </p>
            <p style="margin: 0 0 24px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                To activate your coach account and access your dashboard, schedules, and assigned students, please set up your account password using the secure button below:
            </p>

            <!-- Action Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{setup_link}" style="display: inline-block; background-color: #FFB70F; color: #111827; font-size: 16px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 8px; box-shadow: 0 2px 4px rgba(255, 183, 15, 0.4);">
                    Set Up Your Password
                </a>
            </div>

            <!-- Expiration Notice -->
            <div style="background-color: #fffbeb; border: 1px solid #fef3c7; border-radius: 8px; padding: 14px 16px; margin: 24px 0;">
                <p style="margin: 0; color: #92400e; font-size: 13px; line-height: 1.5;">
                    ⏳ <strong>Important:</strong> This password setup link will expire in <strong>{expiry_hours} hours</strong>. For security purposes, please set up your password before it expires.
                </p>
            </div>

            <p style="margin: 24px 0 8px 0; color: #6b7280; font-size: 13px;">
                If the button above does not work, copy and paste this link into your web browser:
            </p>
            <p style="margin: 0; color: #d97706; font-size: 12px; word-break: break-all; background-color: #f3f4f6; padding: 10px; border-radius: 6px;">
                {setup_link}
            </p>
        </div>

        <!-- Footer -->
        <div style="background-color: #f9fafb; border-top: 1px solid #e5e7eb; padding: 24px; text-align: center;">
            <p style="margin: 0 0 6px 0; color: #6b7280; font-size: 12px;">
                &copy; {datetime.utcnow().year} Rock Martial Arts Academy. All rights reserved.
            </p>
            <p style="margin: 0; color: #9ca3af; font-size: 11px;">
                If you did not register for this account, please contact us immediately.
            </p>
        </div>
    </div>
</body>
</html>
"""


def build_coach_rejection_email_html(
    coach_name: str,
    reason: str,
) -> str:
    """Build a modern, courteous HTML email notification when a coach application is rejected."""
    display_name = coach_name.strip() if coach_name else "Coach"
    rejection_reason = reason.strip() if reason else "Application criteria were not met."

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Coach Application Status - Rock Martial Arts Academy</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937;">
    <div style="max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #111827 0%, #1f2937 100%); padding: 32px 24px; text-align: center;">
            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">
                ROCK MARTIAL ARTS ACADEMY
            </h1>
            <p style="margin: 8px 0 0 0; color: #ef4444; font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                Coach Application Update
            </p>
        </div>

        <!-- Content -->
        <div style="padding: 36px 32px;">
            <h2 style="margin: 0 0 16px 0; color: #111827; font-size: 20px; font-weight: 600;">
                Hello {display_name},
            </h2>
            <p style="margin: 0 0 16px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                Thank you for your interest in joining <strong>Rock Martial Arts Academy</strong> as a coach and for taking the time to submit your application.
            </p>
            <p style="margin: 0 0 20px 0; color: #4b5563; font-size: 15px; line-height: 1.6;">
                After careful review of your profile and submitted details, our administration team has determined that we are unable to approve your application at this time.
            </p>

            <!-- Reason Box -->
            <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 16px 20px; margin: 24px 0; border-radius: 4px;">
                <p style="margin: 0 0 6px 0; color: #991b1b; font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">
                    Reason for Decision:
                </p>
                <p style="margin: 0; color: #7f1d1d; font-size: 14px; line-height: 1.6;">
                    {rejection_reason}
                </p>
            </div>

            <p style="margin: 20px 0 0 0; color: #4b5563; font-size: 14px; line-height: 1.6;">
                If you have questions regarding this decision, additional certifications, or would like to discuss future opportunities, please reach out to our academy administration team.
            </p>
        </div>

        <!-- Footer -->
        <div style="background-color: #f9fafb; border-top: 1px solid #e5e7eb; padding: 24px; text-align: center;">
            <p style="margin: 0 0 6px 0; color: #6b7280; font-size: 12px;">
                &copy; {datetime.utcnow().year} Rock Martial Arts Academy. All rights reserved.
            </p>
        </div>
    </div>
</body>
</html>
"""


async def send_coach_approval_email(
    to_email: str,
    coach_name: str,
    setup_link: str,
    expiry_hours: int = 24,
) -> bool:
    """Send coach approval notification with 24-hour password setup link via Resend or SMTP fallback."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )

    html_content = build_coach_approval_email_html(
        coach_name=coach_name,
        setup_link=setup_link,
        expiry_hours=expiry_hours,
    )
    subject = "Welcome to Rock Martial Arts - Your Coach Application is Approved!"

    if api_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": from_email,
                        "to": [to_email.strip()],
                        "subject": subject,
                        "html": html_content,
                    },
                )
                if 200 <= resp.status_code < 300:
                    logger.info("Coach approval email sent via Resend to %s", to_email)
                    return True
                else:
                    logger.warning("Resend returned status %d for coach approval email", resp.status_code)
        except Exception as e:
            logger.exception("Resend failed for coach approval email to %s: %s", to_email, e)

    # Fallback to SMTP email service
    try:
        from utils.email_service import get_email_service
        service = get_email_service()
        return await service.send_email(
            to_email=to_email,
            subject=subject,
            body=f"Hello {coach_name},\n\nYour coach application has been approved! Set up your password here: {setup_link} (Link valid for {expiry_hours} hours).",
            html_body=html_content,
        )
    except Exception as e:
        logger.exception("Fallback SMTP email failed for %s: %s", to_email, e)
        return False


async def send_coach_rejection_email(
    to_email: str,
    coach_name: str,
    reason: str,
) -> bool:
    """Send coach rejection notification with reason via Resend or SMTP fallback."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )

    html_content = build_coach_rejection_email_html(
        coach_name=coach_name,
        reason=reason,
    )
    subject = "Update regarding your Coach Application - Rock Martial Arts Academy"

    if api_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": from_email,
                        "to": [to_email.strip()],
                        "subject": subject,
                        "html": html_content,
                    },
                )
                if 200 <= resp.status_code < 300:
                    logger.info("Coach rejection email sent via Resend to %s", to_email)
                    return True
                else:
                    logger.warning("Resend returned status %d for coach rejection email", resp.status_code)
        except Exception as e:
            logger.exception("Resend failed for coach rejection email to %s: %s", to_email, e)

    # Fallback to SMTP email service
    try:
        from utils.email_service import get_email_service
        service = get_email_service()
        return await service.send_email(
            to_email=to_email,
            subject=subject,
            body=f"Hello {coach_name},\n\nThank you for applying. We are unable to approve your application at this time.\n\nReason: {reason}",
            html_body=html_content,
        )
    except Exception as e:
        logger.exception("Fallback SMTP email failed for %s: %s", to_email, e)
        return False
