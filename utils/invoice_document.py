"""Printable HTML invoice document (M07-S01-T04)."""
from html import escape
from typing import Any, Dict, List, Optional


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


def build_invoice_html(invoice: Dict[str, Any]) -> str:
    """Build a self-contained printable HTML invoice."""
    company = invoice.get("company") or {}
    customer = invoice.get("customer") or {}
    items: List[Dict[str, Any]] = list(invoice.get("line_items") or [])
    currency = invoice.get("currency") or "INR"
    invoice_number = _text(invoice.get("invoice_number"), "DRAFT")
    paid_at = invoice.get("paid_at") or invoice.get("created_at")
    paid_label = ""
    if paid_at:
        try:
            paid_label = paid_at.strftime("%d %b %Y, %I:%M %p") if hasattr(paid_at, "strftime") else str(paid_at)
        except Exception:
            paid_label = str(paid_at)

    rows = []
    for idx, item in enumerate(items, start=1):
        desc = item.get("description") or item.get("course_name") or f"Line {idx}"
        student = item.get("student_label") or ""
        branch = item.get("branch_name") or ""
        detail_bits = [b for b in [student, branch] if b]
        detail = f"<div class='muted'>{escape(' · '.join(detail_bits))}</div>" if detail_bits else ""
        rows.append(
            f"""
            <tr>
              <td>{idx}</td>
              <td><strong>{_text(desc)}</strong>{detail}</td>
              <td class="num">{_money(item.get("course_fee"), currency)}</td>
              <td class="num">{_money(item.get("admission_fee"), currency)}</td>
              <td class="num">{_money(item.get("discount_amount"), currency)}</td>
              <td class="num">{_money(item.get("line_total"), currency)}</td>
            </tr>
            """
        )

    if not rows:
        rows.append(
            "<tr><td colspan='6' style='text-align:center;padding:16px;'>No line items</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Invoice {invoice_number}</title>
  <style>
    :root {{ --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --accent:#b45309; }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      margin: 0;
      padding: 32px;
      background: #fff;
    }}
    .sheet {{ max-width: 860px; margin: 0 auto; }}
    .top {{ display:flex; justify-content:space-between; gap:24px; margin-bottom:28px; }}
    .brand h1 {{ margin:0; font-size:28px; color: var(--accent); letter-spacing:0.02em; }}
    .brand p {{ margin:6px 0 0; color: var(--muted); font-size:13px; }}
    .meta {{ text-align:right; font-size:14px; }}
    .meta strong {{ display:block; font-size:18px; margin-bottom:6px; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:24px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
    .card h3 {{ margin:0 0 8px; font-size:12px; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); }}
    .card p {{ margin:2px 0; font-size:14px; }}
    table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; font-size:13px; vertical-align:top; }}
    th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted); }}
    .num {{ text-align:right; white-space:nowrap; }}
    .muted {{ color: var(--muted); font-size:12px; margin-top:4px; font-family: system-ui, sans-serif; }}
    .totals {{ margin-top:20px; width:320px; margin-left:auto; }}
    .totals div {{ display:flex; justify-content:space-between; padding:6px 0; font-size:14px; }}
    .totals .grand {{ border-top:2px solid var(--ink); margin-top:8px; padding-top:10px; font-weight:700; font-size:16px; }}
    .footer {{ margin-top:36px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; font-family: system-ui, sans-serif; }}
    @media print {{
      body {{ padding: 12px; }}
      .no-print {{ display:none !important; }}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="top">
      <div class="brand">
        <h1>{_text(company.get("name"), "Rock Martial Arts")}</h1>
        <p>{_text(company.get("address"), "")}</p>
        <p>{_text(company.get("email"), "")} {_text(company.get("phone"), "")}</p>
      </div>
      <div class="meta">
        <strong>TAX INVOICE</strong>
        <div>Invoice No: {invoice_number}</div>
        <div>Date: {_text(paid_label)}</div>
        <div>Payment Ref: {_text(invoice.get("payment_reference"))}</div>
        <div>Method: {_text(invoice.get("payment_gateway_label") or invoice.get("payment_method"))}</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Bill To</h3>
        <p><strong>{_text(customer.get("name"), "Customer")}</strong></p>
        <p>{_text(customer.get("email"), "")}</p>
        <p>{_text(customer.get("phone"), "")}</p>
      </div>
      <div class="card">
        <h3>Summary</h3>
        <p>Currency: {_text(currency)}</p>
        <p>Status: {_text(str(invoice.get("status") or "issued").title())}</p>
        <p>Lines: {len(items)}</p>
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Description</th>
          <th class="num">Course</th>
          <th class="num">Admission</th>
          <th class="num">Discount</th>
          <th class="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>

    <div class="totals">
      <div><span>Subtotal (courses)</span><span>{_money(invoice.get("subtotal_amount"), currency)}</span></div>
      <div><span>Admission</span><span>{_money(invoice.get("admission_total"), currency)}</span></div>
      <div><span>Discount</span><span>- {_money(invoice.get("discount_total"), currency)}</span></div>
      <div><span>Tax</span><span>{_money(invoice.get("tax_total"), currency)}</span></div>
      <div class="grand"><span>Total Paid</span><span>{_money(invoice.get("total_amount"), currency)}</span></div>
    </div>

    <div class="footer">
      This is a computer-generated invoice for a successful payment.
      Keep this reference for your records. For support, contact your branch.
    </div>
  </div>
</body>
</html>
"""
    return html
