"""
M08-S03/S04 public student ID verification (token → approved fields).

No authentication. Does not accept raw student UUID.
Includes lightweight per-IP rate limiting to slow token guessing.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict

from fastapi import APIRouter, HTTPException, Path, Request

from utils.student_id_card_service import verify_qr_token

router = APIRouter()

# Simple in-process rate limit (per worker). Enough to blunt casual guessing.
_VERIFY_WINDOW_SEC = 60
_VERIFY_MAX_PER_WINDOW = 40
_VERIFY_HITS: Dict[str, Deque[float]] = defaultdict(deque)
_VERIFY_LOCK = Lock()


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _rate_limit_or_429(ip: str) -> None:
    now = time.time()
    with _VERIFY_LOCK:
        hits = _VERIFY_HITS[ip]
        while hits and now - hits[0] > _VERIFY_WINDOW_SEC:
            hits.popleft()
        if len(hits) >= _VERIFY_MAX_PER_WINDOW:
            raise HTTPException(
                status_code=429,
                detail="Too many verification attempts. Please try again shortly.",
            )
        hits.append(now)


@router.get("/student-id/verify/{qr_token}")
async def verify_student_id_card(
    request: Request,
    qr_token: str = Path(
        ...,
        min_length=16,
        max_length=128,
        description="Opaque QR token (not student UUID)",
    ),
):
    """
    Resolve QR token to approved verification information.

    Security: token-only lookup, allowlisted fields, rate-limited.
    There is intentionally no verify-by-student-id endpoint.
    """
    _rate_limit_or_429(_client_ip(request))
    return await verify_qr_token(qr_token)


@router.get("/student-id/by-student/{student_id}")
async def verify_by_student_id_blocked(student_id: str):
    """
    Explicitly blocked: direct student ID verification is not allowed (M08-S04-T05).
    """
    raise HTTPException(
        status_code=404,
        detail="Not found. Student identity verification requires a secure QR token.",
    )
