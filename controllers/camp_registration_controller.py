import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException, UploadFile, status

from controllers.cms_residential_camp_controller import CMSResidentialCampController, _date_range
from controllers.upload_controller import ALLOWED_IMAGES, UPLOAD_ROOT, _safe_filename
from models.camp_registration_models import (
    CampRegistrationCreate,
    CampRegistrationEventSnapshot,
    CampRegistrationResponse,
    CampRegistrationSummary,
    CampRegVerifyPayment,
)
from utils.database import get_db, serialize_doc
from utils.razorpay_client import get_razorpay_client, verify_razorpay_signature

logger = logging.getLogger(__name__)


def _inr_to_paise(text: str) -> Optional[int]:
    cleaned = (text or "").replace(",", "")
    matches = re.findall(r"(\d+(?:\.\d+)?)", cleaned)
    if not matches:
        return None
    amount = max(float(m) for m in matches)
    paise = int(round(amount * 100))
    return paise if paise >= 100 else None


def _paise_to_inr(paise: int) -> str:
    rupees = int(round(paise / 100))
    return f"₹{rupees:,}"


def _pay_now_paise(event: Dict[str, Any]) -> int:
    total = _inr_to_paise(str(event.get("fee_total") or event.get("camp_fee") or ""))
    if total:
        half = int(round(total / 2))
        if half >= 100:
            return half
    pay_now = _inr_to_paise(str(event.get("fee_pay_now") or ""))
    if pay_now:
        return pay_now
    return 750000



def _dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _fact_value(facts: List[dict], label: str) -> str:
    want = label.strip().lower()
    for item in facts or []:
        if (item.get("label") or "").strip().lower() == want:
            return (item.get("value") or "").strip()
    return ""


