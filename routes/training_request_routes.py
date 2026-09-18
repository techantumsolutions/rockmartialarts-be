"""M12 Training request routes — Home/School/College/Corporate/Residential + shared admin."""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from models.training_request_models import (
    CollegeTrainingRequestCreate,
    CorporateTrainingRequestCreate,
    HomeTrainingRequestCreate,
    ResidentialTrainingRequestCreate,
    SchoolTrainingRequestCreate,
    TrainingRequestCoachAssign,
    TrainingRequestPaymentVerify,
    TrainingRequestStatusUpdate,
)
from models.user_models import UserRole
from utils.training_request_service import (
    assign_coach,
    create_college_training_request,
    create_corporate_training_request,
    create_home_training_request,
    create_residential_payment_order,
    create_residential_training_request,
    create_school_training_request,
    get_training_request,
    list_residential_packages,
    list_status_history,
    list_training_requests,
    summarize_training_requests,
    update_training_request_status,
    verify_residential_payment,
)
from utils.unified_auth import get_optional_current_user_or_superadmin, require_role_unified

router = APIRouter()

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN,
    UserRole.COACH_ADMIN,
    UserRole.BRANCH_MANAGER,
]


@router.post("/home", status_code=status.HTTP_201_CREATED)
async def api_create_home_training(
    body: HomeTrainingRequestCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public (optional auth) Home Training request submission."""
    return await create_home_training_request(body, current_user=current_user)


@router.post("/school", status_code=status.HTTP_201_CREATED)
async def api_create_school_training(
    body: SchoolTrainingRequestCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public (optional auth) School Training request submission."""
    return await create_school_training_request(body, current_user=current_user)


@router.post("/college", status_code=status.HTTP_201_CREATED)
async def api_create_college_training(
    body: CollegeTrainingRequestCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public (optional auth) College Training request submission."""
    return await create_college_training_request(body, current_user=current_user)


@router.post("/corporate", status_code=status.HTTP_201_CREATED)
async def api_create_corporate_training(
    body: CorporateTrainingRequestCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public (optional auth) Corporate Training request submission."""
    return await create_corporate_training_request(body, current_user=current_user)


@router.get("/residential/packages")
async def api_list_residential_packages():
    """Public catalog of residential packages/durations (does not touch camp CMS)."""
    return list_residential_packages()


@router.post("/residential", status_code=status.HTTP_201_CREATED)
async def api_create_residential_training(
    body: ResidentialTrainingRequestCreate,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public (optional auth) Residential Training request / enquiry."""
    return await create_residential_training_request(body, current_user=current_user)


@router.post("/{request_id}/create-order")
async def api_create_residential_order(
    request_id: str,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — create Razorpay order for a residential training request (M06)."""
    return await create_residential_payment_order(request_id, current_user=current_user)


@router.post("/{request_id}/verify-payment")
async def api_verify_residential_payment(
    request_id: str,
    body: TrainingRequestPaymentVerify,
    current_user: Optional[dict] = Depends(get_optional_current_user_or_superadmin),
):
    """Public — verify Razorpay payment for residential training request (M06)."""
    return await verify_residential_payment(request_id, body, current_user=current_user)


@router.get("/summary")
async def api_training_requests_summary(
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    """M12-S06 unified admin summary counts (type / status / payment / unassigned)."""
    return await summarize_training_requests(
        current_user=current_user, branch_id=branch_id
    )


@router.get("")
async def api_list_training_requests(
    type: Optional[str] = Query(None, alias="type"),
    status_filter: Optional[str] = Query(None, alias="status"),
    branch_id: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    assigned_coach_id: Optional[str] = Query(None),
    unassigned_only: bool = Query(False),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_training_requests(
        current_user=current_user,
        type_filter=type,
        status=status_filter,
        branch_id=branch_id,
        payment_status=payment_status,
        assigned_coach_id=assigned_coach_id,
        unassigned_only=unassigned_only,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/{request_id}")
async def api_get_training_request(
    request_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await get_training_request(request_id, current_user=current_user)


@router.get("/{request_id}/status-history")
async def api_training_request_history(
    request_id: str,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await list_status_history(request_id, current_user=current_user)


@router.patch("/{request_id}/status")
async def api_update_status(
    request_id: str,
    body: TrainingRequestStatusUpdate,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await update_training_request_status(
        request_id, body, current_user=current_user
    )


@router.patch("/{request_id}/coach")
async def api_assign_coach(
    request_id: str,
    body: TrainingRequestCoachAssign,
    current_user: dict = Depends(require_role_unified(ADMIN_ROLES)),
):
    return await assign_coach(request_id, body, current_user=current_user)
