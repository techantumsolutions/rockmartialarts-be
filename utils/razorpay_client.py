"""Shared Razorpay SDK client and signature verification (keys stay on server)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Dict

import razorpay
import requests
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def get_razorpay_client() -> razorpay.Client:
    key = os.getenv("RAZORPAY_KEY_ID", "").strip()
    sec = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key or not sec:
        raise HTTPException(
            status_code=503,
            detail="Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET).",
        )
    sess = requests.Session()
    sess.trust_env = False
    return razorpay.Client(session=sess, auth=(key, sec))


def verify_razorpay_signature(order_id: str, pay_id: str, signature: str) -> bool:
    sec = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not sec:
        return False
    body = f"{order_id}|{pay_id}".encode("utf-8")
    expected = hmac.new(sec.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def get_razorpay_runtime_status() -> Dict[str, Any]:
    """
    Safe go-live readiness snapshot (no secrets).
    Used by /api/payments/razorpay/status and startup warnings.
    """
    key = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    sec = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()
    webhook = (os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    frontend = (
        os.getenv("FRONTEND_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or ""
    ).strip()

    mode = "unset"
    if key.startswith("rzp_live_"):
        mode = "live"
    elif key.startswith("rzp_test_"):
        mode = "test"

    frontend_is_local = (not frontend) or ("localhost" in frontend) or ("127.0.0.1" in frontend)
    frontend_is_https = frontend.lower().startswith("https://")

    warnings = []
    if not key or not sec:
        warnings.append("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing")
    if mode == "live" and frontend_is_local:
        warnings.append("FRONTEND_URL still points at localhost — receipt/invite links will be wrong on live")
    if mode == "live" and frontend and not frontend_is_https:
        warnings.append("FRONTEND_URL should be https for live Razorpay checkouts and emails")
    if mode == "live" and not webhook:
        warnings.append(
            "RAZORPAY_WEBHOOK_SECRET missing — paid-but-unconfirmed recoveries via webhook will not work"
        )

    return {
        "configured": bool(key and sec),
        "mode": mode,
        "key_prefix": key[:12] if key else None,
        "webhook_configured": bool(webhook),
        "frontend_url_set": bool(frontend),
        "frontend_url_is_local": frontend_is_local,
        "frontend_url_is_https": frontend_is_https if frontend else False,
        "ready_for_checkout": bool(key and sec and mode in {"live", "test"}),
        "warnings": warnings,
    }


def log_razorpay_startup_warnings() -> None:
    status = get_razorpay_runtime_status()
    if not status["configured"]:
        logger.error("Razorpay is NOT configured — online payments will fail (503).")
        return
    logger.info(
        "Razorpay ready mode=%s webhook=%s frontend_local=%s",
        status["mode"],
        status["webhook_configured"],
        status["frontend_url_is_local"],
    )
    for w in status.get("warnings") or []:
        logger.warning("Razorpay go-live: %s", w)
