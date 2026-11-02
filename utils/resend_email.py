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


async def send_html_email_via_resend(
    to_email: str,
    subject: str,
    html_message: str,
    plain_message: Optional[str] = None,
) -> bool:
    """Send an arbitrary HTML email via Resend (shared by credentials / custom mailers)."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )

    if not api_key:
        logger.warning(
            "RESEND_API_KEY not configured. Custom email not sent to %s (subject=%s)",
            to_email,
            subject,
        )
        return False

    payload: dict = {
        "from": from_email,
        "to": [to_email.strip()],
        "subject": subject,
        "html": html_message,
    }
    if plain_message and str(plain_message).strip():
        payload["text"] = str(plain_message).strip()

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
                    "Custom email sent via Resend to %s (status=%d)",
                    to_email,
                    resp.status_code,
                )
                return True
            logger.error(
                "Resend API error sending custom email to %s: HTTP %d: %s",
                to_email,
                resp.status_code,
                resp.text,
            )
            return False
    except Exception as exc:
        logger.exception("Failed to send custom email via Resend to %s: %s", to_email, exc)
        return False


async def send_partner_invite_email(
    *,
    to_email: str,
    partner_name: str,
    phone: str,
    address: str,
    payment_method: str,
    payment_status: str,
    set_password_link: str,
    login_link: str,
) -> bool:
    """Send partner admin invite (create password) via Resend."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )

    if not api_key:
        logger.warning(
            "RESEND_API_KEY not configured. Partner invite not sent. Link for %s: %s",
            to_email,
            set_password_link,
        )
        return False

    display = (partner_name or "Partner").strip()
    subject = f"Welcome to {display} — create your partner admin password"
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <div style="max-width:560px;margin:40px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,.1);">
    <div style="background:linear-gradient(135deg,#111827 0%,#1f2937 100%);padding:28px 24px;text-align:center;">
      <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">Partner Admin Invite</h1>
      <p style="margin:8px 0 0;color:#fbbf24;font-size:13px;font-weight:600;letter-spacing:.5px;">WHITE-LABEL DASHBOARD</p>
    </div>
    <div style="padding:28px 28px 20px;">
      <h2 style="margin:0 0 12px;font-size:18px;color:#111827;">Hello {display},</h2>
      <p style="margin:0 0 16px;color:#4b5563;font-size:14px;line-height:1.6;">
        Your partner organization has been created. Set your password to access your partner admin dashboard.
      </p>
      <table style="width:100%;border-collapse:collapse;margin:12px 0 20px;font-size:14px;">
        <tr><td style="padding:6px 0;color:#6b7280;">Partner</td><td style="padding:6px 0;"><b>{display}</b></td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Email</td><td style="padding:6px 0;">{to_email}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Phone</td><td style="padding:6px 0;">{phone or "—"}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Address</td><td style="padding:6px 0;">{address or "—"}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Payment</td><td style="padding:6px 0;">{payment_method or "—"} / {payment_status or "—"}</td></tr>
      </table>
      <div style="text-align:center;margin:24px 0;">
        <a href="{set_password_link}" style="display:inline-block;background:#FFC403;color:#1a2332;font-size:15px;font-weight:600;text-decoration:none;padding:12px 28px;border-radius:8px;">
          Create password
        </a>
      </div>
      <p style="margin:0 0 8px;color:#6b7280;font-size:12px;">Login after setup:</p>
      <p style="margin:0;color:#111827;font-size:12px;word-break:break-all;background:#f3f4f6;padding:10px;border-radius:6px;">{login_link}</p>
      <p style="margin:16px 0 0;color:#9ca3af;font-size:12px;">This invite link expires in 7 days.</p>
    </div>
  </div>
</body>
</html>"""

    payload = {
        "from": from_email,
        "to": [to_email.strip()],
        "subject": subject,
        "html": html,
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
                    "Partner invite email sent via Resend to %s (status=%d)",
                    to_email,
                    resp.status_code,
                )
                return True
            logger.error(
                "Resend API error sending partner invite to %s: HTTP %d: %s",
                to_email,
                resp.status_code,
                resp.text,
            )
            return False
    except Exception as exc:
        logger.exception("Failed to send partner invite via Resend to %s: %s", to_email, exc)
        return False

