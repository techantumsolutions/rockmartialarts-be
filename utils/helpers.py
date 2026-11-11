from fastapi import Request
from typing import Optional, Dict, Any
from datetime import datetime, date
from bson import ObjectId
import logging
import os

from models.activitylog_models import ActivityLog
from models.notification_models import NotificationLog, NotificationType
from utils.database import get_db

def serialize_doc(doc):
    """Convert MongoDB document to JSON serializable format"""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_doc(item) for item in doc]
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == "_id":
                continue  # Skip MongoDB _id field
            elif isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, date) and not isinstance(value, datetime):
                # Convert date objects to ISO format string for BSON compatibility
                result[key] = value.isoformat()
            elif isinstance(value, datetime):
                # Convert datetime objects to ISO format string for JSON serialization
                result[key] = value.isoformat()
            elif isinstance(value, dict):
                result[key] = serialize_doc(value)
            elif isinstance(value, list):
                result[key] = serialize_doc(value)
            else:
                result[key] = value
        return result
    elif isinstance(doc, date) and not isinstance(doc, datetime):
        # Convert standalone date objects to ISO format string
        return doc.isoformat()
    elif isinstance(doc, datetime):
        # Convert datetime objects to ISO format string for JSON serialization
        return doc.isoformat()
    return doc

async def log_activity(
    request: Request,
    action: str,
    status: str = "success",
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
):
    """Helper function to log user activity."""
    db = get_db()

    # Serialize details to handle date objects and other non-BSON types
    serialized_details = serialize_doc(details) if details else None

    log_entry = ActivityLog(
        user_id=user_id,
        user_name=user_name,
        action=action,
        details=serialized_details,
        status=status,
        ip_address=request.client.host if (request and getattr(request, "client", None)) else "N/A",
        timestamp=datetime.utcnow()
    )

    # Serialize the entire log entry to ensure BSON compatibility
    log_entry_dict = serialize_doc(log_entry.dict())
    await db.activity_logs.insert_one(log_entry_dict)

def _allow_sms_or_whatsapp_stub() -> bool:
    for key in (
        "ALLOW_SMS_STUB",
        "NOTIFICATION_ALLOW_SMS_STUB",
        "REG_CHECKOUT_ALLOW_SMS_STUB",
    ):
        if os.getenv(key, "").lower() in ("1", "true", "yes"):
            return True
    env = (os.getenv("ENV") or os.getenv("NODE_ENV") or "").lower()
    if env in ("production", "prod"):
        return False
    return os.getenv("ENVIRONMENT", "development").lower() not in ("production", "prod")


async def send_sms(phone: str, message: str) -> bool:
    """Send SMS via registration checkout provider when configured; never fake success in production."""
    from utils.reg_checkout_sms import send_registration_sms, sms_provider_expects_delivery

    ok, err = send_registration_sms(phone, message)
    if ok:
        return True
    if sms_provider_expects_delivery():
        logging.warning("SMS send failed to %s: %s", phone, err or "unknown error")
        return False
    if _allow_sms_or_whatsapp_stub():
        logging.info("SMS stub (no provider): message not sent to %s", phone)
        return True
    logging.warning("SMS provider not configured; refusing to report success for %s", phone)
    return False


async def send_whatsapp(phone: str, message: str) -> bool:
    """WhatsApp delivery via Meta Cloud / webhook when configured; stub only when allowed."""
    from utils.whatsapp_sender import send_whatsapp_message, whatsapp_is_configured, whatsapp_provider_name

    if whatsapp_is_configured():
        ok, err = await send_whatsapp_message(phone, message)
        if ok:
            return True
        logging.warning("WhatsApp send failed to %s: %s", phone, err or "unknown error")
        return False

    provider = whatsapp_provider_name()
    if _allow_sms_or_whatsapp_stub():
        logging.info("WhatsApp stub (provider=%s): message not sent to %s", provider or "unset", phone)
        return True
    logging.warning(
        "WhatsApp provider not configured (WHATSAPP_PROVIDER=%s); refusing fake success for %s",
        provider or "unset",
        phone,
    )
    return False

async def check_and_send_stock_alert(product: dict, branch_id: str, new_stock_level: int):
    """Checks if stock is low and sends an alert if needed."""
    db = get_db()
    threshold = product.get("stock_alert_threshold", 10)
    if new_stock_level <= threshold:
        # Additive email alert (does not replace existing SMS/WA template path)
        try:
            from utils.ops_email_hooks import fire_stock_low

            branch_name = ""
            try:
                bdoc = await db.branches.find_one({"id": branch_id})
                if bdoc:
                    branch_name = (bdoc.get("branch") or {}).get("name") or bdoc.get("name") or ""
            except Exception:
                pass
            await fire_stock_low(
                db,
                item_name=(product or {}).get("name") or "Item",
                quantity=new_stock_level,
                branch_name=branch_name,
            )
        except Exception:
            logging.exception("ops email stock low alert failed")

        # Find admins to notify (Super Admins and the Coach Admin of the branch)
        from models.user_models import UserRole
        admin_filter = {
            "$or": [
                {"role": UserRole.SUPER_ADMIN.value},
                {"role": UserRole.COACH_ADMIN.value, "branch_id": branch_id}
            ]
        }
        admins = await db.users.find(admin_filter).to_list(length=None)

        template = await db.notification_templates.find_one({"name": "low_stock_alert"})
        if not template or not admins:
            return  # Cannot send alert if no template or no admins

        for admin in admins:
            body = template["body"].replace("{{product_name}}", product["name"])
            body = body.replace("{{branch_id}}", branch_id)
            body = body.replace("{{stock_level}}", str(new_stock_level))

            success = False
            if template["type"] == NotificationType.WHATSAPP.value:
                success = await send_whatsapp(admin["phone"], body)
            elif template["type"] == NotificationType.SMS.value:
                success = await send_sms(admin["phone"], body)

            log_entry = NotificationLog(
                user_id=admin["id"],
                template_id=template["id"],
                type=template["type"],
                status="sent" if success else "failed",
                content=body
            )
            await db.notification_logs.insert_one(log_entry.dict())
