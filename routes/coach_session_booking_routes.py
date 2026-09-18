"""M15-S05 Coach session booking routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.coach_session_booking_models import (
    CoachSessionBookingCreate,
    CoachSessionReschedule,
    CoachSessionStatusUpdate,
)
from models.user_models import UserRole
from utils.unified_auth import require_role_unified
from utils.coach_session_booking_service import (
    coach_schedule,
    create_session_booking,
    get_session_booking,
    list_lead_session_bookings,
    list_session_bookings,
    reschedule_session,
    session_booking_summary,
    update_session_status,
)
from utils.coach_session_slot_service import list_coach_session_slots

router = APIRouter()
lead_session_router = APIRouter()
coach_me_session_router = APIRouter()

_ADMIN = [UserRole.SUPER_ADMIN, UserRole.COACH_ADMIN, UserRole.BRANCH_MANAGER]
_ADMIN_COACH = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.COACH,
]
_COACH = [UserRole.COACH]


@router.get("/summary")
async def api_summary(
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await session_booking_summary(current_user=current_user)


@router.get("")
async def api_list(
    status: Optional[str] = Query(None),
    coach_id: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await list_session_bookings(
        current_user=current_user,
        status=status,
        coach_id=coach_id,
        lead_id=lead_id,
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.post("", status_code=201)
async def api_create(
    body: CoachSessionBookingCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await create_session_booking(body, current_user=current_user)


@router.get("/slots")
async def api_slots(
    coach_id: str = Query(...),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    # Coaches may only query their own slots
    role = str(current_user.get("role") or "").lower()
    if role == "coach" and str(current_user.get("id")) != coach_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="You can only view your own slots")
    return await list_coach_session_slots(
        coach_id=coach_id,
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
    )


@router.get("/{booking_id}")
async def api_get(
    booking_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await get_session_booking(booking_id, current_user=current_user)


@router.patch("/{booking_id}/status")
async def api_status(
    booking_id: str,
    body: CoachSessionStatusUpdate,
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await update_session_status(booking_id, body, current_user=current_user)


@router.patch("/{booking_id}/reschedule")
async def api_reschedule(
    booking_id: str,
    body: CoachSessionReschedule,
    current_user: dict = Depends(require_role_unified(_ADMIN_COACH)),
):
    return await reschedule_session(booking_id, body, current_user=current_user)


# Nested under /api/leads
@lead_session_router.get("/{lead_id}/coach-session-bookings")
async def api_lead_bookings(
    lead_id: str,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    return await list_lead_session_bookings(lead_id, current_user=current_user)


@lead_session_router.post("/{lead_id}/coach-session-bookings", status_code=201)
async def api_lead_book(
    lead_id: str,
    body: CoachSessionBookingCreate,
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    # Force lead_id from path
    data = body.model_dump()
    data["lead_id"] = lead_id
    forced = CoachSessionBookingCreate(**data)
    return await create_session_booking(forced, current_user=current_user)


@lead_session_router.get("/{lead_id}/coach-session-slots")
async def api_lead_slots(
    lead_id: str,
    coach_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    current_user: dict = Depends(require_role_unified(_ADMIN)),
):
    from utils.database import get_db
    from utils.lead_service import _assert_lead_access
    from fastapi import HTTPException

    db = get_db()
    lead = await db.leads.find_one({"id": lead_id})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    await _assert_lead_access(db, lead, current_user)
    cid = (coach_id or lead.get("assigned_coach_id") or "").strip()
    if not cid:
        raise HTTPException(
            status_code=400,
            detail="No coach assigned. Assign and wait for acceptance first.",
        )
    return await list_coach_session_slots(
        coach_id=cid,
        date_from=date_from,
        date_to=date_to,
        branch_id=lead.get("branch_id"),
    )


# Under /api/coaches
@coach_me_session_router.get("/me/session-bookings")
async def api_my_bookings(
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await list_session_bookings(
        current_user=current_user,
        status=status,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )


@coach_me_session_router.get("/me/schedule")
async def api_my_schedule(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    current_user: dict = Depends(require_role_unified(_COACH)),
):
    return await coach_schedule(
        current_user=current_user, date_from=date_from, date_to=date_to
    )
