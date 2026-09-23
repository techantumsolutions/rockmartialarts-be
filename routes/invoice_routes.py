from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from controllers.invoice_controller import InvoiceController
from models.user_models import UserRole
from utils.unified_auth import require_role_unified

router = APIRouter()


class InvoiceWhatsAppResendBody(BaseModel):
    phone_override: Optional[str] = Field(
        None, description="Optional alternate phone; defaults to invoice customer phone."
    )
    force: bool = False


@router.get("")
async def list_invoices(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    payment_id: Optional[str] = None,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await InvoiceController.list_invoices(
        current_user,
        skip=skip,
        limit=limit,
        search=search,
        payment_id=payment_id,
    )


@router.get("/by-payment/{payment_id}")
async def get_invoice_by_payment(
    payment_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await InvoiceController.get_by_payment(payment_id, current_user)


@router.get("/{invoice_id}/document", response_class=HTMLResponse)
async def get_invoice_document(
    invoice_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await InvoiceController.get_document_html(invoice_id, current_user)


@router.get("/{invoice_id}/whatsapp/deliveries")
async def get_invoice_whatsapp_deliveries(
    invoice_id: str,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    """M07-S05: WhatsApp delivery history for an invoice."""
    return await InvoiceController.get_whatsapp_deliveries(invoice_id, current_user)


@router.post("/{invoice_id}/whatsapp/resend")
async def resend_invoice_whatsapp(
    invoice_id: str,
    body: Optional[InvoiceWhatsAppResendBody] = None,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    """M07-S05: resend invoice on WhatsApp without creating a new invoice."""
    payload = body or InvoiceWhatsAppResendBody()
    return await InvoiceController.resend_whatsapp(
        invoice_id,
        current_user,
        phone_override=payload.phone_override,
    )


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    include_html: bool = False,
    current_user: dict = Depends(
        require_role_unified(
            [UserRole.SUPER_ADMIN, UserRole.BRANCH_MANAGER, UserRole.STUDENT]
        )
    ),
):
    return await InvoiceController.get_invoice(
        invoice_id, current_user, include_html=include_html
    )
