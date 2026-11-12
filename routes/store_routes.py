from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException

from controllers.store_controller import StoreController
from models.store_models import (
    StoreCategoryCreate,
    StoreCategoryUpdate,
    StoreProductCreate,
    StoreProductUpdate,
    StoreProductStatusUpdate,
    StorePaymentOrderRequest,
    StoreOrderCreate,
    OrderFulfillmentUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import (
    get_current_user_or_superadmin,
    get_optional_current_user_or_superadmin,
    require_role_unified,
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Categories
# -----------------------------------------------------------------------------
@router.get("/categories")
async def get_categories(active_only: bool = Query(False)):
    """List store categories with product counts."""
    return await StoreController.get_categories(active_only=active_only)


@router.post("/categories")
async def create_category(
    payload: StoreCategoryCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: create a new store category."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.create_category(payload, current_user)


@router.put("/categories/{category_id}")
async def update_category(
    category_id: str,
    payload: StoreCategoryUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: update store category."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.update_category(category_id, payload, current_user)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: delete store category."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.delete_category(category_id, current_user)


# -----------------------------------------------------------------------------
# Products
# -----------------------------------------------------------------------------
@router.get("/products")
async def get_products(
    category_id: Optional[str] = Query(None),
    category_slug: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Get store products. Rock students see active products; Super Admin sees all."""
    return await StoreController.get_products(
        category_id=category_id,
        category_slug=category_slug,
        is_active=is_active,
        search=search,
        current_user=current_user,
    )


@router.get("/products/{product_id}")
async def get_product(
    product_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Get single product details."""
    return await StoreController.get_product(product_id, current_user=current_user)


@router.post("/products")
async def create_product(
    payload: StoreProductCreate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: create a store product."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.create_product(payload, current_user)


@router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    payload: StoreProductUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: update product details."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.update_product(product_id, payload, current_user)


@router.patch("/products/{product_id}/status")
async def toggle_product_status(
    product_id: str,
    payload: StoreProductStatusUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: toggle product active/inactive."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.toggle_product_status(product_id, payload.is_active, current_user)


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: str,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: delete product."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.delete_product(product_id, current_user)


# -----------------------------------------------------------------------------
# Orders & Student Purchases
# -----------------------------------------------------------------------------
@router.post("/create-payment-order")
async def create_payment_order(
    payload: StorePaymentOrderRequest,
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """Rock Student: initialize Razorpay order with server-backed order_id."""
    return await StoreController.create_payment_order(payload, current_user)


@router.post("/orders")
async def create_order(
    payload: StoreOrderCreate,
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """Rock Student: buy product (auto-binds student details, no delivery form required)."""
    return await StoreController.create_order(payload, current_user)


@router.get("/orders/my-orders")
async def get_my_orders(
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """Rock Student: view purchased products and handover status."""
    return await StoreController.get_student_orders(current_user)


@router.get("/admin/orders")
async def get_admin_orders(
    fulfillment_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: view all store orders with student details and fulfillment status."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.get_admin_orders(
        fulfillment_status=fulfillment_status,
        search=search,
        current_user=current_user,
    )


@router.patch("/admin/orders/{order_id}/fulfillment")
async def update_order_fulfillment(
    order_id: str,
    payload: OrderFulfillmentUpdate,
    current_user: dict = Depends(require_role_unified([UserRole.SUPER_ADMIN])),
):
    """Super Admin: update 'Given to Student' status."""
    if current_user.get("role") == "partner_super_admin" or current_user.get("partner_org_id"):
        raise HTTPException(status_code=403, detail="Store management is only available for Rock Super Admin")
    return await StoreController.update_order_fulfillment(order_id, payload, current_user)

