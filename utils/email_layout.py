"""
Shared Rock Martial Arts transactional email shell.

Minimal, clean dark-header card used by ops catalog + Resend builders.
"""
from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Optional


ACADEMY_NAME = "Rock Martial Arts Academy"
FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
)

# Brand tokens
HEADER_BG = "linear-gradient(135deg, #111827 0%, #1f2937 100%)"
PAGE_BG = "#f9fafb"
CARD_BG = "#ffffff"
TEXT = "#4b5563"
HEADING = "#111827"
MUTED = "#9ca3af"
ACCENT_RED = "#ef4444"
ACCENT_GOLD = "#FFB70F"
ACCENT_PARTNER = "#FFC403"


def _logo_url() -> str:
    explicit = (os.getenv("EMAIL_LOGO_URL") or "").strip()
    if explicit:
        return explicit
    base = (os.getenv("FRONTEND_URL") or os.getenv("NEXT_PUBLIC_SITE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/logo.png"
    return ""


def wrap_rock_email_html(
    *,
    content_html: str,
    eyebrow: str = "",
    title: str = "",
    cta_label: str = "",
    cta_url: str = "",
    accent: str = ACCENT_RED,
    footer_note: str = "This is an automated message from Rock Martial Arts Academy.",
    academy_name: str = ACADEMY_NAME,
    include_logo: bool = True,
    preheader: str = "",
) -> str:
    """Wrap inner HTML in the branded Rock Martial Arts card."""
    year = datetime.utcnow().year
    accent_safe = accent or ACCENT_RED
    cta_text_color = "#111827" if accent_safe.lower() in (
        ACCENT_GOLD.lower(),
        ACCENT_PARTNER.lower(),
        "#ffc403",
        "#ffb70f",
    ) else "#ffffff"

    logo = ""
    logo_src = _logo_url() if include_logo else ""
    if logo_src and logo_src.startswith("http"):
        logo = f"""
        <img src="{html.escape(logo_src, quote=True)}" alt="{html.escape(academy_name)}"
             width="56" height="56"
             style="display:block;margin:0 auto 14px;width:56px;height:56px;object-fit:contain;border:0;" />
        """

    eyebrow_html = ""
    if eyebrow:
        eyebrow_html = f"""
        <p style="margin:10px 0 0 0;color:{accent_safe};font-size:12px;font-weight:700;
                  text-transform:uppercase;letter-spacing:1.2px;">
          {html.escape(eyebrow)}
        </p>
        """

    title_html = ""
    if title:
        title_html = f"""
        <h2 style="margin:0 0 16px 0;color:{HEADING};font-size:20px;font-weight:600;">
          {html.escape(title)}
        </h2>
        """

    cta = ""
    if cta_label and cta_url:
        cta = f"""
        <div style="text-align:center;margin:28px 0 12px;">
          <a href="{html.escape(cta_url, quote=True)}"
             style="display:inline-block;background-color:{accent_safe};color:{cta_text_color};
                    font-size:15px;font-weight:700;text-decoration:none;padding:14px 28px;
                    border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.12);">
            {html.escape(cta_label)}
          </a>
        </div>
        """

    preheader_html = ""
    if preheader:
        preheader_html = f"""
        <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
          {html.escape(preheader)}
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta name="color-scheme" content="light"/>
  <title>{html.escape(academy_name)}</title>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BG};font-family:{FONT};color:{HEADING};">
  {preheader_html}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{PAGE_BG};padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;background:{CARD_BG};border-radius:12px;
                      overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.08);">
          <tr>
            <td style="background:{HEADER_BG};padding:28px 24px;text-align:center;">
              {logo}
              <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.6px;">
                {html.escape(academy_name.upper())}
              </div>
              {eyebrow_html}
            </td>
          </tr>
          <tr>
            <td style="padding:32px 28px;font-size:15px;line-height:1.65;color:{TEXT};">
              {title_html}
              {content_html}
              {cta}
            </td>
          </tr>
          <tr>
            <td style="background:{PAGE_BG};border-top:1px solid #e5e7eb;padding:20px 28px;text-align:center;">
              <p style="margin:0 0 6px 0;color:#6b7280;font-size:12px;">
                &copy; {year} {html.escape(academy_name)}. All rights reserved.
              </p>
              <p style="margin:0;color:{MUTED};font-size:11px;line-height:1.5;">
                {html.escape(footer_note)}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def plain_body_to_content_html(plain: str) -> str:
    """Escape plain text and convert newlines to <br> for email body."""
    return html.escape(plain or "").replace("\n", "<br>\n")
