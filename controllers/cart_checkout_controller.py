"""M05-S05 cart checkout prepare + confirm (Razorpay) → fulfillment."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException

from models.cart_checkout_models import (
    CartCheckoutConfirmBody,
    CartCheckoutDocument,
    CartCheckoutEnrollmentLink,
    CartCheckoutStatus,
)
from models.cart_models import CartStatus
from models.user_models import UserRole
from utils.cart_fulfillment import (
    create_pending_enrollment_for_item,
    fulfill_cart_checkout,
    fulfillment_key_for_checkout,
    resolve_student_id_for_line,
)
from utils.cart_validation import validate_cart_document
from utils.database import get_db
from utils.discount_engine import compute_cart_promotions_for_cart
from utils.razorpay_client import get_razorpay_client, verify_razorpay_signature

logger = logging.getLogger(__name__)


class CartCheckoutController:
    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    async def prepare(
        *,
        current_user: dict,
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        """Validate cart, create pending enrollments + Razorpay order for cart total."""
        if not current_user or current_user.get("role") != UserRole.STUDENT.value:
            raise HTTPException(
                status_code=401,
                detail="Please sign in as a student to checkout the enrollment cart.",
            )

        db = get_db()
        owner_id = current_user["id"]
        from controllers.cart_controller import CartController

        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        cart, changed = CartController._sanitize_cart_items(cart)
        cart = await CartController._persist_cart_if_sanitized(cart, changed)

        if cart.get("status") != CartStatus.ACTIVE.value:
            raise HTTPException(status_code=400, detail="This cart is no longer active")

        items = cart.get("items") or []
        students = cart.get("students") or []
        if not items:
            raise HTTPException(status_code=400, detail="Add at least one course before checkout")
        if not students:
            raise HTTPException(status_code=400, detail="Add at least one student before checkout")

        issues, items = await validate_cart_document(
            {**cart, "items": items},
            current_user=current_user,
            reprice_check=True,
        )
        blocking = [i for i in issues if i.code not in ("fee_drift",)]
        if blocking:
            raise HTTPException(status_code=400, detail=blocking[0].message)

        cart["items"] = items
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": items, "updated_at": CartCheckoutController._now()}},
        )

        promo = await compute_cart_promotions_for_cart(cart)
        amount_inr = float(promo.total_amount)
        if amount_inr <= 0:
            raise HTTPException(status_code=400, detail="Checkout amount must be greater than zero")
        amount_paise = int(round(amount_inr * 100))
        if amount_paise < 100:
            raise HTTPException(status_code=400, detail="Amount too small for online payment")

        # Cancel prior unfinished checkouts for this cart
        await db.cart_checkouts.update_many(
            {
                "cart_id": cart["id"],
                "status": CartCheckoutStatus.PENDING_PAYMENT.value,
            },
            {
                "$set": {
                    "status": CartCheckoutStatus.CANCELLED.value,
                    "updated_at": CartCheckoutController._now(),
                    "cancel_reason": "superseded_by_new_prepare",
                }
            },
        )

        checkout_id = str(uuid.uuid4())
        students_by_line = {s.get("student_line_id"): s for s in students}
        enrollment_links = []
        line_student_ids: Dict[str, str] = {}

        for item in items:
            sid_line = item.get("student_line_id")
            line = students_by_line.get(sid_line) or {"label": "Student", "student_line_id": sid_line}
            if sid_line not in line_student_ids:
                line_student_ids[sid_line] = await resolve_student_id_for_line(
                    db, line=line, owner=current_user
                )
                # Persist resolved student_id back onto cart line for clarity
                for s in students:
                    if s.get("student_line_id") == sid_line and not s.get("student_id"):
                        s["student_id"] = line_student_ids[sid_line]

            student_id = line_student_ids[sid_line]
            item_with_cart = {**item, "_cart_id": cart["id"]}
            enrollment = await create_pending_enrollment_for_item(
                db,
                checkout_id=checkout_id,
                item=item_with_cart,
                student_id=student_id,
                student_label=(line.get("label") or "Student"),
            )
            enrollment_links.append(
                CartCheckoutEnrollmentLink(
                    cart_item_id=item["id"],
                    student_line_id=sid_line,
                    student_id=student_id,
                    enrollment_id=enrollment["id"],
                    course_id=item["course_id"],
                    branch_id=item["branch_id"],
                    course_name=item.get("course_name") or "",
                    branch_name=item.get("branch_name") or "",
                    student_label=(line.get("label") or "Student"),
                ).dict()
            )

        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"students": students, "updated_at": CartCheckoutController._now()}},
        )

        discount_snapshot = promo.to_snapshot()
        checkout_doc = CartCheckoutDocument(
            id=checkout_id,
            cart_id=cart["id"],
            owner_user_id=owner_id,
            students_snapshot=students,
            items_snapshot=items,
            totals={
                "subtotal_amount": promo.subtotal_amount,
                "promo_discount_total": promo.promo_discount_total,
                "total_amount": promo.total_amount,
                "item_count": len(items),
                "student_count": len(students),
                "currency": "INR",
            },
            discount_snapshot=discount_snapshot,
            enrollment_links=enrollment_links,
            amount_inr=amount_inr,
            fulfillment_key=fulfillment_key_for_checkout(checkout_id),
        )
        payload = checkout_doc.dict()
        # enrollment_links already dicts in list above — pydantic may wrap; normalize
        payload["enrollment_links"] = enrollment_links

        client = get_razorpay_client()
        try:
            order = client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "payment_capture": 1,
                    "notes": {
                        "cart_checkout_id": checkout_id[:40],
                        "cart_id": str(cart["id"])[:40],
                        "student_id": str(owner_id)[:40],
                        "source": "enrollment_cart",
                    },
                }
            )
        except Exception:
            logger.exception("Razorpay order.create failed cart_checkout_id=%s", checkout_id)
            raise HTTPException(
                status_code=502,
                detail="We could not reach the payment service. Please try again in a moment.",
            )

        now = CartCheckoutController._now()
        payload["razorpay_order_id"] = order["id"]
        payload["updated_at"] = now

        payment_id = str(uuid.uuid4())
        payment_doc = {
            "id": payment_id,
            "user_id": owner_id,
            "student_id": owner_id,
            "enrollment_id": enrollment_links[0]["enrollment_id"] if enrollment_links else None,
            "cart_checkout_id": checkout_id,
            "cart_id": cart["id"],
            "amount": amount_inr,
            "payment_type": "course_fee",
            "payment_method": "digital_wallet",
            "payment_status": "pending",
            "status": "initiated",
            "transaction_id": None,
            "razorpay_payment_id": None,
            "razorpay_order_id": order["id"],
            "payment_date": None,
            "due_date": now + timedelta(days=30),
            "notes": f"Enrollment cart checkout {checkout_id}; items={len(items)}; promo={promo.promo_discount_total}",
            "discount_snapshot": discount_snapshot,
            "created_at": now,
            "updated_at": now,
        }
        await db.payments.insert_one(payment_doc)
        payload["payment_id"] = payment_id

        await db.cart_checkouts.insert_one(payload)
        await db.carts.update_one(
            {"id": cart["id"]},
            {
                "$set": {
                    "discount_snapshot": discount_snapshot,
                    "pending_checkout_id": checkout_id,
                    "updated_at": now,
                }
            },
        )

        key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        return {
            "cart_checkout_id": checkout_id,
            "amount": amount_inr,
            "currency": "INR",
            "item_count": len(items),
            "student_count": len(students),
            "promo_discount_total": promo.promo_discount_total,
            "discount_breakdown": promo.discount_lines,
            "enrollment_ids": [l["enrollment_id"] for l in enrollment_links],
            "order": {
                "id": order["id"],
                "amount": int(order["amount"]),
                "currency": order.get("currency") or "INR",
            },
            "key": key_id,
        }

    @staticmethod
    async def confirm(
        body: CartCheckoutConfirmBody,
        *,
        current_user: dict,
    ) -> Dict[str, Any]:
        if not current_user or current_user.get("role") != UserRole.STUDENT.value:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        checkout = await db.cart_checkouts.find_one({"id": body.cart_checkout_id})
        if not checkout:
            raise HTTPException(status_code=404, detail="Cart checkout not found")
        if checkout.get("owner_user_id") != current_user["id"]:
            raise HTTPException(status_code=403, detail="This checkout does not belong to you")

        if checkout.get("status") == CartCheckoutStatus.FULFILLED.value:
            invoice = None
            try:
                from utils.invoice_service import safe_generate_invoice_for_payment

                invoice = await safe_generate_invoice_for_payment(
                    cart_checkout_id=body.cart_checkout_id
                )
            except Exception:
                pass
            try:
                from utils.billing_cycle_service import safe_upsert_billing_cycles_for_payment

                await safe_upsert_billing_cycles_for_payment(
                    cart_checkout_id=body.cart_checkout_id
                )
            except Exception:
                pass
            out = {
                "message": "Checkout already completed",
                "already_fulfilled": True,
                "cart_checkout_id": body.cart_checkout_id,
                "enrollment_ids": [l.get("enrollment_id") for l in (checkout.get("enrollment_links") or [])],
            }
            if invoice and invoice.get("id"):
                out["invoice_id"] = invoice.get("id")
                out["invoice_number"] = invoice.get("invoice_number")
            return out

        if not verify_razorpay_signature(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        ):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        if checkout.get("razorpay_order_id") and checkout["razorpay_order_id"] != body.razorpay_order_id:
            raise HTTPException(status_code=400, detail="Payment order does not match this checkout")

        expected_paise = int(round(float(checkout.get("amount_inr") or 0) * 100))
        # Soft amount check via payment row
        pay = await db.payments.find_one(
            {"cart_checkout_id": body.cart_checkout_id, "razorpay_order_id": body.razorpay_order_id}
        )
        if pay and expected_paise < 100:
            raise HTTPException(status_code=400, detail="Invalid checkout amount")

        result = await fulfill_cart_checkout(
            body.cart_checkout_id,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_order_id=body.razorpay_order_id,
            actor="cart_confirm",
        )
        return {
            "message": "Payment verified and enrollments activated",
            **result,
        }

    @staticmethod
    async def get_checkout(
        checkout_id: str,
        *,
        current_user: Optional[dict],
    ) -> Dict[str, Any]:
        db = get_db()
        checkout = await db.cart_checkouts.find_one({"id": checkout_id})
        if not checkout:
            raise HTTPException(status_code=404, detail="Cart checkout not found")
        if current_user and current_user.get("role") == UserRole.STUDENT.value:
            if checkout.get("owner_user_id") != current_user.get("id"):
                raise HTTPException(status_code=403, detail="Not allowed")
        # Strip internal noise
        checkout.pop("_id", None)
        return {"checkout": checkout}
