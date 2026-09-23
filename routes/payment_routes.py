from fastapi import APIRouter, Depends, status, Query
from typing import Optional
from controllers.payment_controller import PaymentController
from models.student_models import (
    StudentPaymentCreate,
    ConfirmRazorpayPayment,
    PrepareStudentCheckoutBody,
    CreateStudentRazorpayOrderBody,
    RenewalQuoteBody,
    PrepareRenewalCheckoutBody,
)
from models.payment_models import RegistrationPaymentCreate, AdminPaymentRecoveryBody
from models.user_models import UserRole
from utils.auth import require_role
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin, get_optional_current_user_or_superadmin
from fastapi import Request, HTTPException
from utils.razorpay_reconciliation import verify_razorpay_webhook_signature

router = APIRouter()

@router.post("/students/payments", status_code=status.HTTP_201_CREATED)
async def student_process_payment(
    payment_data: StudentPaymentCreate,
    current_user: dict = Depends(require_role([UserRole.STUDENT]))
):
    return await PaymentController.student_process_payment(payment_data, current_user)


@router.post("/prepare-student-checkout", status_code=status.HTTP_200_OK)
async def prepare_student_checkout(
    body: PrepareStudentCheckoutBody,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Create pending enrollment and return amount + enrollment_id for Razorpay verification."""
    return await PaymentController.prepare_student_course_checkout(body, current_user)


@router.post("/students/razorpay/create-order", status_code=status.HTTP_200_OK)
async def student_create_razorpay_order(
    body: CreateStudentRazorpayOrderBody,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Create a Razorpay order for a pending enrollment (amount in paise from server-side INR total)."""
    return await PaymentController.create_student_razorpay_order(body, current_user)

@router.post("/student-checkout-quote", status_code=status.HTTP_200_OK)
async def student_checkout_quote(
    body: PrepareStudentCheckoutBody,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """Quote checkout amount for student dashboard without creating pending enrollment."""
    return await PaymentController.quote_student_course_checkout(body, current_user)


@router.post("/renewal-quote", status_code=status.HTTP_200_OK)
async def student_renewal_quote(
    body: RenewalQuoteBody,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """M07-S03: renewal quote with grace/overdue days and arrear placeholder."""
    return await PaymentController.quote_student_renewal(body, current_user)


@router.post("/prepare-student-renewal-checkout", status_code=status.HTTP_200_OK)
async def prepare_student_renewal_checkout(
    body: PrepareRenewalCheckoutBody,
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """M07-S04: prepare pending renewal checkout from eligible enrollment + quote."""
    return await PaymentController.prepare_student_renewal_checkout(body, current_user)


@router.get("/renewal-history", status_code=status.HTTP_200_OK)
async def student_renewal_history(
    enrollment_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role([UserRole.STUDENT])),
):
    """M07-S04: renewal history (old expiry, renewal date, arrears, resulting expiry)."""
    return await PaymentController.get_student_renewal_history(current_user, enrollment_id)


@router.post("/confirm-razorpay", status_code=status.HTTP_200_OK)
async def confirm_razorpay_payment(
    data: ConfirmRazorpayPayment,
    current_user: dict = Depends(require_role([UserRole.STUDENT]))
):
    """Record Razorpay payment and update enrollment (student-only)."""
    return await PaymentController.confirm_razorpay_payment(data, current_user)

@router.post("/process-registration", status_code=status.HTTP_201_CREATED)
async def process_registration_payment(payment_data: RegistrationPaymentCreate):
    """Process payment for student registration (public endpoint)"""
    return await PaymentController.process_registration_payment(payment_data)

@router.post("/renew-subscription", status_code=status.HTTP_200_OK)
async def renew_subscription(
    body: PrepareStudentCheckoutBody,
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Renew an existing subscription — reuses prepare-student-checkout logic
    which now allows renewal for expired enrollments."""
    return await PaymentController.prepare_student_course_checkout(body, current_user)

@router.get("/course-payment-info")
async def get_course_payment_info(
    course_id: str = Query(..., description="Course ID"),
    branch_id: str = Query(..., description="Branch ID"),
    duration: str = Query(..., description="Duration code"),
    batch_ref: Optional[str] = Query(None, description="Branch batch for per-batch pricing"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Get payment information for a course. Optional Bearer: logged-in students omit repeat admission fee."""
    optional_student_id = None
    if current_user and current_user.get("role") == "student":
        optional_student_id = current_user.get("id")
    return await PaymentController.get_course_payment_info(
        course_id,
        branch_id,
        duration,
        batch_ref=batch_ref,
        optional_student_id=optional_student_id,
    )

@router.get("/notifications")
async def get_payment_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Get payment notifications for superadmin dashboard"""
    return await PaymentController.get_payment_notifications(skip, limit)

@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Mark a payment notification as read"""
    return await PaymentController.mark_notification_read(notification_id)

@router.get("/stats")
async def get_payment_stats(
    start_date: Optional[str] = Query(None, description="Period start YYYY-MM-DD (inclusive)"),
    end_date: Optional[str] = Query(None, description="Period end YYYY-MM-DD (inclusive)"),
    branch_id: Optional[str] = Query(None, description="Filter by branch id (super admin)"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER, UserRole.COACH, UserRole.STUDENT]))
):
    """Get payment statistics for dashboard - Students get their own payment stats"""
    return await PaymentController.get_payment_stats(
        current_user, start_date=start_date, end_date=end_date, branch_id=branch_id
    )

@router.get("")
async def get_payments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None, description="Filter by branch id"),
    search: Optional[str] = Query(None, description="Search by student name/email/phone or transaction id"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER, UserRole.COACH, UserRole.STUDENT]))
):
    """Get payments with filtering - Students can only see their own payments"""
    return await PaymentController.get_payments(
        skip,
        limit,
        status,
        payment_type,
        current_user,
        start_date=start_date,
        end_date=end_date,
        search=search,
        branch_id=branch_id,
    )

