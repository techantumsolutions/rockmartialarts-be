"""M07-S01 invoice list / detail / printable document APIs."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.invoice_service import (
    ensure_invoice_indexes,
    generate_invoice_for_payment,
    public_invoice_payload,
)


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


def _managed_branch_ids(user: dict) -> List[str]:
    raw = (user or {}).get("managed_branches") or (user or {}).get("branch_ids") or []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw if x]


class InvoiceController:
    @staticmethod
    async def list_invoices(
        current_user: dict,
        *,
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        payment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        await ensure_invoice_indexes(db)

        skip = max(0, int(skip or 0))
        limit = max(1, min(int(limit or 50), 200))
        role = _role(current_user)
        query: Dict[str, Any] = {}

        if role == UserRole.STUDENT.value:
            uid = current_user["id"]
            query["$or"] = [
                {"student_id": uid},
                {"account_user_id": uid},
            ]
        elif role == UserRole.BRANCH_MANAGER.value:
            branches = _managed_branch_ids(current_user)
            if not branches:
                return {"invoices": [], "total": 0, "skip": skip, "limit": limit}
            query["branch_id"] = {"$in": branches}
        elif role != UserRole.SUPER_ADMIN.value:
            raise HTTPException(status_code=403, detail="Not allowed to list invoices")

        if payment_id:
            query["payment_id"] = payment_id

        if search and search.strip():
            s = search.strip()
            search_clause = {
                "$or": [
                    {"invoice_number": {"$regex": s, "$options": "i"}},
                    {"payment_reference": {"$regex": s, "$options": "i"}},
                    {"customer.name": {"$regex": s, "$options": "i"}},
                    {"customer.email": {"$regex": s, "$options": "i"}},
                    {"customer.phone": {"$regex": s, "$options": "i"}},
                ]
            }
            if "$or" in query:
                query = {"$and": [{"$or": query["$or"]}, search_clause]}
            else:
                query.update(search_clause)

        total = await db.invoices.count_documents(query)
        cursor = db.invoices.find(query).sort("created_at", -1).skip(skip).limit(limit)
        rows = await cursor.to_list(limit)
        items = []
        for row in rows:
            ser = serialize_doc(row)
            items.append(
                {
                    "id": ser.get("id"),
                    "invoice_number": ser.get("invoice_number"),
                    "status": ser.get("status"),
                    "total_amount": ser.get("total_amount"),
                    "currency": ser.get("currency") or "INR",
                    "paid_at": ser.get("paid_at"),
                    "customer_name": (ser.get("customer") or {}).get("name") or "",
                    "payment_id": ser.get("payment_id"),
                    "cart_checkout_id": ser.get("cart_checkout_id"),
                    "payment_reference": ser.get("payment_reference"),
                    "line_count": len(ser.get("line_items") or []),
                    "created_at": ser.get("created_at"),
                    "source": ser.get("source"),
                }
            )
        return {"invoices": items, "total": total, "skip": skip, "limit": limit}

    @staticmethod
    async def get_invoice(invoice_id: str, current_user: dict, *, include_html: bool = False) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await db.invoices.find_one({"id": invoice_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        InvoiceController._assert_can_access(doc, current_user)
        payload = public_invoice_payload(doc, include_html=include_html)
        try:
            from utils.invoice_whatsapp_service import (
                list_invoice_whatsapp_deliveries,
                public_whatsapp_delivery_summary,
            )

            deliveries = await list_invoice_whatsapp_deliveries(invoice_id, limit=10)
            latest = deliveries[0] if deliveries else None
            if latest:
                payload["whatsapp_delivery"] = public_whatsapp_delivery_summary(latest)
            payload["whatsapp_deliveries"] = [
                public_whatsapp_delivery_summary(d) for d in deliveries if d
            ]
        except Exception:
            pass
        return {"invoice": payload}

    @staticmethod
    async def get_whatsapp_deliveries(invoice_id: str, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await db.invoices.find_one({"id": invoice_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        InvoiceController._assert_can_access(doc, current_user)
        from utils.invoice_whatsapp_service import (
            list_invoice_whatsapp_deliveries,
            public_whatsapp_delivery_summary,
        )

        rows = await list_invoice_whatsapp_deliveries(invoice_id, limit=50)
        return {
            "invoice_id": invoice_id,
            "invoice_number": doc.get("invoice_number"),
            "deliveries": [public_whatsapp_delivery_summary(r) for r in rows],
        }

    @staticmethod
    async def resend_whatsapp(invoice_id: str, current_user: dict, *, phone_override: Optional[str] = None) -> Dict[str, Any]:
        """M07-S05-T04: resend without generating a new invoice."""
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await db.invoices.find_one({"id": invoice_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        InvoiceController._assert_can_resend_whatsapp(doc, current_user)

        from utils.invoice_whatsapp_service import (
            public_whatsapp_delivery_summary,
            resolve_invoice_phone,
            send_invoice_whatsapp,
        )

        phone = resolve_invoice_phone(serialize_doc(doc), phone_override)
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="No mobile number on this invoice. Update the student profile phone and try again.",
            )

        delivery = await send_invoice_whatsapp(
            serialize_doc(doc),
            trigger="resend",
            phone_override=phone_override,
            actor_user_id=current_user.get("id"),
            force=True,
        )
        status = str(delivery.get("status") or "")
        if status == "failed":
            raise HTTPException(
                status_code=502,
                detail=delivery.get("error") or "WhatsApp provider failed to send invoice",
            )
        return {
            "invoice_id": invoice_id,
            "invoice_number": doc.get("invoice_number"),
            "delivery_id": delivery.get("id"),
            "status": delivery.get("status"),
            "phone_masked": delivery.get("phone_masked"),
            "attempt": delivery.get("attempt"),
            "whatsapp_delivery": public_whatsapp_delivery_summary(delivery),
            "message": "Invoice resent on WhatsApp",
        }

    @staticmethod
    def _assert_can_resend_whatsapp(doc: dict, current_user: dict) -> None:
        role = _role(current_user)
        if role == UserRole.SUPER_ADMIN.value:
            return
        if role == UserRole.BRANCH_MANAGER.value:
            InvoiceController._assert_can_access(doc, current_user)
            return
        if role == UserRole.STUDENT.value:
            # Students may resend to their own invoice phone
            InvoiceController._assert_can_access(doc, current_user)
            return
        raise HTTPException(status_code=403, detail="Not allowed to resend invoice WhatsApp")

    @staticmethod
    async def get_by_payment(payment_id: str, current_user: dict) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await db.invoices.find_one({"payment_id": payment_id})
        if not doc:
            # Best-effort generate if payment is paid (covers historical rows)
            pay = await db.payments.find_one({"id": payment_id})
            if pay and str(pay.get("payment_status") or "").lower() in {"paid", "completed"}:
                InvoiceController._assert_can_access_payment(pay, current_user)
                doc = await generate_invoice_for_payment(payment_doc=pay)
            if not doc:
                raise HTTPException(status_code=404, detail="Invoice not found for this payment")
        InvoiceController._assert_can_access(doc, current_user)
        payload = public_invoice_payload(doc, include_html=False)
        try:
            from utils.invoice_whatsapp_service import (
                list_invoice_whatsapp_deliveries,
                public_whatsapp_delivery_summary,
            )

            deliveries = await list_invoice_whatsapp_deliveries(str(doc.get("id")), limit=5)
            if deliveries:
                payload["whatsapp_delivery"] = public_whatsapp_delivery_summary(deliveries[0])
        except Exception:
            pass
        return {"invoice": payload}

    @staticmethod
    async def get_document_html(invoice_id: str, current_user: dict) -> HTMLResponse:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        doc = await db.invoices.find_one({"id": invoice_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        InvoiceController._assert_can_access(doc, current_user)
        html = doc.get("document_html")
        if not html:
            from utils.invoice_document import build_invoice_html

            html = build_invoice_html(serialize_doc(doc))
            await db.invoices.update_one(
                {"id": invoice_id},
                {"$set": {"document_html": html, "updated_at": datetime.utcnow()}},
            )
        return HTMLResponse(content=html, media_type="text/html; charset=utf-8")

    @staticmethod
    def _assert_can_access(doc: dict, current_user: dict) -> None:
        role = _role(current_user)
        if role == UserRole.SUPER_ADMIN.value:
            return
        uid = current_user.get("id")
        if role == UserRole.STUDENT.value:
            if doc.get("student_id") == uid or doc.get("account_user_id") == uid:
                return
            raise HTTPException(status_code=403, detail="Not allowed to view this invoice")
        if role == UserRole.BRANCH_MANAGER.value:
            branches = _managed_branch_ids(current_user)
            if doc.get("branch_id") and doc.get("branch_id") in branches:
                return
            raise HTTPException(status_code=403, detail="Not allowed to view this invoice")
        raise HTTPException(status_code=403, detail="Not allowed")

    @staticmethod
    def _assert_can_access_payment(pay: dict, current_user: dict) -> None:
        role = _role(current_user)
        if role == UserRole.SUPER_ADMIN.value:
            return
        uid = current_user.get("id")
        if role == UserRole.STUDENT.value and (
            pay.get("student_id") == uid or pay.get("user_id") == uid
        ):
            return
        if role == UserRole.BRANCH_MANAGER.value:
            return  # generation will still scope later list by branch
        raise HTTPException(status_code=403, detail="Not allowed")
