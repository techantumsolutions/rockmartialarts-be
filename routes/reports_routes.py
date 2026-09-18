from fastapi import APIRouter, Depends, Query
from typing import Optional
from datetime import datetime
from controllers.reports_controller import ReportsController
from models.user_models import UserRole
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin

router = APIRouter()

@router.get("/categories")
async def get_report_categories():
    """Get available report categories"""
    return await ReportsController.get_report_categories()

@router.get("/categories/{category_id}/reports")
async def get_category_reports(category_id: str):
    """Get reports available for a specific category"""
    return await ReportsController.get_category_reports(category_id)

@router.get("/financial")
async def get_financial_reports(
    branch_id: Optional[str] = Query(None, description="Filter by specific branch ID"),
    payment_type: Optional[str] = Query(None, description="Filter by payment type"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    payment_status: Optional[str] = Query(None, description="Filter by payment status"),
    date_range: Optional[str] = Query(None, description="Filter by date range"),
    amount_min: Optional[float] = Query(None, description="Minimum amount filter"),
    amount_max: Optional[float] = Query(None, description="Maximum amount filter"),
    search: Optional[str] = Query(None, description="Search in transaction ID, course, branch, or notes"),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Number of records to return"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive financial reports with enhanced filtering and search"""
    return await ReportsController.get_financial_reports(
        current_user, branch_id, payment_type, payment_method, payment_status,
        date_range, amount_min, amount_max, search, skip, limit
    )

@router.get("/financial/filters")
async def get_financial_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for financial reports"""
    return await ReportsController.get_financial_report_filters(current_user)

@router.get("/students")
async def get_student_reports(
    branch_id: Optional[str] = Query(None, description="Filter by branch ID (ignored for branch managers - they see only their assigned branches)"),
    course_id: Optional[str] = Query(None, description="Filter by course ID"),
    start_date: Optional[datetime] = Query(None, description="Start date for report"),
    end_date: Optional[datetime] = Query(None, description="End date for report"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive student reports with branch-specific filtering for branch managers"""
    return await ReportsController.get_student_reports(
        current_user, branch_id, course_id, start_date, end_date
    )


@router.get("/students/list")
async def list_student_report_rows(
    q: Optional[str] = Query(None, description="Search name/email/phone"),
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None, description="Account status filter"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """M08-S02: filtered student rows for Admin/Branch Manager reports."""
    return await ReportsController.list_student_report_rows(
        current_user,
        q=q,
        branch_id=branch_id,
        course_id=course_id,
        is_active=is_active,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get("/students/export")
async def export_student_reports(
    format: str = Query("csv", description="csv or excel"),
    q: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    course_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin),
):
    """M08-S02: CSV/Excel export using current authorized filters."""
    return await ReportsController.export_student_reports(
        current_user,
        format=format,
        q=q,
        branch_id=branch_id,
        course_id=course_id,
        is_active=is_active,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/students/filters")
async def get_student_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for student reports (branch-specific for branch managers)"""
    return await ReportsController.get_student_report_filters(current_user)

@router.get("/coaches")
async def get_coach_reports(
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    start_date: Optional[datetime] = Query(None, description="Start date for report"),
    end_date: Optional[datetime] = Query(None, description="End date for report"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive coach reports"""
    return await ReportsController.get_coach_reports(
        current_user, branch_id, start_date, end_date
    )

@router.get("/branches")
async def get_branch_reports(
    branch_id: Optional[str] = Query(None, description="Filter by specific branch ID"),
    metric: Optional[str] = Query(None, description="Filter by performance metric"),
    date_range: Optional[str] = Query(None, description="Filter by date range"),
    status: Optional[str] = Query(None, description="Filter by branch status"),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Number of records to return"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive branch reports with filtering and search"""
    return await ReportsController.get_branch_reports(
        current_user, branch_id, metric, date_range, status, skip, limit
    )

@router.get("/branches/filters")
async def get_branch_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for branch reports"""
    return await ReportsController.get_branch_report_filters(current_user)

@router.get("/courses")
async def get_course_reports(
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    category_id: Optional[str] = Query(None, description="Filter by category ID"),
    difficulty_level: Optional[str] = Query(None, description="Filter by difficulty level"),
    active_only: bool = Query(True, description="Filter only active courses"),
    search: Optional[str] = Query(None, description="Search by title, code, or description"),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Number of records to return"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive course reports with filtering and search"""
    return await ReportsController.get_course_reports(
        current_user, branch_id, category_id, difficulty_level, active_only, search, skip, limit
    )

@router.get("/filters")
async def get_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for reports"""
    return await ReportsController.get_report_filters(current_user)

# Individual financial report endpoints matching the reference image
@router.get("/financial/total-balance-fees-statement")
async def get_total_balance_fees_statement(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Total Balance Fees Statement report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Total Balance Fees Statement",
        "data": result["financial_reports"]["total_balance_fees_statement"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/balance-fees-statement")
async def get_balance_fees_statement(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Balance Fees Statement report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Balance Fees Statement",
        "data": result["financial_reports"]["balance_fees_statement"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/daily-collection-report")
async def get_daily_collection_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Daily Collection Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Daily Collection Report",
        "data": result["financial_reports"]["daily_collection_report"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/type-wise-balance-report")
async def get_type_wise_balance_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Type Wise Balance Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Type Wise Balance Report",
        "data": result["financial_reports"]["type_wise_balance_report"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/fees-statement")
async def get_fees_statement(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Fees Statement report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Fees Statement",
        "data": result["financial_reports"]["fees_statement"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/total-fee-collection-report")
async def get_total_fee_collection_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Total Fee Collection Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Total Fee Collection Report",
        "data": result["financial_reports"]["total_fee_collection_report"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/other-fees-collection-report")
async def get_other_fees_collection_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Other Fees Collection Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Other Fees Collection Report",
        "data": result["financial_reports"]["other_fees_collection_report"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/online-fees-collection-report")
async def get_online_fees_collection_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Online Fees Collection Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Online Fees Collection Report",
        "data": result["financial_reports"]["online_fees_collection_report"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/balance-fees-report-with-remark")
async def get_balance_fees_report_with_remark(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Balance Fees Report With Remark"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Balance Fees Report With Remark",
        "data": result["financial_reports"]["balance_fees_report_with_remark"],
        "generated_at": result["generated_at"]
    }

@router.get("/financial/expense-report")
async def get_expense_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Expense Report (placeholder for future implementation)"""
    return {
        "report_type": "Expense Report",
        "data": {"message": "Expense reporting feature coming soon"},
        "generated_at": datetime.utcnow()
    }

@router.get("/financial/payroll-report")
async def get_payroll_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Payroll Report (placeholder for future implementation)"""
    return {
        "report_type": "Payroll Report",
        "data": {"message": "Payroll reporting feature coming soon"},
        "generated_at": datetime.utcnow()
    }

@router.get("/financial/income-report")
async def get_income_report(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    branch_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get Income Report"""
    result = await ReportsController.get_financial_reports(
        current_user, start_date, end_date, branch_id
    )
    return {
        "report_type": "Income Report",
        "data": {
            "total_income": result["financial_reports"]["total_balance_fees_statement"]["total_amount"],
            "monthly_breakdown": result["financial_reports"]["total_fee_collection_report"]
        },
        "generated_at": result["generated_at"]
    }

# Master Report endpoints
@router.get("/masters")
async def get_master_reports(
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    course_id: Optional[str] = Query(None, description="Filter by course ID"),
    area_of_expertise: Optional[str] = Query(None, description="Filter by area of expertise"),
    professional_experience: Optional[str] = Query(None, description="Filter by professional experience level"),
    designation_id: Optional[str] = Query(None, description="Filter by designation"),
    active_only: bool = Query(True, description="Filter only active masters"),
    search: Optional[str] = Query(None, description="Search by name, email, or phone"),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Number of records to return"),
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get comprehensive master (coach) reports with filtering and search"""
    return await ReportsController.get_master_reports(
        current_user, branch_id, course_id, area_of_expertise,
        professional_experience, designation_id, active_only, search, skip, limit
    )

@router.get("/masters/filters")
async def get_master_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for master reports"""
    return await ReportsController.get_master_report_filters(current_user)

@router.get("/courses/filters")
async def get_course_report_filters(
    current_user: dict = Depends(get_current_user_or_superadmin)
):
    """Get available filter options for course reports"""
    return await ReportsController.get_course_report_filters(current_user)
