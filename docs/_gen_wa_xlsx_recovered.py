from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
import os

BASE = "https://rockmartialartsacademy.com"

rows = [
    (
        "Student self registration – Welcome",
        "W1 · rock_student_welcome_self",
        "Open dashboard",
        f"{BASE}/student-dashboard",
        "Hello {{student_name}}!\n\nWelcome to {{academy_name}}. Your registration is complete.\n\nWe're excited to have you on the mat!\n— {{academy_name}}",
    ),
    (
        "Student created by admin – Welcome + set password",
        "W2 · rock_student_welcome_admin",
        "Set password",
        f"{BASE}/reset-password?token={{token}}",
        "Hello {{student_name}}!\n\nYour student account has been created at {{academy_name}}.\nBranch: {{branch_name}}\n\nTap the button below to set your password and activate your dashboard.\nThis link expires in {{expiry_hours}} hours.\nA copy has also been sent to your email.\n\n— {{academy_name}}",
    ),
    (
        "Student – Set password reminder",
        "W3 · rock_student_password_setup",
        "Set password",
        f"{BASE}/reset-password?token={{token}}",
        "Hello {{student_name}}!\n\nSet your password to activate your {{academy_name}} student account.\nThis link expires in {{expiry_hours}} hours.\n\nIf you did not expect this message, please contact the academy.\n— {{academy_name}}",
    ),
    (
        "Family account – New student profile added",
        "W4 · rock_family_profile_added",
        "",
        "",
        'Hello {{parent_name}}!\n\nA new student profile "{{student_name}}" has been added under your {{academy_name}} family account.\n\nYou can switch profiles anytime from the student dashboard.\n— {{academy_name}}',
    ),
    (
        "Student account activated – Login",
        "W5 · rock_student_account_ready",
        "Login",
        f"{BASE}/login",
        "Hello {{student_name}}!\n\nYour {{academy_name}} account is ready.\nSee you in class!\n— {{academy_name}}",
    ),
    (
        "Payment successful",
        "W6 · rock_payment_success",
        "",
        "",
        "Hello {{student_name}}!\n\nPayment of ₹{{amount}} for {{course_or_plan}} was successful on {{payment_date}}.\nTransaction ID: {{txn_id}}\n\nThank you!\n— {{academy_name}}",
    ),
    (
        "Payment failed",
        "W7 · rock_payment_failed",
        "Retry payment",
        f"{BASE}/student-dashboard/payments",
        "Hello {{student_name}}!\n\nYour payment of ₹{{amount}} could not be completed.\nReason: {{failure_reason}}\n\nTap below to retry.\nNeed help? Contact {{academy_name}}.",
    ),
    (
        "Invoice sent",
        "W8 · rock_invoice_issued",
        "View invoice",
        f"{BASE}/student-dashboard/invoices/{{invoice_id}}",
        "Hello {{student_name}}!\n\nYour invoice {{invoice_no}} for ₹{{amount}} is ready.\n\n— {{academy_name}}",
    ),
    (
        "Fee payment reminder",
        "W9 · rock_payment_reminder",
        "Pay now",
        f"{BASE}/student-dashboard/payments",
        "Hello {{student_name}}!\n\nFriendly reminder: ₹{{amount}} is due on {{due_date}} for your {{academy_name}} fees.\n\n— {{academy_name}}",
    ),
    (
        "Course enrollment confirmed",
        "W10 · rock_enrollment_confirm",
        "",
        "",
        "Hello {{student_name}}!\n\nYou are enrolled in {{course_name}} at {{branch_name}}.\nStart date: {{start_date}}\n\nSee you at {{academy_name}}!",
    ),
    (
        "Course / plan renewal reminder",
        "W11 · rock_renewal_reminder",
        "Renew now",
        f"{BASE}/student-dashboard/courses",
        "Hello {{student_name}}!\n\nYour {{course_name}} plan at {{academy_name}} ends on {{end_date}}.\n\nRenew to continue training without interruption.\n— {{academy_name}}",
    ),
    (
        "Course / plan expired",
        "W12 · rock_enrollment_expired",
        "Renew now",
        f"{BASE}/student-dashboard/courses",
        "Hello {{student_name}}!\n\nYour {{course_name}} enrollment at {{academy_name}} has expired.\n\nRenew to continue.\n— {{academy_name}}",
    ),
    (
        "Class schedule updated",
        "W13 · rock_course_schedule_update",
        "",
        "",
        "Hello {{student_name}}!\n\nSchedule update for {{course_name}}:\n{{new_schedule}}\n\n— {{academy_name}}",
    ),
    (
        "Create coach – Welcome",
        "W14 · rock_coach_welcome",
        "",
        "",
        "Hello {{coach_name}}!\n\nYour coach account at {{academy_name}} has been created. Please check your email for login / password setup instructions.\n\n— {{academy_name}}",
    ),
    (
        "Coach application approved",
        "W15 · rock_coach_approved",
        "Set password",
        f"{BASE}/coach/reset-password?token={{token}}",
        "Hello {{coach_name}}!\n\nGreat news — your coach application at {{academy_name}} is approved.\n\nTap below to set your password.\n— {{academy_name}}",
    ),
    (
        "Coach application rejected",
        "W16 · rock_coach_rejected",
        "",
        "",
        "Hello {{coach_name}}!\n\nThank you for applying to {{academy_name}}. We are unable to approve your application at this time.\n\nReason: {{rejection_reason}}\n\n— {{academy_name}}",
    ),
    (
        "Coach – Set password",
        "W17 · rock_coach_password_setup",
        "Set password",
        f"{BASE}/coach/reset-password?token={{token}}",
        "Hello {{coach_name}}!\n\nSet your {{academy_name}} coach password.\nLink expires in {{expiry_hours}} hours.\n— {{academy_name}}",
    ),
    (
        "Coach – New lead assigned",
        "W18 · rock_coach_lead_assigned",
        "",
        "",
        "Hello {{coach_name}}!\n\nNew lead assigned:\nName: {{lead_name}}\nPhone: {{lead_phone}}\nSource: {{lead_source}}\n\nPlease follow up soon.\n— {{academy_name}}",
    ),
    (
        "Coach – Batch / students updated",
        "W19 · rock_coach_batch_update",
        "",
        "",
        "Hello {{coach_name}}!\n\nUpdate for {{course_name}}:\n{{change_summary}}\n\n— {{academy_name}}",
    ),
    (
        "Create branch manager – Invitation",
        "W20 · rock_bm_welcome",
        "Set password",
        f"{BASE}/branch-manager/login",
        "Hello {{bm_name}}!\n\nYou are invited as Branch Manager for {{branch_name}} at {{academy_name}}.\n\nTap below to set your password.\n— {{academy_name}}",
    ),
    (
        "Branch manager – New student registration",
        "W21 · rock_bm_new_registration",
        "",
        "",
        "Hello {{bm_name}}!\n\nNew registration:\nStudent: {{student_name}}\nCourse: {{course_name}}\nAmount: ₹{{amount}}\n\n— {{academy_name}}",
    ),
    (
        "Branch manager – New lead",
        "W22 · rock_bm_lead_alert",
        "",
        "",
        "Hello {{bm_name}}!\n\nNew lead for your branch:\nName: {{lead_name}}\nPhone: {{lead_phone}}\nSource: {{lead_source}}\n\n— {{academy_name}}",
    ),
    (
        "Super admin – New student registration alert",
        "W23 · rock_sa_new_registration",
        "",
        "",
        "New registration at {{academy_name}}:\nStudent: {{student_name}}\nBranch: {{branch_name}}\nAmount: ₹{{amount}}",
    ),
    (
        "Low stock alert",
        "W24 · rock_stock_low_alert",
        "",
        "",
        "Low stock alert ({{academy_name}}):\nItem: {{item_name}}\nQty left: {{quantity}}\nBranch: {{branch_name}}\n\nPlease restock soon.",
    ),
    (
        "Create partner – Invitation + set password",
        "W25 · rock_partner_invite",
        "Create password",
        f"{BASE}/partners/{{partner_slug}}/set-password?token={{token}}",
        'Hello!\n\nYour partner organization "{{partner_name}}" is ready (Powered by Rock Martial Arts).\n\nCreate your password to get started.',
    ),
    (
        "Partner onboarding payment successful",
        "W26 · rock_partner_payment_success",
        "",
        "",
        'Payment successful for partner "{{partner_name}}".\nAmount: ₹{{amount}}\nTxn: {{txn_id}}\n\nWelcome aboard — Powered by Rock Martial Arts.',
    ),
    (
        "Partner onboarding payment failed",
        "W27 · rock_partner_payment_failed",
        "Retry payment",
        f"{BASE}/partners/{{partner_slug}}/login",
        'Payment for partner "{{partner_name}}" could not be completed.\n\nTap below to retry.',
    ),
    (
        "Partner account activated",
        "W28 · rock_partner_activated",
        "Login",
        f"{BASE}/partners/{{partner_slug}}/login",
        'Good news! Partner "{{partner_name}}" is now ACTIVE.',
    ),
    (
        "Partner account deactivated",
        "W29 · rock_partner_deactivated",
        "",
        "",
        'Partner "{{partner_name}}" has been deactivated.\n\nFor help, contact: {{support_contact}}',
    ),
    (
        "Partner – New student joined",
        "W30 · rock_partner_new_student",
        "",
        "",
        "Hello {{partner_admin_name}}!\n\nNew student under your partner account:\n{{student_name}} | Branch: {{branch_name}} | Course: {{course_name}}",
    ),
    (
        "Partner – Student payment received",
        "W31 · rock_partner_payment_received",
        "",
        "",
        "Hello {{partner_admin_name}}!\n\nPayment received:\nStudent: {{student_name}}\nAmount: ₹{{amount}}\nInvoice: {{invoice_no}}",
    ),
    (
        "Website lead – Thank you to customer",
        "W32 · rock_lead_received_ack",
        "",
        "",
        "Hello {{lead_name}}!\n\nThanks for contacting {{academy_name}}. Our team will reach out shortly.",
    ),
    (
        "Website lead – Alert to staff",
        "W33 · rock_lead_staff_alert",
        "",
        "",
        "New lead ({{academy_name}}):\nName: {{lead_name}}\nPhone: {{lead_phone}}\nSource: {{lead_source}}\nBranch: {{branch_name}}",
    ),
    (
        "Demo request – Thank you to customer",
        "W34 · rock_demo_request_ack",
        "",
        "",
        "Hello {{lead_name}}!\n\nWe received your demo request for {{preferred_date}} at {{academy_name}}. Our team will confirm soon.",
    ),
    (
        "Demo request – Alert to staff",
        "W35 · rock_demo_staff_alert",
        "",
        "",
        "Demo request ({{academy_name}}):\nName: {{lead_name}}\nPhone: {{lead_phone}}\nBranch: {{branch_name}}\nPreferred date: {{preferred_date}}",
    ),
    (
        "Training request – Thank you to customer",
        "W36 · rock_training_request_ack",
        "",
        "",
        "Hello {{lead_name}}!\n\nThanks for your training request at {{academy_name}}. We will get back to you soon.",
    ),
    (
        "Training request – Alert to staff",
        "W37 · rock_training_request_staff",
        "",
        "",
        "Training request ({{academy_name}}):\nName: {{lead_name}}\nPhone: {{lead_phone}}\nDetails: {{request_details}}",
    ),
    (
        "Callback request – Thank you to customer",
        "W38 · rock_callback_request_ack",
        "",
        "",
        "Hello {{lead_name}}!\n\nWe received your callback request for {{academy_name}}. Our team will call you soon.",
    ),
    (
        "Callback request – Alert to staff",
        "W39 · rock_callback_staff_alert",
        "",
        "",
        "Callback request ({{academy_name}}):\nName: {{lead_name}}\nPhone: {{lead_phone}}",
    ),
    (
        "Camp registration – Confirmation to customer",
        "W40 · rock_camp_confirm",
        "",
        "",
        "Hello {{registrant_name}}!\n\nYou are registered for {{camp_name}} ({{camp_dates}}).\n\nSee you at camp!\n— {{academy_name}}",
    ),
    (
        "Camp registration – Alert to staff",
        "W41 · rock_camp_staff_alert",
        "",
        "",
        "New camp registration ({{academy_name}}):\nName: {{registrant_name}}\nCamp: {{camp_name}}\nPhone: {{registrant_phone}}",
    ),
    (
        "Event registration – Confirmation to customer",
        "W42 · rock_event_confirm",
        "",
        "",
        "Hello {{registrant_name}}!\n\nYou are registered for {{event_name}}.\nDate: {{event_date}}\nVenue: {{event_venue}}\n\n— {{academy_name}}",
    ),
    (
        "Event reminder to customer",
        "W43 · rock_event_reminder",
        "",
        "",
        "Hello {{registrant_name}}!\n\nReminder: {{event_name}} is on {{event_date}} at {{event_venue}}.\n\n— {{academy_name}}",
    ),
    (
        "Partner website lead – Thank you to customer",
        "W44 · rock_collab_partner_lead_ack",
        "",
        "",
        "Hello {{lead_name}}!\n\nThanks for contacting {{partner_branch_name}} (Powered by Rock Martial Arts). We will reach out shortly.",
    ),
    (
        "Partner website lead – Alert to staff",
        "W45 · rock_collab_partner_lead_staff",
        "",
        "",
        "New partner-page lead for {{partner_branch_name}}:\nName: {{lead_name}}\nPhone: {{lead_phone}}",
    ),
]

