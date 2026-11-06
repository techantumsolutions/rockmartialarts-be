"""
OTP + Razorpay registration checkout.
Mongo collections (isolated from LMS `users`):
  reg_checkout_subscribers   — name, phone, is_verified, created_at
  reg_checkout_subscriptions — user_id, course_name, duration, amount, dates, status, payment_id
  reg_checkout_payments      — user_phone, razorpay ids, amount, status, created_at
  reg_checkout_otp           — phone, code_hash, expires_at, last_sent_at
"""

from __future__ import annotations

import logging
import os
import random
import string
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException
from passlib.context import CryptContext

from models.reg_checkout_models import CreateRegOrderBody, VerifyOtpBody, VerifyRegPaymentBody
from utils.database import get_db
from utils.indian_phone import (
    canonical_indian_phone,
    otp_phone_variants,
    subscriber_phone_lookup_filter,
    subscriber_phone_matches,
)
from utils.reg_checkout_sms import (
    public_sms_failure_hint,
    send_registration_sms,
    sms_provider_expects_delivery,
)
from utils.razorpay_client import get_razorpay_client, verify_razorpay_signature

logger = logging.getLogger(__name__)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

COL_SUBSCRIBERS = "reg_checkout_subscribers"
COL_SUBSCRIPTIONS = "reg_checkout_subscriptions"
COL_PAYMENTS = "reg_checkout_payments"
COL_OTP = "reg_checkout_otp"

SECRET_KEY = os.environ.get("SECRET_KEY", "student_management_secret_key_2025_secure")
JWT_ALG = "HS256"
# OTP code validity (minutes); clamp 5–15 per ops-friendly defaults
OTP_TTL_MIN = max(
    5, min(15, int(os.getenv("REG_CHECKOUT_OTP_TTL_MIN", "10")))
)
RESEND_COOLDOWN_SEC = int(os.getenv("REG_CHECKOUT_RESEND_COOLDOWN_SEC", "30"))
# Post-verify JWT for create-order / verify-payment (do not tie to OTP TTL)
VERIFICATION_JWT_HOURS = int(os.getenv("REG_CHECKOUT_VERIFY_JWT_HOURS", "24"))
JWT_DECODE_LEEWAY_SEC = int(os.getenv("REG_CHECKOUT_JWT_LEEWAY_SEC", "120"))

REQUIRE_OTP = os.getenv("REG_CHECKOUT_REQUIRE_OTP", "true").lower() in ("1", "true", "yes")


def _issue_phone_token(phone: str) -> str:
    norm = canonical_indian_phone(phone)
    if not norm:
        raise HTTPException(status_code=400, detail="Invalid phone number.")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": norm,
        "scope": "reg_checkout",
        "iat": now,
        "exp": now + timedelta(hours=VERIFICATION_JWT_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALG)