@router.put("/{payment_id}/cancel")
async def cancel_payment_attempt(
    payment_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Cancel mistaken or pending payment attempts (super admin only)."""
    return await PaymentController.cancel_payment_attempt(payment_id, current_user)


@router.post("/{payment_id}/recover")
async def recover_cancelled_payment_attempt(
    payment_id: str,
    body: AdminPaymentRecoveryBody,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Restore admin-cancelled payment: pending checkout, mark received, or waive (super admin only)."""
    return await PaymentController.recover_cancelled_payment_attempt(payment_id, body, current_user)


@router.get("/export")
async def export_payments(
    status: Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    format: str = Query("csv", regex="^(csv|excel)$"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]))
):
    """Export payment reports"""
    return await PaymentController.export_payments(
        status, payment_type, branch_id, start_date, end_date, format, current_user
    )


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    """
    Razorpay webhook receiver (additive; does not change existing checkout flow).
    Requires `RAZORPAY_WEBHOOK_SECRET` and header `X-Razorpay-Signature`.
    """
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature") or ""
    if not verify_razorpay_webhook_signature(raw, sig):
        # Do not leak signature details
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    payload = await request.json()
    return await PaymentController.process_razorpay_webhook(payload, headers=dict(request.headers))


@router.post("/sync-razorpay")
async def sync_razorpay_payments(
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN]))
):
    """Admin-safe utility to reconcile Razorpay payments vs DB."""
    return await PaymentController.sync_razorpay_payments(current_user)


@router.post("/sync-razorpay/one")
async def sync_one_razorpay_payment(
    payment_id: Optional[str] = Query(None, description="Razorpay payment id (pay_...)"),
    order_id: Optional[str] = Query(None, description="Razorpay order id (order_...)"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Reconcile a single Razorpay payment/order against DB (super admin only)."""
    return await PaymentController.sync_one_razorpay_payment(current_user, payment_id=payment_id, order_id=order_id)


@router.get("/revenue/by-branch")
async def revenue_by_branch(
    start_date: Optional[str] = Query(None, description="Period start YYYY-MM-DD (inclusive)"),
    end_date: Optional[str] = Query(None, description="Period end YYYY-MM-DD (inclusive)"),
    branch_id: Optional[str] = Query(None, description="Filter by a single branch id"),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Branch-wise revenue totals for a period (super admin only)."""
    return await PaymentController.get_revenue_by_branch(
        current_user, start_date=start_date, end_date=end_date, branch_id=branch_id
    )