wb = Workbook()
ws = wb.active
ws.title = "WhatsApp Templates"

headers = ["Title", "Template Code", "Button Label", "Button URL", "Template"]
ws.append(headers)

header_fill = PatternFill("solid", fgColor="1F4E79")
header_font = Font(bold=True, color="FFFFFF")
wrap = Alignment(wrap_text=True, vertical="top")
thin = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

for col in range(1, 6):
    cell = ws.cell(1, col)
    cell.fill = header_fill
    cell.font = header_font

for r in rows:
    ws.append(list(r))

for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=5):
    for cell in row:
        cell.alignment = wrap
        cell.border = thin

ws.column_dimensions["A"].width = 48
ws.column_dimensions["B"].width = 42
ws.column_dimensions["C"].width = 18
ws.column_dimensions["D"].width = 75
ws.column_dimensions["E"].width = 70

for i in range(2, ws.max_row + 1):
    ws.row_dimensions[i].height = 95

ws.auto_filter.ref = f"A1:E{ws.max_row}"
ws.freeze_panes = "A2"

ws2 = wb.create_sheet("Notes")
ws2.append(["Notes for client / Meta setup"])
ws2.append([f"All button URLs use domain: {BASE}/"])
ws2.append(["Dynamic parts like {{token}}, {{invoice_id}}, {{partner_slug}} are filled when the message is sent."])
ws2.append([
    "W25 optional second button (if Meta allows 2 URL buttons): "
    f"Label=Login, URL={BASE}/partners/{{{{partner_slug}}}}/login"
])
ws2.column_dimensions["A"].width = 120

out = os.path.join(os.path.dirname(__file__), "WhatsApp_Templates_Client_Approval.xlsx")
wb.save(out)
print(out)
print("count", len(rows))
