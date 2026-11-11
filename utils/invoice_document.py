"""Printable HTML invoice document with Rock Martial Arts & Partner branding."""
import base64
from html import escape
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RMA_LOGO_CACHE: Optional[str] = None


def get_rock_martial_arts_logo_data_uri() -> str:
    """Return the base64 data URI of the official Rock Martial Arts logo."""
    global _RMA_LOGO_CACHE
    if _RMA_LOGO_CACHE:
        return _RMA_LOGO_CACHE

    be_root = Path(__file__).resolve().parent.parent  # rockmartialarts-be
    candidates = [
        be_root / "static" / "logo.png",
        be_root.parent / "rockmartialarts-fe" / "public" / "logo.png",
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = p.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                _RMA_LOGO_CACHE = f"data:image/png;base64,{b64}"
                return _RMA_LOGO_CACHE
            except Exception:
                pass
    return ""


def resolve_logo_to_data_uri(logo_url: Optional[str]) -> Optional[str]:
    """Convert local upload/asset paths to data URIs so invoices render offline & in print."""
    if not logo_url:
        return None
    url = str(logo_url).strip()
    if not url:
        return None
    if url.startswith("data:") or url.startswith("http://") or url.startswith("https://"):
        return url

    be_root = Path(__file__).resolve().parent.parent
    clean_path = url.lstrip("/\\")
    candidates = [
        be_root / clean_path,
        be_root / "static" / clean_path,
        be_root.parent / "rockmartialarts-fe" / "public" / clean_path,
    ]
    for c in candidates:
        if c.is_file():
            try:
                ext = c.suffix.lower()
                mime = "image/png"
                if ext in (".jpg", ".jpeg"):
                    mime = "image/jpeg"
                elif ext == ".webp":
                    mime = "image/webp"
                elif ext == ".svg":
                    mime = "image/svg+xml"
                data = c.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{b64}"
            except Exception:
                pass
    return url


def number_to_indian_rupees(amount: float) -> str:
    """Convert numeric currency value to Indian words representation (Lakhs, Crores)."""
    try:
        n = int(round(float(amount or 0)))
    except (TypeError, ValueError):
        n = 0

    if n <= 0:
        return "Zero Rupees Only"

    units = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _below_thousand(num: int) -> str:
        parts = []
        if num >= 100:
            parts.append(units[num // 100] + " Hundred")
            num %= 100
        if num >= 20:
            parts.append(tens[num // 10])
            num %= 10
        if num > 0:
            parts.append(units[num])
        return " ".join(p for p in parts if p)

    parts = []
    crores = n // 10000000
    n %= 10000000
    if crores:
        parts.append(_below_thousand(crores) + " Crore")
    lakhs = n // 100000
    n %= 100000
    if lakhs:
        parts.append(_below_thousand(lakhs) + " Lakh")
    thousands = n // 1000
    n %= 1000
    if thousands:
        parts.append(_below_thousand(thousands) + " Thousand")
    if n:
        parts.append(_below_thousand(n))

    result = " ".join(p for p in parts if p).strip()
    return f"{result} Rupees Only" if result else "Zero Rupees Only"


def _money(value: float, currency: str = "INR") -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if (currency or "INR").upper() == "INR":
        return f"₹{amount:,.2f}"
    return f"{currency} {amount:,.2f}"


def _text(value: Optional[str], fallback: str = "—") -> str:
    text = (value or "").strip()
    return escape(text) if text else fallback


async def resolve_invoice_company_branding(db, invoice_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolves company/organization branding for an invoice:
    - If partner invoice: returns partner organization's logo, name, and contact.
    - If Rock Martial Arts invoice: returns Rock Martial Arts logo, name, and branch details.
    """
    partner_org_id = str(invoice_doc.get("partner_org_id") or "").strip()
    branch_id = str(invoice_doc.get("branch_id") or "").strip()

    line_items = list(invoice_doc.get("line_items") or [])
    if not branch_id or not partner_org_id:
        for item in line_items:
            if not partner_org_id and item.get("partner_org_id"):
                partner_org_id = str(item.get("partner_org_id")).strip()
            if not branch_id and item.get("branch_id"):
                branch_id = str(item.get("branch_id")).strip()

    branch_doc = None
    if branch_id and db is not None:
        try:
            branch_doc = await db.branches.find_one({"id": branch_id})
            if branch_doc and not partner_org_id and branch_doc.get("partner_org_id"):
                partner_org_id = str(branch_doc.get("partner_org_id")).strip()
        except Exception:
            pass

    student_id = invoice_doc.get("student_id") or invoice_doc.get("account_user_id")
    if student_id and not partner_org_id and db is not None:
        try:
            student_doc = await db.users.find_one({"id": student_id})
            if student_doc and student_doc.get("partner_org_id"):
                partner_org_id = str(student_doc.get("partner_org_id")).strip()
        except Exception:
            pass

    payment_id = invoice_doc.get("payment_id")
    if payment_id and not partner_org_id and db is not None:
        try:
            pay_doc = await db.payments.find_one({"id": payment_id})
            if pay_doc and pay_doc.get("partner_org_id"):
                partner_org_id = str(pay_doc.get("partner_org_id")).strip()
        except Exception:
            pass

    # 1. Partner organization tenant invoice
    if partner_org_id and db is not None:
        try:
            org = await db.partner_organizations.find_one({"id": partner_org_id})
            branding = await db.partner_org_branding.find_one({"partner_org_id": partner_org_id})
            media = (branding or {}).get("media") or {}
            footer = (branding or {}).get("footer") or {}
            contact = (branding or {}).get("contact_page") or {}

            raw_logo = media.get("logo_url") or footer.get("logo_url") or (org or {}).get("logo_url")
            logo_uri = resolve_logo_to_data_uri(raw_logo) if raw_logo else None

            partner_name = (
                (org or {}).get("name")
                or (branding or {}).get("content", {}).get("tagline")
                or "Partner Organization"
            )
            return {
                "name": partner_name,
                "logo_url": logo_uri,
                "is_partner": True,
                "partner_org_id": partner_org_id,
                "branch_name": (branch_doc or {}).get("name") or "",
                "email": (org or {}).get("email") or contact.get("email"),
                "phone": (org or {}).get("phone") or contact.get("phone"),
                "address": (org or {}).get("address") or contact.get("address"),
            }
        except Exception as e:
            logger.warning("Error resolving partner branding for org %s: %s", partner_org_id, e)

    # 2. Collaboration partner branch invoice
    if (
        branch_doc
        and (branch_doc.get("is_collaboration_partner") or branch_doc.get("allows_collaboration"))
        and db is not None
    ):
        try:
            collab = await db.collaboration_partner_branding.find_one({"branch_id": branch_id})
            collab_media = (collab or {}).get("media") or {}
            raw_collab_logo = collab_media.get("logo_url")
            if raw_collab_logo:
                return {
                    "name": branch_doc.get("name") or "Partner Academy",
                    "logo_url": resolve_logo_to_data_uri(raw_collab_logo),
                    "is_partner": True,
                    "branch_name": branch_doc.get("name"),
                    "email": branch_doc.get("email"),
                    "phone": branch_doc.get("phone"),
                    "address": branch_doc.get("address"),
                }
        except Exception as e:
            logger.warning("Error resolving collab partner branding: %s", e)

    # 3. Default: Rock Martial Arts Academy
    rma_logo = get_rock_martial_arts_logo_data_uri()
    branch_name = (branch_doc or {}).get("name") or ""
    branch_address = (branch_doc or {}).get("address") or ""
    return {
        "name": "Rock Martial Arts Academy",
        "logo_url": rma_logo,
        "is_partner": False,
        "branch_name": branch_name,
        "email": (branch_doc or {}).get("email") or "contact@rockmartialarts.com",
        "phone": (branch_doc or {}).get("phone") or None,
        "address": branch_address or "Balaji Nagar, Hyderabad",
    }


def build_invoice_html(invoice: Dict[str, Any]) -> str:
    """
    Build a self-contained, beautifully styled printable HTML tax invoice.
    Uses the Rock Martial Arts official logo or Partner logo, avoids dummy / duplicate data,
    and supports high-res printing and PDF conversion.
    """
    company = invoice.get("company") or {}
    customer = invoice.get("customer") or {}
    items: List[Dict[str, Any]] = list(invoice.get("line_items") or [])
    currency = invoice.get("currency") or "INR"
    invoice_number = _text(invoice.get("invoice_number"), "DRAFT")

    is_partner = bool(company.get("is_partner"))
    company_name = company.get("name") or ("Partner Academy" if is_partner else "Rock Martial Arts Academy")

    # Logo resolution: Use company logo_url, or load default RMA logo for non-partner
    logo_url = company.get("logo_url")
    if not logo_url and not is_partner:
        logo_url = get_rock_martial_arts_logo_data_uri()

    paid_at = invoice.get("paid_at") or invoice.get("created_at")
    paid_label = ""
    if paid_at:
        try:
            paid_label = (
                paid_at.strftime("%d %b %Y, %I:%M %p")
                if hasattr(paid_at, "strftime")
                else str(paid_at)
            )
        except Exception:
            paid_label = str(paid_at)

    customer_name = (customer.get("name") or "Customer").strip()
    customer_email = (customer.get("email") or "").strip()
    customer_phone = (customer.get("phone") or "").strip()
    customer_address = (customer.get("address") or "").strip()

    # Determine whether line items have admission or discount fees > 0
    has_admission = any(float(i.get("admission_fee") or 0) > 0 for i in items)
    has_discount = any(float(i.get("discount_amount") or 0) > 0 for i in items)

    # Collect distinct branches from items or company
    branches = []
    if company.get("branch_name"):
        branches.append(str(company.get("branch_name")).strip())
    for it in items:
        b = str(it.get("branch_name") or "").strip()
        if b and b not in branches:
            branches.append(b)
    primary_branch = branches[0] if branches else "Balaji Nagar"

    rows = []
    for idx, item in enumerate(items, start=1):
        desc = item.get("description") or item.get("course_name") or f"Item {idx}"
        student = str(item.get("student_label") or "").strip()
        branch = str(item.get("branch_name") or "").strip()

        # Deduplicate student name: only show student subtitle if beneficiary differs from Bill To
        detail_bits = []
        if student and student.lower() != customer_name.lower():
            detail_bits.append(f"Student: {escape(student)}")
        if not branch and primary_branch:
            branch = primary_branch

        detail_html = (
            f"<div class='item-sub'>{ ' · '.join(detail_bits) }</div>" if detail_bits else ""
        )

        course_fee_val = item.get("course_fee")
        line_total_val = item.get("line_total") if item.get("line_total") is not None else course_fee_val

        # Dynamic columns based on non-zero fees
        extra_cells = ""
        if has_admission:
            adm_val = float(item.get("admission_fee") or 0)
            extra_cells += f"<td class='num'>{_money(adm_val, currency)}</td>"
        if has_discount:
            disc_val = float(item.get("discount_amount") or 0)
            extra_cells += (
                f"<td class='num disc-text'>-{_money(disc_val, currency)}</td>"
                if disc_val > 0
                else f"<td class='num'>—</td>"
            )

        fee_cell = (
            f"<td class='num'>{_money(course_fee_val, currency)}</td>"
            if (has_admission or has_discount)
            else ""
        )

        rows.append(
            f"""
            <tr>
              <td class="col-idx">{idx}</td>
              <td class="col-desc">
                <div class="item-name">{_text(desc)}</div>
                {detail_html}
              </td>
              <td class="col-branch">{_text(branch)}</td>
              {fee_cell}
              {extra_cells}
              <td class="num col-total"><strong>{_money(line_total_val, currency)}</strong></td>
            </tr>
            """
        )

    if not rows:
        colspan = 4 + (1 if (has_admission or has_discount) else 0) + (1 if has_admission else 0) + (1 if has_discount else 0)
        rows.append(
            f"<tr><td colspan='{colspan}' style='text-align:center;padding:24px;color:#64748b;'>No items on this invoice</td></tr>"
        )

    # Dynamic headers
    header_fee_cell = "<th class='num'>Course Fee</th>" if (has_admission or has_discount) else ""
    header_adm_cell = "<th class='num'>Admission</th>" if has_admission else ""
    header_disc_cell = "<th class='num'>Discount</th>" if has_discount else ""

    # Totals breakdown
    subtotal = float(invoice.get("subtotal_amount") or 0)
    admission_total = float(invoice.get("admission_total") or 0)
    discount_total = float(invoice.get("discount_total") or 0)
    tax_total = float(invoice.get("tax_total") or 0)
    total_amount = float(invoice.get("total_amount") or 0)
    if total_amount == 0 and subtotal > 0:
        total_amount = max(0.0, subtotal + admission_total - discount_total + tax_total)

    # Only include non-zero breakdown lines to eliminate dummy clutter
    totals_rows = []
    totals_rows.append(
        f"<div class='total-row'><span>Course Subtotal</span><span>{_money(subtotal, currency)}</span></div>"
    )
    if admission_total > 0:
        totals_rows.append(
            f"<div class='total-row'><span>Admission Fee</span><span>{_money(admission_total, currency)}</span></div>"
        )
    if discount_total > 0:
        totals_rows.append(
            f"<div class='total-row discount-row'><span>Promotional Discount</span><span>- {_money(discount_total, currency)}</span></div>"
        )
    if tax_total > 0:
        totals_rows.append(
            f"<div class='total-row'><span>Tax / GST</span><span>{_money(tax_total, currency)}</span></div>"
        )

    amount_in_words = number_to_indian_rupees(total_amount)

    # Logo HTML
    if logo_url:
        logo_html = f'<img src="{logo_url}" alt="{escape(company_name)}" class="brand-logo" />'
    else:
        # Fallback badge if no logo is available
        initials = "".join(w[:1] for w in company_name.split()[:2]).upper() or "RMA"
        logo_html = f'<div class="brand-badge-fallback">{escape(initials)}</div>'

    tagline = "Authorized Partner Organization" if is_partner else "Premier Martial Arts & Fitness Training"

    company_contact_bits = []
    if company.get("email"):
        company_contact_bits.append(escape(str(company.get("email"))))
    if company.get("phone"):
        company_contact_bits.append(escape(str(company.get("phone"))))
    company_contact_line = (
        f"<div class='brand-contact'>{' · '.join(company_contact_bits)}</div>"
        if company_contact_bits
        else ""
    )

    company_address_line = (
        f"<div class='brand-address'>{_text(company.get('address'), '')}</div>"
        if company.get("address")
        else ""
    )

    payment_method = _text(
        invoice.get("payment_gateway_label") or invoice.get("payment_method"), "Online / Verified"
    )
    payment_ref = _text(invoice.get("payment_reference"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Invoice {invoice_number} · {escape(company_name)}</title>
  <style>
    :root {{
      --slate-900: #0f172a;
      --slate-800: #1e293b;
      --slate-700: #334155;
      --slate-600: #475569;
      --slate-500: #64748b;
      --slate-200: #e2e8f0;
      --slate-100: #f1f5f9;
      --slate-50:  #f8fafc;
      --emerald-600: #059669;
      --emerald-700: #047857;
      --emerald-50:  #ecfdf5;
      --amber-500: #f59e0b;
      --line: #e2e8f0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: var(--slate-900);
      background: #f8fafc;
      margin: 0;
      padding: 32px 16px;
      -webkit-font-smoothing: antialiased;
    }}
    .sheet {{
      max-width: 840px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid var(--slate-200);
      border-radius: 12px;
      padding: 36px 40px;
      box-shadow: 0 10px 25px -5px rgba(0,0,0,0.04), 0 8px 10px -6px rgba(0,0,0,0.02);
    }}
    .top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 24px;
      padding-bottom: 24px;
      border-bottom: 2px solid var(--slate-100);
      margin-bottom: 24px;
    }}
    .brand-col {{
      flex: 1;
      min-width: 0;
    }}
    .brand-logo-wrap {{
      margin-bottom: 12px;
    }}
    .brand-logo {{
      max-height: 65px;
      max-width: 220px;
      height: auto;
      width: auto;
      object-fit: contain;
      display: block;
    }}
    .brand-badge-fallback {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 52px;
      height: 52px;
      border-radius: 10px;
      background: var(--slate-900);
      color: #ffffff;
      font-weight: 700;
      font-size: 18px;
      letter-spacing: 0.05em;
    }}
    .brand-name {{
      font-size: 18px;
      font-weight: 700;
      color: var(--slate-900);
      line-height: 1.3;
      margin-top: 4px;
    }}
    .brand-sub {{
      font-size: 12px;
      color: var(--slate-500);
      font-weight: 500;
      margin-top: 2px;
    }}
    .brand-contact, .brand-address {{
      font-size: 11px;
      color: var(--slate-500);
      margin-top: 3px;
    }}
    .meta-col {{
      text-align: right;
      flex-shrink: 0;
    }}
    .doc-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--emerald-50);
      color: var(--emerald-700);
      border: 1px solid #a7f3d0;
      border-radius: 9999px;
      padding: 4px 12px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .doc-badge .dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--emerald-600);
      display: inline-block;
    }}
    .doc-title {{
      font-size: 22px;
      font-weight: 800;
      color: var(--slate-900);
      letter-spacing: 0.04em;
      margin: 0 0 10px 0;
    }}
    .meta-table {{
      font-size: 12.5px;
      line-height: 1.6;
      color: var(--slate-600);
    }}
    .meta-table .inv-num {{
      font-weight: 700;
      color: var(--slate-900);
    }}
    .grid-cards {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--slate-50);
      border: 1px solid var(--slate-200);
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .card-label {{
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--slate-500);
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .card-title {{
      font-size: 14.5px;
      font-weight: 700;
      color: var(--slate-900);
      margin-bottom: 4px;
    }}
    .card-detail {{
      font-size: 12.5px;
      color: var(--slate-600);
      line-height: 1.5;
    }}
    .card-row {{
      display: flex;
      justify-content: space-between;
      font-size: 12.5px;
      padding: 3px 0;
      color: var(--slate-600);
    }}
    .card-row strong {{
      color: var(--slate-900);
      font-weight: 600;
    }}
    .card-row .status-paid {{
      color: var(--emerald-700);
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      border: 1px solid var(--slate-200);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 20px;
    }}
    th {{
      background: var(--slate-50);
      color: var(--slate-600);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 10px 14px;
      border-bottom: 1px solid var(--slate-200);
      text-align: left;
    }}
    td {{
      padding: 12px 14px;
      font-size: 13px;
      color: var(--slate-700);
      border-bottom: 1px solid var(--slate-100);
      vertical-align: middle;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    .col-idx {{
      width: 36px;
      color: var(--slate-500);
      font-weight: 600;
    }}
    .item-name {{
      font-weight: 600;
      color: var(--slate-900);
      font-size: 13.5px;
    }}
    .item-sub {{
      font-size: 11.5px;
      color: var(--slate-500);
      margin-top: 2px;
    }}
    .col-branch {{
      color: var(--slate-700);
      font-size: 12.5px;
    }}
    .num {{
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}
    .disc-text {{
      color: var(--emerald-700);
      font-weight: 600;
    }}
    .summary-section {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 24px;
      margin-top: 8px;
      padding-top: 8px;
    }}
    .words-box {{
      flex: 1;
      background: var(--slate-50);
      border: 1px dashed var(--slate-200);
      border-radius: 8px;
      padding: 12px 16px;
      max-width: 440px;
    }}
    .words-label {{
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--slate-500);
      margin-bottom: 4px;
    }}
    .words-value {{
      font-size: 12.5px;
      font-weight: 600;
      color: var(--slate-800);
      line-height: 1.4;
    }}
    .totals-box {{
      width: 320px;
      flex-shrink: 0;
      border: 1px solid var(--slate-200);
      border-radius: 8px;
      background: #ffffff;
      padding: 12px 16px;
    }}
    .total-row {{
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      color: var(--slate-600);
      padding: 4px 0;
    }}
    .discount-row {{
      color: var(--emerald-700);
      font-weight: 600;
    }}
    .grand-total {{
      border-top: 2px solid var(--slate-900);
      margin-top: 8px;
      padding-top: 8px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-size: 15px;
      font-weight: 700;
      color: var(--slate-900);
    }}
    .grand-total .amount {{
      font-size: 19px;
      color: var(--slate-900);
      font-weight: 800;
    }}
    .footer {{
      margin-top: 36px;
      padding-top: 16px;
      border-top: 1px solid var(--slate-200);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11.5px;
      color: var(--slate-500);
      line-height: 1.5;
    }}
    .footer-note {{
      max-width: 520px;
    }}
    .footer-brand {{
      text-align: right;
      font-weight: 600;
      color: var(--slate-700);
    }}
    @media print {{
      body {{
        padding: 0 !important;
        background: #ffffff !important;
      }}
      .sheet {{
        border: none !important;
        box-shadow: none !important;
        padding: 12mm 15mm !important;
        max-width: 100% !important;
      }}
      .no-print {{
        display: none !important;
      }}
      tr {{
        page-break-inside: avoid;
      }}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="top">
      <div class="brand-col">
        <div class="brand-logo-wrap">
          {logo_html}
        </div>
        <div class="brand-name">{escape(company_name)}</div>
        <div class="brand-sub">{escape(tagline)}</div>
        {company_contact_line}
        {company_address_line}
      </div>
      <div class="meta-col">
        <div class="doc-badge"><span class="dot"></span> PAID</div>
        <div class="doc-title">TAX INVOICE</div>
        <div class="meta-table">
          <div>Invoice No: <span class="inv-num">{invoice_number}</span></div>
          <div>Date: <strong>{_text(paid_label)}</strong></div>
          <div>Payment Ref: <strong>{payment_ref}</strong></div>
          <div>Method: <strong>{payment_method}</strong></div>
        </div>
      </div>
    </div>

    <div class="grid-cards">
      <div class="card">
        <div class="card-label">BILL TO / STUDENT</div>
        <div class="card-title">{_text(customer_name, "Customer")}</div>
        { f'<div class="card-detail">{escape(customer_email)}</div>' if customer_email else '' }
        { f'<div class="card-detail">{escape(customer_phone)}</div>' if customer_phone else '' }
        { f'<div class="card-detail">{escape(customer_address)}</div>' if customer_address else '' }
      </div>
      <div class="card">
        <div class="card-label">ENROLLMENT & RECEIPT</div>
        <div class="card-row"><span>Branch:</span> <strong>{_text(primary_branch)}</strong></div>
        <div class="card-row"><span>Payment Status:</span> <span class="status-paid">✔ Verified & Paid</span></div>
        <div class="card-row"><span>Currency:</span> <strong>{_text(currency)} (₹)</strong></div>
        { f'<div class="card-row"><span>Partner Org:</span> <strong>{escape(company_name)}</strong></div>' if is_partner else '' }
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th class="col-idx">#</th>
          <th>Description / Course</th>
          <th>Branch</th>
          {header_fee_cell}
          {header_adm_cell}
          {header_disc_cell}
          <th class="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>

    <div class="summary-section">
      <div class="words-box">
        <div class="words-label">Amount in Words</div>
        <div class="words-value">{escape(amount_in_words)}</div>
      </div>
      <div class="totals-box">
        {''.join(totals_rows)}
        <div class="grand-total">
          <span>Total Paid</span>
          <span class="amount">{_money(total_amount, currency)}</span>
        </div>
      </div>
    </div>

    <div class="footer">
      <div class="footer-note">
        This is a computer-generated tax invoice for a verified payment. No physical signature is required.
        Keep this invoice for your curriculum records. For inquiries, contact your branch.
      </div>
      <div class="footer-brand">
        {escape(company_name)}
      </div>
    </div>
  </div>
</body>
</html>
"""
    return html