def _verify_phone_token(token: Optional[str], phone: str) -> None:
    if not REQUIRE_OTP:
        return
    if not token:
        raise HTTPException(status_code=401, detail="Phone verification required (missing token).")
    norm = canonical_indian_phone(phone)
    if not norm:
        raise HTTPException(status_code=401, detail="Invalid phone number.")
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[JWT_ALG],
            leeway=JWT_DECODE_LEEWAY_SEC,
        )
        if payload.get("scope") != "reg_checkout" or str(payload.get("sub")) != norm:
            raise HTTPException(status_code=403, detail="Invalid phone verification.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Phone verification expired. Please verify your number again from the OTP step.",
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired phone verification.")


def _dlt_template_id_primary_legacy(primary_key: str, legacy_key: str) -> Optional[str]:
    """Use explicit *TEMPLATE_ID env, or legacy var only when it looks like an id (not a message with %)."""
    v = os.getenv(primary_key, "").strip()
    if v:
        return v
    legacy = os.getenv(legacy_key, "").strip()
    if not legacy:
        return None
    if "%" in legacy or "{" in legacy or len(legacy) > 32:
        return None
    compact = legacy.replace("-", "").replace("_", "")
    if compact.isalnum() and len(compact) >= 6 and not legacy.lower().startswith("your "):
        return legacy
    return None


def _otp_dlt_template_id() -> Optional[str]:
    """
    DLT / smslogin `templateid` must be the provider's template ID (digits/alphanumeric),
    not the approved message text. Prefer DLT_OTP_TEMPLATE_ID; legacy DLT_TEMPLATE_OTP
    is used only when it looks like an ID (not a sentence).
    """
    v = os.getenv("DLT_OTP_TEMPLATE_ID", "").strip() or os.getenv("SMS_OTP_TEMPLATE_ID", "").strip()
    if v:
        return v
    legacy = os.getenv("DLT_TEMPLATE_OTP", "").strip()
    if not legacy or "%" in legacy or "{" in legacy or len(legacy) > 32:
        return None
    compact = legacy.replace("-", "").replace("_", "")
    if compact.isalnum() and not legacy.lower().startswith("your "):
        return legacy
    return None


def _otp_sms_message(otp: str) -> str:
    """SMS body must match the DLT-approved wording; use DLT_OTP_MESSAGE with %s or {otp}."""
    body = os.getenv("DLT_OTP_MESSAGE", "").strip()
    if not body:
        legacy = os.getenv("DLT_TEMPLATE_OTP", "").strip()
        if legacy and ("%s" in legacy or "{otp}" in legacy.lower()):
            body = legacy
    if body:
        if "%s" in body:
            try:
                return body % otp
            except Exception:
                logger.exception("DLT_OTP_MESSAGE / DLT_TEMPLATE_OTP %%s formatting failed")
        return body.replace("{otp}", otp)
    return (
        "ROCK MARTIAL ARTS ACADEMY: Your OTP is "
        f"{otp}. Use this to complete your registration/login. Do not share this code with anyone."
    )


def _sms_dlt_template_id(kind: str) -> Optional[str]:
    """
    DLT template id for transactional SMS (welcome, payment, reminder).
    Uses the same resolution path as OTP: kind-specific env → optional shared
    DLT_TRANSACTIONAL_TEMPLATE_ID → DLT_OTP_TEMPLATE_ID so smslogin always gets
    a templateid when OTP SMS is already configured.
    For Indian DLT, the SMS body must match the template registered for that id.
    """
    if kind == "welcome":
        tid = _dlt_template_id_primary_legacy("DLT_WELCOME_TEMPLATE_ID", "DLT_TEMPLATE_WELCOME")
    elif kind == "payment":
        tid = _dlt_template_id_primary_legacy("DLT_PAYMENT_TEMPLATE_ID", "DLT_TEMPLATE_PAYMENT_CONFIRMED")
    elif kind == "reminder":
        tid = _dlt_template_id_primary_legacy("DLT_REMINDER_TEMPLATE_ID", "DLT_TEMPLATE_REMINDER")
    else:
        tid = None
    if tid:
        return tid
    tx = os.getenv("DLT_TRANSACTIONAL_TEMPLATE_ID", "").strip()
    if tx:
        return tx
    otp_tid = _otp_dlt_template_id()
    if otp_tid and kind != "otp":
        logger.warning(
            "SMS kind=%s: using DLT_OTP_TEMPLATE_ID — set DLT_%s_TEMPLATE_ID or "
            "DLT_TRANSACTIONAL_TEMPLATE_ID if this message uses a different DLT template.",
            kind,
            kind.upper(),
        )
    return otp_tid


def _welcome_sms_text(name: str, course_name: str, duration: str) -> str:
    tpl = os.getenv("DLT_WELCOME_MESSAGE", "").strip()
    if tpl:
        try:
            return tpl % {
                "name": name.strip(),
                "course_name": course_name,
                "duration": duration,
            }
        except Exception:
            logger.exception("DLT_WELCOME_MESSAGE format failed; using default body")
    return (
        f"ROCK MARTIAL ARTS ACADEMY: Welcome {name.strip()}! Your registration for "
        f"{course_name} – {duration} is confirmed. Get ready to train hard and grow stronger every day. See you in class!"
    )


def _payment_sms_text(name: str, amount: float, course_name: str, duration: str) -> str:
    tpl = os.getenv("DLT_PAYMENT_MESSAGE", "").strip()
    if tpl:
        try:
            return tpl % {
                "name": name.strip(),
                "amount": amount,
                "course_name": course_name,
                "duration": duration,
            }
        except Exception:
            logger.exception("DLT_PAYMENT_MESSAGE format failed; using default body")
    return (
        f"ROCK MARTIAL ARTS ACADEMY: Hi {name.strip()}, your payment of ₹{amount:.0f} for "
        f"{course_name} – {duration} was received successfully. "
        "Thank you — see you in class!"
    )


def _reminder_sms_text(name: str, validity: str) -> str:
    tpl = os.getenv("DLT_REMINDER_MESSAGE", "").strip()
    if tpl:
        try:
            return tpl % {"name": name.strip(), "validity": validity}
        except Exception:
            logger.exception("DLT_REMINDER_MESSAGE format failed; using default body")
    return (
        f"ROCK MARTIAL ARTS ACADEMY: Hi {name}, your plan ends on {validity}. "
        "Don't break your momentum—renew now and keep progressing. Stay strong!"
    )


def _coerce_subscription_end(val: Any) -> Optional[datetime]:
    """Normalize end_date from Mongo (datetime, date, or ISO string) for comparisons."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, 23, 59, 59)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if "T" in s:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except ValueError:
                return None
        try:
            d = date.fromisoformat(s[:10])
            return datetime(d.year, d.month, d.day, 23, 59, 59)
        except ValueError:
            return None
    return None


def _parse_end_date(s: str) -> datetime:
    s = (s or "").strip().replace("Z", "")
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    d = date.fromisoformat(s[:10])
    return datetime(d.year, d.month, d.day, 23, 59, 59)


class RegCheckoutController:
    @staticmethod
    async def send_otp(phone: str) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        canonical = canonical_indian_phone(phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid phone number.")

        variants = otp_phone_variants(canonical)
        filt: Dict[str, Any] = {"phone": {"$in": variants}}
        existing = await db[COL_OTP].find_one(filt)

        now = datetime.utcnow()
        if existing and existing.get("last_sent_at"):
            delta = (now - existing["last_sent_at"]).total_seconds()
            if delta < RESEND_COOLDOWN_SEC:
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {int(RESEND_COOLDOWN_SEC - delta)}s before resending OTP.",
                )

        otp = "".join(random.choices(string.digits, k=6))
        code_hash = _pwd.hash(otp)
        expires = now + timedelta(minutes=OTP_TTL_MIN)

        otp_doc = {
            "phone": canonical,
            "code_hash": code_hash,
            "expires_at": expires,
            "last_sent_at": now,
        }
        if existing:
            await db[COL_OTP].update_one(
                {"_id": existing["_id"]},
                {"$set": otp_doc},
            )
        else:
            await db[COL_OTP].insert_one(otp_doc)

        msg = _otp_sms_message(otp)
        tid = _otp_dlt_template_id()
        if tid is None and sms_provider_expects_delivery():
            prov = (os.getenv("SMS_PROVIDER") or "json").strip().lower()
            if prov in ("smslogin", "smslogin_co", "rockacademy", "msg91"):
                logger.warning(
                    "OTP SMS: no DLT template id (set DLT_OTP_TEMPLATE_ID). "
                    "smslogin/MSG91 often require templateid for DLT."
                )
        ok, sms_err = send_registration_sms(canonical, msg, template_id=tid)
        allow_stub = os.getenv("REG_CHECKOUT_ALLOW_SMS_STUB", "").lower() in (
            "1",
            "true",
            "yes",
        )
        expects_sms = sms_provider_expects_delivery()
        if expects_sms and not allow_stub and not ok:
            await db[COL_OTP].delete_many({"phone": {"$in": variants}})
            hint = public_sms_failure_hint(sms_err)
            base = (
                "Could not send OTP SMS. Set SMS_PROVIDER=smslogin, SMS_API_URL=https://smslogin.co/v3/api.php, "
                "SMS_USERNAME, SMS_API_KEY, SMS_SENDER_ID, DLT_OTP_TEMPLATE_ID (DLT template id from portal), "
                "and DLT_OTP_MESSAGE (approved body with %s for OTP). Check server logs for the gateway response."
            )
            if hint:
                base = f"{base} Hint: {hint}"
            base += (
                " For local/staging without SMS, set REG_CHECKOUT_ALLOW_SMS_STUB=true (OTP is still stored; "
                "check server logs for the code)."
            )
            raise HTTPException(status_code=503, detail=base)
        if expects_sms and allow_stub and not ok:
            logger.warning(
                "OTP SMS send failed but REG_CHECKOUT_ALLOW_SMS_STUB=true — continuing without delivery."
            )
        if not expects_sms:
            logger.warning(
                "SMS gateway not configured for OTP (e.g. SMS_PROVIDER=json with no SMS_API_URL). "
                "OTP is stored but not sent to the handset — configure Rock Academy SMS env or set "
                "REG_CHECKOUT_ALLOW_SMS_STUB=true only for local dev."
            )
        elif expects_sms and ok:
            logger.info("OTP sent via SMS for phone ending ...%s", canonical[-4:])

        return {"message": "OTP sent", "expires_in_seconds": OTP_TTL_MIN * 60}

    @staticmethod
    async def verify_otp(body: VerifyOtpBody) -> Dict[str, Any]:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        canonical = canonical_indian_phone(body.phone)
        if not canonical:
            raise HTTPException(status_code=400, detail="Invalid phone number.")
        variants = otp_phone_variants(canonical)
        doc = await db[COL_OTP].find_one({"phone": {"$in": variants}})
        if not doc:
            raise HTTPException(status_code=400, detail="No OTP request for this number.")

        if doc.get("expires_at") and datetime.utcnow() > doc["expires_at"]:
            raise HTTPException(
                status_code=400,
                detail="OTP expired. Please resend.",
            )

        if not _pwd.verify(body.otp, doc["code_hash"]):
            raise HTTPException(status_code=400, detail="Invalid OTP.")

        # Remove consumed OTP; payment flow relies on verification JWT until it expires
        await db[COL_OTP].delete_one({"_id": doc["_id"]})

        token = _issue_phone_token(canonical)
        return {
            "verified": True,
            "verification_token": token,
            "expires_in": VERIFICATION_JWT_HOURS * 3600,
        }

    @staticmethod
    async def create_order(body: CreateRegOrderBody) -> Dict[str, Any]:
        _verify_phone_token(body.verification_token, body.phone)
        phone_canon = canonical_indian_phone(body.phone)
        if not phone_canon:
            raise HTTPException(status_code=400, detail="Invalid phone number.")

        client = get_razorpay_client()
        amount_paise = int(round(float(body.amount) * 100))
        if amount_paise < 100:
            raise HTTPException(status_code=400, detail="Amount too small.")

        try:
            order = client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "payment_capture": 1,
                    "notes": {
                        "phone": phone_canon,
                        "course": (body.course_name or "")[:120],
                    },
                }
            )
        except Exception:
            logger.exception("Razorpay order.create failed")
            raise HTTPException(
                status_code=502,
                detail="We could not reach the payment service. Please try again in a moment.",
            )

        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        now = datetime.utcnow()
        await db[COL_PAYMENTS].insert_one(
            {
                "user_phone": phone_canon,
                "razorpay_order_id": order["id"],
                "razorpay_payment_id": None,
                "razorpay_signature": None,
                "amount": float(body.amount),
                "status": "created",
                "created_at": now,
                "course_name": body.course_name,
                "duration": body.duration,
                "customer_name": body.name,
            }
        )

        key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        return {
            "order_id": order["id"],
            "key": key_id,
            "amount": amount_paise,
            "currency": "INR",
        }

    @staticmethod
    async def verify_payment(body: VerifyRegPaymentBody) -> Dict[str, Any]:
        _verify_phone_token(body.verification_token, body.phone)

        if not verify_razorpay_signature(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        ):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        pay = await db[COL_PAYMENTS].find_one({"razorpay_order_id": body.razorpay_order_id})
        if not pay:
            raise HTTPException(status_code=404, detail="Unknown order id")

        norm_phone = canonical_indian_phone(body.phone)
        if not norm_phone or not subscriber_phone_matches(
            pay.get("user_phone"), norm_phone
        ):
            raise HTTPException(status_code=403, detail="Phone does not match order")

        dup = await db[COL_SUBSCRIPTIONS].find_one({"payment_id": body.razorpay_payment_id})
        if dup:
            return {"status": "success", "idempotent": True, "subscription_id": str(dup["_id"])}

        now = datetime.utcnow()
        await db[COL_PAYMENTS].update_one(
            {"razorpay_order_id": body.razorpay_order_id},
            {
                "$set": {
                    "status": "paid",
                    "razorpay_payment_id": body.razorpay_payment_id,
                    "razorpay_signature": body.razorpay_signature,
                    "paid_at": now,
                }
            },
        )

        user = await db[COL_SUBSCRIBERS].find_one(subscriber_phone_lookup_filter(norm_phone))
        if not user:
            ins = await db[COL_SUBSCRIBERS].insert_one(
                {
                    "name": body.name.strip(),
                    "phone": norm_phone,
                    "is_verified": True,
                    "created_at": now,
                }
            )
            user_id = ins.inserted_id
        else:
            user_id = user["_id"]
            await db[COL_SUBSCRIBERS].update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "name": body.name.strip(),
                        "is_verified": True,
                        "phone": norm_phone,
                    }
                },
            )

        end_dt = _parse_end_date(body.end_date)
        sub = await db[COL_SUBSCRIPTIONS].insert_one(
            {
                "user_id": user_id,
                "course_name": body.course_name,
                "duration": body.duration,
                "amount": float(body.amount),
                "start_date": now,
                "end_date": end_dt,
                "status": "active",
                "payment_id": body.razorpay_payment_id,
            }
        )

        pay_msg = _payment_sms_text(
            body.name, float(body.amount), body.course_name, body.duration
        )
        tid_pay = _sms_dlt_template_id("payment")
        welcome = _welcome_sms_text(body.name, body.course_name, body.duration)
        tid_w = _sms_dlt_template_id("welcome")

        welcome_also_in_payment = False
        if tid_w:
            ok_w, err_w = send_registration_sms(norm_phone, welcome, template_id=tid_w)
            if not ok_w:
                logger.error(
                    "Welcome SMS failed payment_id=%s phone=...%s err=%s — will include welcome in payment SMS.",
                    body.razorpay_payment_id,
                    (norm_phone or body.phone)[-4:],
                    err_w,
                )
                welcome_also_in_payment = True
        else:
            logger.warning(
                "DLT_WELCOME_TEMPLATE_ID not set — prepending welcome text to payment SMS "
                "so the student still receives a welcome line (single DLT template)."
            )
            welcome_also_in_payment = True
        if welcome_also_in_payment:
            pay_msg = f"{welcome.strip()} {pay_msg.strip()}"

        ok_p, err_p = send_registration_sms(norm_phone, pay_msg, template_id=tid_pay)
        if not ok_p:
            logger.error(
                "Payment confirmation SMS failed payment_id=%s phone=...%s err=%s",
                body.razorpay_payment_id,
                (norm_phone or body.phone)[-4:],
                err_p,
            )

        return {
            "status": "success",
            "subscription_id": str(sub.inserted_id),
            "user_id": str(user_id),
        }

    @staticmethod
    async def run_renewal_reminders(secret: str) -> Dict[str, Any]:
        expected = os.getenv("REG_CHECKOUT_CRON_SECRET", "").strip()
        if not expected or secret != expected:
            raise HTTPException(status_code=403, detail="Invalid cron secret")

        db = get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database not initialized")

        now = datetime.utcnow()
        until = now + timedelta(days=7)
        q = {
            "status": "active",
            "$or": [{"renewal_reminder_sent": {"$exists": False}}, {"renewal_reminder_sent": False}],
        }

        sent = 0
        skipped = 0
        async for sub in db[COL_SUBSCRIPTIONS].find(q):
            end = _coerce_subscription_end(sub.get("end_date"))
            if end is None:
                skipped += 1
                continue
            if end < now or end > until:
                continue

            user = await db[COL_SUBSCRIBERS].find_one({"_id": sub["user_id"]})
            if not user:
                skipped += 1
                continue
            phone = user.get("phone")
            if not phone:
                skipped += 1
                continue
            name = str(user.get("name") or "Student").strip() or "Student"
            validity = end.strftime("%d-%m-%Y")

            # Additive email reminder (does not replace SMS)
            try:
                from utils.ops_email_hooks import fire_renewal_reminder

                await fire_renewal_reminder(
                    student_email=user.get("email"),
                    student_name=name,
                    course_name=str(sub.get("plan_name") or sub.get("course_name") or "Plan"),
                    end_date=end,
                )
            except Exception:
                logger.exception("ops email renewal reminder failed")

            msg = _reminder_sms_text(name, validity)
            tid = _sms_dlt_template_id("reminder")
            ok, err = send_registration_sms(phone, msg, template_id=tid)
            if ok:
                await db[COL_SUBSCRIPTIONS].update_one(
                    {"_id": sub["_id"]},
                    {"$set": {"renewal_reminder_sent": True, "renewal_reminder_at": now}},
                )
                sent += 1
                logger.info(
                    "Renewal reminder SMS sent subscription_id=%s phone=...%s",
                    sub.get("_id"),
                    (canonical_indian_phone(str(phone)) or str(phone))[-4:],
                )
            else:
                cu = canonical_indian_phone(str(phone)) or str(phone)
                logger.error(
                    "Renewal reminder SMS failed subscription_id=%s phone=...%s err=%s",
                    sub.get("_id"),
                    cu[-4:] if len(cu) >= 4 else cu,
                    err,
                )

        return {
            "message": "Renewal reminders processed",
            "sent": sent,
            "skipped_no_user_or_end_date": skipped,
        }
