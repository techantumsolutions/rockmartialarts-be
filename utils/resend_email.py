from datetime import datetime
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

def _email_webhook_url() -> str:
    """Optional fallback when Resend fails. Empty disables the broken default webhook."""
    raw = (os.getenv("EMAIL_WEBHOOK_URL") or "").strip()
    if raw.lower() in {"0", "false", "off", "disabled", "none"}:
        return ""
    return raw


def _resend_from_header() -> str:
    raw = (
        (os.getenv("RESEND_FROM_EMAIL") or "").strip()
        or (os.getenv("EMAIL_FROM") or "").strip()
        or "onboarding@resend.dev"
    )
    name = (os.getenv("RESEND_FROM_NAME") or "Rock Martial Arts Academy").strip()
    if "<" in raw and ">" in raw:
        return raw
    return f"{name} <{raw}>"


def _resend_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def _send_via_email_webhook(
    *,
    to_email: str,
    subject: str,
    html_message: str,
    plain_message: str,
) -> bool:
    url = _email_webhook_url()
    if not url:
        logger.warning(
            "Email webhook fallback skipped (set EMAIL_WEBHOOK_URL to enable) for %s subject=%s",
            to_email,
            subject,
        )
        return False
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False, trust_env=False) as client:
            resp = await client.post(
                url,
                json={
                    "to_email": to_email,
                    "subject": subject,
                    "message": plain_message,
                    "html_message": html_message,
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                logger.info("Email webhook accepted message to %s subject=%s", to_email, subject)
                return True
            logger.error(
                "Email webhook failed for %s: HTTP %s %s",
                to_email,
                resp.status_code,
                resp.text[:300],
            )
            return False
    except Exception:
        logger.exception("Email webhook exception for %s", to_email)
        return False


def build_student_welcome_email_html(
    student_name: str,
    setup_link: str,
    creator_name: Optional[str] = None,
    creator_role: Optional[str] = None,
) -> str:
    """Build a modern, responsive HTML email template for student password setup."""
    from utils.email_layout import ACCENT_RED, wrap_rock_email_html
    import html as _html

    display_name = _html.escape(student_name.strip() if student_name else "Student")
    link = _html.escape(setup_link, quote=True)
    creator_badge = ""
    if creator_name and creator_role:
        role_label = _html.escape(creator_role.replace("_", " ").title())
        creator_badge = f"""
        <div style="background-color:#f3f4f6;border-left:4px solid #ef4444;padding:12px 16px;margin:20px 0;border-radius:4px;">
          <p style="margin:0;color:#4b5563;font-size:14px;">
            <strong>Created by:</strong> {_html.escape(creator_name)} ({role_label})
          </p>
        </div>
        """
    content = f"""
      <p style="margin:0 0 16px 0;">Hello <strong>{display_name}</strong>,</p>
      <p style="margin:0 0 16px 0;">Welcome to <strong>Rock Martial Arts Academy</strong>! Your student account has been successfully created.</p>
      {creator_badge}
      <p style="margin:0 0 8px 0;">Set up your password to activate your dashboard, training schedule, and attendance records.</p>
      <div style="background-color:#fffbeb;border:1px solid #fef3c7;border-radius:8px;padding:14px 16px;margin:24px 0;">
        <p style="margin:0;color:#92400e;font-size:13px;line-height:1.5;">
          <strong>Important:</strong> This setup link expires in <strong>24 hours</strong>.
        </p>
      </div>
      <p style="margin:16px 0 6px 0;color:#6b7280;font-size:13px;">Or paste this URL into your browser:</p>
      <p style="margin:0;color:#ef4444;font-size:12px;word-break:break-all;background:#f3f4f6;padding:10px;border-radius:6px;">{link}</p>
    """
    return wrap_rock_email_html(
        content_html=content,
        eyebrow="Student Account Setup",
        cta_label="Set Up Your Password",
        cta_url=setup_link,
        accent=ACCENT_RED,
        footer_note="If you did not expect this email, please disregard it.",
        preheader="Set up your Rock Martial Arts student password",
    )


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
    from utils.email_layout import ACCENT_GOLD, wrap_rock_email_html
    import html as _html

    display_name = _html.escape(coach_name.strip() if coach_name else "Coach")
    link = _html.escape(setup_link, quote=True)
    content = f"""
      <p style="margin:0 0 16px 0;">Congratulations <strong>{display_name}</strong>!</p>
      <p style="margin:0 0 16px 0;">Your coach application at <strong>Rock Martial Arts Academy</strong> has been <strong>approved</strong>.</p>
      <p style="margin:0 0 8px 0;">Set your password to access your coach dashboard, schedules, and assigned students.</p>
      <div style="background-color:#fffbeb;border:1px solid #fef3c7;border-radius:8px;padding:14px 16px;margin:24px 0;">
        <p style="margin:0;color:#92400e;font-size:13px;">
          <strong>Important:</strong> This link expires in <strong>{int(expiry_hours)} hours</strong>.
        </p>
      </div>
      <p style="margin:16px 0 6px 0;color:#6b7280;font-size:13px;">Or paste this URL into your browser:</p>
      <p style="margin:0;color:#d97706;font-size:12px;word-break:break-all;background:#f3f4f6;padding:10px;border-radius:6px;">{link}</p>
    """
    return wrap_rock_email_html(
        content_html=content,
        eyebrow="Coach Application Approved",
        cta_label="Set Up Your Password",
        cta_url=setup_link,
        accent=ACCENT_GOLD,
        footer_note="If you did not register for this account, please contact us immediately.",
        preheader="Your coach application was approved — set your password",
    )


def build_coach_rejection_email_html(
    coach_name: str,
    reason: str,
) -> str:
    """Build a modern, courteous HTML email notification when a coach application is rejected."""
    from utils.email_layout import ACCENT_RED, wrap_rock_email_html
    import html as _html

    display_name = _html.escape(coach_name.strip() if coach_name else "Coach")
    rejection_reason = _html.escape(
        reason.strip() if reason else "Application criteria were not met."
    )
    content = f"""
      <p style="margin:0 0 16px 0;">Hello <strong>{display_name}</strong>,</p>
      <p style="margin:0 0 16px 0;">Thank you for applying to join <strong>Rock Martial Arts Academy</strong> as a coach.</p>
      <p style="margin:0 0 16px 0;">After careful review, we are unable to approve your application at this time.</p>
      <div style="background-color:#fef2f2;border-left:4px solid #ef4444;padding:16px 20px;margin:24px 0;border-radius:4px;">
        <p style="margin:0 0 6px 0;color:#991b1b;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Reason</p>
        <p style="margin:0;color:#7f1d1d;font-size:14px;line-height:1.6;">{rejection_reason}</p>
      </div>
      <p style="margin:0;font-size:14px;">Questions or future opportunities — please contact academy administration.</p>
    """
    return wrap_rock_email_html(
        content_html=content,
        eyebrow="Coach Application Update",
        accent=ACCENT_RED,
        preheader="Update on your Rock Martial Arts coach application",
    )


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
    role_login_links: Optional[dict] = None,
) -> bool:
    """Send partner admin invite (create password) via Resend, with webhook fallback."""
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    from_email = _resend_from_header()
    to_clean = (to_email or "").strip()
    if not to_clean:
        logger.error("Partner invite skipped: empty recipient")
        return False

    display = (partner_name or "Partner").strip()
    links = role_login_links or {}
    admin_login = links.get("admin") or login_link
    bm_login = links.get("branch_manager") or ""
    coach_login = links.get("coach") or ""
    student_login = links.get("student") or ""
    role_links_html = f"""
      <p style="margin:16px 0 8px;color:#6b7280;font-size:12px;">Partner login pages:</p>
      <table style="width:100%;border-collapse:collapse;margin:0 0 8px;font-size:12px;background:#f3f4f6;border-radius:6px;">
        <tr><td style="padding:8px 10px;color:#6b7280;white-space:nowrap;">Admin</td><td style="padding:8px 10px;word-break:break-all;"><a href="{admin_login}" style="color:#111827;">{admin_login}</a></td></tr>
        <tr><td style="padding:8px 10px;color:#6b7280;white-space:nowrap;">Branch Admin</td><td style="padding:8px 10px;word-break:break-all;"><a href="{bm_login}" style="color:#111827;">{bm_login}</a></td></tr>
        <tr><td style="padding:8px 10px;color:#6b7280;white-space:nowrap;">Coach</td><td style="padding:8px 10px;word-break:break-all;"><a href="{coach_login}" style="color:#111827;">{coach_login}</a></td></tr>
        <tr><td style="padding:8px 10px;color:#6b7280;white-space:nowrap;">Student</td><td style="padding:8px 10px;word-break:break-all;"><a href="{student_login}" style="color:#111827;">{student_login}</a></td></tr>
      </table>
"""
    # ASCII hyphen in subject — some filters mishandle unicode dashes
    subject = f"Welcome to {display} - create your partner admin password"
    from utils.email_layout import ACCENT_PARTNER, wrap_rock_email_html
    import html as _html

    safe_display = _html.escape(display)
    safe_email = _html.escape(to_clean)
    safe_phone = _html.escape(phone or "—")
    safe_address = _html.escape(address or "—")
    safe_pay = _html.escape(f"{payment_method or '—'} / {payment_status or '—'}")
    safe_set = _html.escape(set_password_link, quote=True)
    safe_login = _html.escape(login_link, quote=True)
    content = f"""
      <p style="margin:0 0 16px 0;">Hello <strong>{safe_display}</strong>,</p>
      <p style="margin:0 0 16px 0;">Your partner organization is ready (Powered by Rock Martial Arts). Create your password to open the partner admin dashboard.</p>
      <table style="width:100%;border-collapse:collapse;margin:12px 0 8px;font-size:14px;">
        <tr><td style="padding:6px 0;color:#6b7280;">Partner</td><td style="padding:6px 0;"><b>{safe_display}</b></td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Email</td><td style="padding:6px 0;">{safe_email}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Phone</td><td style="padding:6px 0;">{safe_phone}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Address</td><td style="padding:6px 0;">{safe_address}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Payment</td><td style="padding:6px 0;">{safe_pay}</td></tr>
      </table>
      <p style="margin:16px 0 6px 0;color:#6b7280;font-size:12px;">Or paste this link:</p>
      <p style="margin:0 0 12px;color:#111827;font-size:12px;word-break:break-all;background:#f3f4f6;padding:10px;border-radius:6px;">{safe_set}</p>
      <p style="margin:0 0 6px;color:#6b7280;font-size:12px;">Admin login after setup:</p>
      <p style="margin:0 0 8px;color:#111827;font-size:12px;word-break:break-all;background:#f3f4f6;padding:10px;border-radius:6px;">{safe_login}</p>
      {role_links_html}
      <p style="margin:16px 0 0;color:#9ca3af;font-size:12px;">This invite link expires in 7 days.</p>
    """
    html = wrap_rock_email_html(
        content_html=content,
        eyebrow="Partner Admin Invite",
        cta_label="Create password",
        cta_url=set_password_link,
        accent=ACCENT_PARTNER,
        preheader=f"Create your password for {display}",
    )
    plain = (
        f"Hello {display},\n\n"
        f"Your partner organization has been created.\n"
        f"Create your password here (expires in 7 days):\n{set_password_link}\n\n"
        f"Admin login after setup:\n{login_link}\n"
    )

    if api_key:
        payload = {
            "from": from_email,
            "to": [to_clean],
            "subject": subject,
            "html": html,
            "text": plain,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers=_resend_headers(api_key),
                    json=payload,
                )
                if 200 <= resp.status_code < 300:
                    msg_id = ""
                    try:
                        msg_id = (resp.json() or {}).get("id") or ""
                    except Exception:
                        pass
                    logger.info(
                        "Partner invite email sent via Resend to %s (status=%d id=%s)",
                        to_clean,
                        resp.status_code,
                        msg_id,
                    )
                    return True
                logger.error(
                    "Resend API error sending partner invite to %s: HTTP %d: %s",
                    to_clean,
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as exc:
            logger.exception("Failed to send partner invite via Resend to %s: %s", to_clean, exc)
    else:
        logger.warning(
            "RESEND_API_KEY not configured. Trying webhook fallback for partner invite to %s",
            to_clean,
        )

    # Fallback when Resend is missing or rejected the send
    ok = await _send_via_email_webhook(
        to_email=to_clean,
        subject=subject,
        html_message=html,
        plain_message=plain,
    )
    if not ok:
        logger.error(
            "Partner invite NOT delivered to %s. Manual link: %s",
            to_clean,
            set_password_link,
        )
    return ok