class CampRegistrationController:
    COLLECTION = "camp_registrations"

    @staticmethod
    async def upload_screenshot(file: UploadFile) -> dict:
        content_type = file.content_type or ""
        if content_type not in ALLOWED_IMAGES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only image files (jpg, png, webp, gif) are allowed.",
            )
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Screenshot must be 5 MB or smaller.",
            )
        dest_dir = UPLOAD_ROOT / "camp-screenshots"
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(file.filename or "screenshot.png")
        dest_path = dest_dir / safe_name
        dest_path.write_bytes(data)
        file_url = f"/uploads/camp-screenshots/{safe_name}"
        return {"file_url": file_url, "url": file_url}

    @staticmethod
    async def create(payload: CampRegistrationCreate) -> CampRegistrationResponse:
        CampRegistrationController._validate(payload)
        cms = await CMSResidentialCampController.get_content()
        cms_data = _dump(cms)
        event = CampRegistrationController._snapshot_event(cms_data)
        now = datetime.utcnow()
        doc = _dump(payload)
        doc["event"] = _dump(event)
        payment = doc.get("payment") or {}
        payment["payment_mode"] = (payment.get("payment_mode") or "razorpay").strip() or "razorpay"
        payment["agree_policy"] = True
        doc["payment"] = payment
        doc["status"] = "pending_payment"
        doc["amount_paise"] = _pay_now_paise(_dump(event))
        doc["razorpay_order_id"] = None
        doc["razorpay_payment_id"] = None
        doc["created_at"] = now
        doc["updated_at"] = now
        db = get_db()
        result = await db[CampRegistrationController.COLLECTION].insert_one(doc)
        saved = await db[CampRegistrationController.COLLECTION].find_one({"_id": result.inserted_id})
        return CampRegistrationController._to_response(saved)

    @staticmethod
    async def list_registrations(
        skip: int = 0,
        limit: int = 25,
        search: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> dict:
        db = get_db()
        collection = db[CampRegistrationController.COLLECTION]
        query: Dict[str, Any] = {}
        if event_id and event_id.strip() and event_id != "all":
            query["event.event_id"] = event_id.strip()
        if search and search.strip():
            term = search.strip()
            query["$or"] = [
                {"participant.full_name": {"$regex": term, "$options": "i"}},
                {"participant.email": {"$regex": term, "$options": "i"}},
                {"participant.parent_guardian_mobile": {"$regex": term, "$options": "i"}},
                {"payment.transaction_id": {"$regex": term, "$options": "i"}},
                {"event.event_name": {"$regex": term, "$options": "i"}},
            ]
        total = await collection.count_documents(query)
        cursor = collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        rows = []
        async for doc in cursor:
            rows.append(CampRegistrationController._to_summary(doc))
        events = await CampRegistrationController._distinct_events()
        return {"items": rows, "total": total, "events": events}

    @staticmethod
    async def get_one(reg_id: str) -> CampRegistrationResponse:
        if not ObjectId.is_valid(reg_id):
            raise HTTPException(status_code=404, detail="Registration not found")
        db = get_db()
        doc = await db[CampRegistrationController.COLLECTION].find_one({"_id": ObjectId(reg_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Registration not found")
        return CampRegistrationController._to_response(doc)

    @staticmethod
    async def _load(reg_id: str) -> Dict[str, Any]:
        if not ObjectId.is_valid(reg_id):
            raise HTTPException(status_code=404, detail="Registration not found")
        db = get_db()
        doc = await db[CampRegistrationController.COLLECTION].find_one({"_id": ObjectId(reg_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Registration not found")
        return doc

    @staticmethod
    async def create_order(reg_id: str) -> dict:
        doc = await CampRegistrationController._load(reg_id)
        if (doc.get("status") or "") == "paid":
            raise HTTPException(status_code=409, detail="This registration is already paid")
        event = doc.get("event") or {}
        amount_paise = int(doc.get("amount_paise") or 0) or _pay_now_paise(event)
        if amount_paise < 100:
            raise HTTPException(status_code=400, detail="Invalid camp fee amount")
        client = get_razorpay_client()
        participant = doc.get("participant") or {}
        try:
            order = client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "payment_capture": 1,
                    "notes": {
                        "camp_registration_id": str(doc["_id"]),
                        "event_id": (event.get("event_id") or "")[:40],
                        "participant": (participant.get("full_name") or "")[:80],
                    },
                }
            )
        except Exception:
            logger.exception("Razorpay camp order.create failed")
            raise HTTPException(
                status_code=502,
                detail="We could not reach the payment service. Please try again in a moment.",
            )
        now = datetime.utcnow()
        db = get_db()
        await db[CampRegistrationController.COLLECTION].update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "razorpay_order_id": order["id"],
                    "amount_paise": amount_paise,
                    "status": "pending_payment",
                    "updated_at": now,
                }
            },
        )
        return {
            "order": {
                "id": order["id"],
                "amount": amount_paise,
                "currency": "INR",
            },
            "key": os.getenv("RAZORPAY_KEY_ID", "").strip(),
        }

    @staticmethod
    async def verify_payment(reg_id: str, body: CampRegVerifyPayment) -> dict:
        doc = await CampRegistrationController._load(reg_id)
        stored_order = (doc.get("razorpay_order_id") or "").strip()
        if stored_order and stored_order != body.razorpay_order_id:
            raise HTTPException(status_code=400, detail="Order does not match this registration")
        if (doc.get("status") or "") == "paid" and (doc.get("razorpay_payment_id") or "") == body.razorpay_payment_id:
            return CampRegistrationController._payment_result(doc)
        if not verify_razorpay_signature(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        ):
            raise HTTPException(status_code=400, detail="Invalid payment signature")
        now = datetime.utcnow()
        payment = dict(doc.get("payment") or {})
        payment["payment_mode"] = "razorpay"
        payment["transaction_id"] = body.razorpay_payment_id
        payment["agree_policy"] = True
        db = get_db()
        await db[CampRegistrationController.COLLECTION].update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": "paid",
                    "razorpay_order_id": body.razorpay_order_id,
                    "razorpay_payment_id": body.razorpay_payment_id,
                    "razorpay_signature": body.razorpay_signature,
                    "payment": payment,
                    "updated_at": now,
                }
            },
        )
        saved = await db[CampRegistrationController.COLLECTION].find_one({"_id": doc["_id"]})
        return CampRegistrationController._payment_result(saved or doc)

    @staticmethod
    def _payment_result(doc: Dict[str, Any]) -> dict:
        event = doc.get("event") or {}
        participant = doc.get("participant") or {}
        return {
            "id": str(doc.get("_id") or ""),
            "status": doc.get("status") or "paid",
            "event_name": event.get("event_name") or "",
            "event_dates": event.get("event_dates") or "",
            "event_location": event.get("event_location") or "",
            "participant_name": participant.get("full_name") or "",
            "fee_total": event.get("fee_total") or "",
            "fee_pay_now": event.get("fee_pay_now") or "",
            "fee_balance": event.get("fee_balance") or "",
            "refund_text": event.get("refund_text") or "",
            "razorpay_payment_id": doc.get("razorpay_payment_id") or "",
            "amount_paise": doc.get("amount_paise") or 0,
        }

    @staticmethod
    async def _distinct_events() -> List[dict]:
        db = get_db()
        pipeline = [
            {"$group": {
                "_id": "$event.event_id",
                "event_name": {"$first": "$event.event_name"},
                "event_dates": {"$first": "$event.event_dates"},
            }},
            {"$sort": {"event_name": 1}},
        ]
        out = []
        async for row in db[CampRegistrationController.COLLECTION].aggregate(pipeline):
            if not row.get("_id"):
                continue
            out.append({
                "event_id": row["_id"],
                "event_name": row.get("event_name") or "Untitled event",
                "event_dates": row.get("event_dates") or "",
            })
        return out

    @staticmethod
    def _snapshot_event(cms: Dict[str, Any]) -> CampRegistrationEventSnapshot:
        facts = cms.get("facts") or []
        price = (cms.get("schedule") or {}).get("price") or {}
        start = (cms.get("start_date") or "").strip()
        end = (cms.get("end_date") or "").strip()
        dates = _date_range(start, end) if (start or end) else ""
        fee_total = (cms.get("camp_fee") or price.get("amount") or _fact_value(facts, "Camp Fee") or "₹15,000").strip()
        total_paise = _inr_to_paise(fee_total) or 1500000
        pay_paise = int(round(total_paise / 2))
        balance_paise = max(0, total_paise - pay_paise)
        return CampRegistrationEventSnapshot(
            event_id=(cms.get("event_id") or "").strip(),
            event_name=(cms.get("event_name") or "").strip() or "Residential Camp",
            event_dates=dates or _fact_value(facts, "Dates"),
            event_location=(cms.get("event_location") or "").strip() or _fact_value(facts, "Location"),
            fee_total=fee_total,
            fee_pay_now=_paise_to_inr(pay_paise),
            fee_balance=_paise_to_inr(balance_paise),
            refund_text=f"{(price.get('refund_label') or '').strip()} {(price.get('refund_text') or '').strip()}".strip(),
        )

    @staticmethod
    def _validate(payload: CampRegistrationCreate) -> None:
        p = payload.participant
        if not (p.full_name or "").strip():
            raise HTTPException(status_code=400, detail="Full name is required")
        if not (p.parent_guardian_mobile or "").strip():
            raise HTTPException(status_code=400, detail="Parent / guardian mobile is required")
        if not (p.email or "").strip():
            raise HTTPException(status_code=400, detail="Email is required")
        if not payload.final.agreed:
            raise HTTPException(status_code=400, detail="Final declaration is required")
        rules = payload.rules
        if not all([rules.agree_rules, rules.agree_instructions, rules.agree_discipline, rules.agree_property, rules.agree_non_refundable]):
            raise HTTPException(status_code=400, detail="All camp rules must be accepted")
        if not payload.photo.consent:
            raise HTTPException(status_code=400, detail="Photo / video consent is required")
        if not payload.parent_consent.agreed:
            raise HTTPException(status_code=400, detail="Parent / guardian consent is required")
        mode = (payload.payment.payment_mode or "").strip().lower()
        if mode in ("upi", "other") and not (payload.payment.screenshot_url or "").strip():
            raise HTTPException(status_code=400, detail="Payment screenshot is required for this payment mode")

    @staticmethod
    def _to_summary(doc: Dict[str, Any]) -> CampRegistrationSummary:
        ser = serialize_doc(doc) or {}
        participant = ser.get("participant") or {}
        payment = ser.get("payment") or {}
        event = ser.get("event") or {}
        return CampRegistrationSummary(
            id=ser.get("id") or "",
            status=ser.get("status") or "received",
            event_id=event.get("event_id") or "",
            event_name=event.get("event_name") or "",
            event_dates=event.get("event_dates") or "",
            event_location=event.get("event_location") or "",
            full_name=participant.get("full_name") or "",
            parent_guardian_mobile=participant.get("parent_guardian_mobile") or "",
            email=participant.get("email") or "",
            payment_mode=payment.get("payment_mode") or "",
            transaction_id=payment.get("transaction_id") or "",
            created_at=ser.get("created_at"),
        )

    @staticmethod
    def _to_response(doc: Dict[str, Any]) -> CampRegistrationResponse:
        ser = serialize_doc(doc) or {}
        return CampRegistrationResponse(
            id=ser.get("id") or "",
            status=ser.get("status") or "received",
            event=CampRegistrationEventSnapshot(**(ser.get("event") or {})),
            participant=ser.get("participant") or {},
            training=ser.get("training") or {},
            medical=ser.get("medical") or {},
            food=ser.get("food") or {},
            emergency=ser.get("emergency") or {},
            residential=ser.get("residential") or {},
            payment=ser.get("payment") or {},
            rules=ser.get("rules") or {},
            photo=ser.get("photo") or {},
            parent_consent=ser.get("parent_consent") or {},
            hear_about=ser.get("hear_about") or {},
            final=ser.get("final") or {},
            created_at=ser.get("created_at"),
            updated_at=ser.get("updated_at"),
        )
