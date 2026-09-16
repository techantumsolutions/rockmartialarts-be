from typing import Optional

from fastapi import APIRouter, Depends, Header

from controllers.cart_controller import CartController
from models.cart_models import (
    CartEnsureBody,
    CartItemCreate,
    CartItemUpdate,
    CartMultiCourseAddBody,
    CartStudentCreate,
    CartStudentsBulkCreate,
    CartStudentUpdate,
)
from models.cart_checkout_models import CartCheckoutConfirmBody
from utils.unified_auth import get_optional_current_user_or_superadmin

router = APIRouter()


@router.get("/current")
async def get_current_cart(
    guest_token: Optional[str] = None,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Get or create the active enrollment cart for the student account or guest token."""
    token = (guest_token or x_guest_cart_token or "").strip() or None
    return await CartController.get_current(current_user=current_user, guest_token=token)


@router.post("/current/ensure")
async def ensure_cart(
    body: CartEnsureBody,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    token = (body.guest_token or x_guest_cart_token or "").strip() or None
    return await CartController.get_current(current_user=current_user, guest_token=token)


@router.post("/current/students/bulk")
async def add_cart_students_bulk(
    body: CartStudentsBulkCreate,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Add multiple students to the enrollment cart in one request."""
    return await CartController.add_students_bulk(
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/current/students")
async def add_cart_student(
    body: CartStudentCreate,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.add_student(
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.patch("/current/students/{student_line_id}")
async def update_cart_student(
    student_line_id: str,
    body: CartStudentUpdate,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.update_student(
        student_line_id,
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.delete("/current/students/{student_line_id}")
async def remove_cart_student(
    student_line_id: str,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.remove_student(
        student_line_id,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/current/items/multi")
async def add_cart_items_multi(
    body: CartMultiCourseAddBody,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Add multiple courses for one student at one branch."""
    return await CartController.add_multi_courses(
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/current/items")
async def add_cart_item(
    body: CartItemCreate,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.add_item(
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.patch("/current/items/{item_id}")
async def update_cart_item(
    item_id: str,
    body: CartItemUpdate,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.update_item(
        item_id,
        body,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.delete("/current/items/{item_id}")
async def remove_cart_item(
    item_id: str,
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.remove_item(
        item_id,
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/current/validate")
async def validate_cart(
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    return await CartController.validate_current(
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/current/checkout/prepare")
async def prepare_cart_checkout(
    x_guest_cart_token: Optional[str] = Header(None, alias="X-Guest-Cart-Token"),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Create pending enrollments + Razorpay order for the active cart (student auth required)."""
    from controllers.cart_checkout_controller import CartCheckoutController

    return await CartCheckoutController.prepare(
        current_user=current_user,
        guest_token=(x_guest_cart_token or "").strip() or None,
    )


@router.post("/checkout/confirm")
async def confirm_cart_checkout(
    body: CartCheckoutConfirmBody,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Verify Razorpay payment and fulfill all cart enrollments (idempotent)."""
    from controllers.cart_checkout_controller import CartCheckoutController

    return await CartCheckoutController.confirm(body, current_user=current_user)


@router.get("/checkout/{checkout_id}")
async def get_cart_checkout(
    checkout_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    from controllers.cart_checkout_controller import CartCheckoutController

    return await CartCheckoutController.get_checkout(checkout_id, current_user=current_user)
