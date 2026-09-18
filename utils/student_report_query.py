"""
M08-S02 reusable student report query + export helpers.

Supports All / Active / Inactive / Branch / Course / Branch+Course filters
with Branch Manager scope enforcement. Does not mutate enrollments or payments.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException

from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "student_id",
    "full_name",
    "email",
    "phone",
    "is_active",
    "gender",
    "date_of_birth",
    "branch_names",
    "course_names",
    "total_enrollments",
    "created_at",
]


def _role(user: dict) -> str:
    return str((user or {}).get("role") or "").lower()


def _parse_iso_date(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        if end_of_day:
            dt = dt + timedelta(days=1)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


async def resolve_authorized_branch_scope(
    db,
    current_user: dict,
    requested_branch_id: Optional[str] = None,
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Returns (allowed_branch_ids_or_None_for_all, effective_branch_id).

    For branch managers:
      - allowed = managed branches
      - requesting another branch => 403
    """
    role = _role(current_user)
    req = (requested_branch_id or "").strip()
    if req in {"", "all"}:
        req = ""

    if role in {"super_admin", "superadmin"}:
        return (None, req or None)

    if role == "coach_admin":
        own = current_user.get("branch_id")
        allowed = [str(own)] if own else await get_managed_branch_ids_for_user(db, current_user)
        if not allowed:
            return ([], None)
        if req and req not in allowed:
            raise HTTPException(
                status_code=403,
                detail="You cannot filter or export students outside your assigned branch.",
            )
        return (allowed, req or None)

    if role == "branch_manager":
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            return ([], None)
        if req and req not in managed:
            raise HTTPException(
                status_code=403,
                detail="Branch Managers cannot export or filter other branch data.",
            )
        # If no specific branch requested, scope to all managed
        return (managed, req or None)

    if role == "coach":
        own = current_user.get("branch_id")
        allowed = [str(own)] if own else []
        if not allowed:
            return ([], None)
        if req and req not in allowed:
            raise HTTPException(status_code=403, detail="Not allowed for this branch.")
        return (allowed, req or None)

    raise HTTPException(status_code=403, detail="Not allowed to access student reports")


async def query_student_report_rows(
    current_user: dict,
    *,
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = 0,
    limit: int = 5000,
) -> Dict[str, Any]:
    """
    Shared student report query used by list JSON and CSV/Excel export.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    allowed_branches, effective_branch = await resolve_authorized_branch_scope(
        db, current_user, branch_id
    )
    if allowed_branches is not None and len(allowed_branches) == 0:
        return {
            "students": [],
            "total": 0,
            "count": 0,
            "filters": {
                "q": q,
                "branch_id": branch_id,
                "course_id": course_id,
                "is_active": is_active,
                "start_date": start_date,
                "end_date": end_date,
            },
            "message": "No branches assigned",
        }

    student_filter: Dict[str, Any] = {"role": "student"}
    if is_active is not None:
        student_filter["is_active"] = bool(is_active)

    if q and len(str(q).strip()) >= 2:
        pattern = {"$regex": re.escape(str(q).strip()), "$options": "i"}
        student_filter["$or"] = [
            {"full_name": pattern},
            {"first_name": pattern},
            {"last_name": pattern},
            {"email": pattern},
            {"phone": pattern},
            {"id": pattern},
        ]

    start_dt = _parse_iso_date(start_date)
    end_dt = _parse_iso_date(end_date, end_of_day=True)
    if start_dt or end_dt:
        created: Dict[str, Any] = {}
        if start_dt:
            created["$gte"] = start_dt
        if end_dt:
            created["$lt"] = end_dt
        student_filter["created_at"] = created

    # Enrollment-scoped student ids when branch/course filters apply
    course_ref = (course_id or "").strip()
    if course_ref in {"", "all"}:
        course_ref = ""

    need_enrollment_scope = bool(allowed_branches) or bool(effective_branch) or bool(course_ref)
    scoped_student_ids: Optional[List[str]] = None
    if need_enrollment_scope:
        enr_filter: Dict[str, Any] = {}
        # Include inactive enrollments so inactive account still appears when filtering by branch history
        branch_scope = [effective_branch] if effective_branch else allowed_branches
        if branch_scope:
            enr_filter["branch_id"] = {"$in": branch_scope}
        if course_ref:
            enr_filter["course_id"] = course_ref
        if enr_filter:
            scoped_student_ids = await db.enrollments.distinct("student_id", enr_filter)
            if not scoped_student_ids:
                return {
                    "students": [],
                    "total": 0,
                    "count": 0,
                    "filters": {
                        "q": q,
                        "branch_id": effective_branch or branch_id,
                        "course_id": course_ref or course_id,
                        "is_active": is_active,
                        "start_date": start_date,
                        "end_date": end_date,
                        "scoped_branches": branch_scope,
                    },
                    "message": "No students match the selected filters",
                }
            student_filter["id"] = {"$in": scoped_student_ids}

    skip = max(0, int(skip or 0))
    limit = max(1, min(int(limit or 50), 10000))

    total = await db.users.count_documents(student_filter)
    cursor = db.users.find(student_filter).sort("created_at", -1).skip(skip).limit(limit)
    students = await cursor.to_list(length=limit)

    enriched: List[Dict[str, Any]] = []
    for student in students:
        sid = student.get("id")
        enrollments = await db.enrollments.find({"student_id": sid}).to_list(length=100)
        # Prefer active enrollments for display, but keep all for counts
        courses: List[Dict[str, Any]] = []
        branches: List[Dict[str, Any]] = []
        for enrollment in enrollments:
            course = await db.courses.find_one({"id": enrollment.get("course_id")})
            if course:
                courses.append(
                    {
                        "id": course.get("id"),
                        "name": course.get("name") or course.get("title") or "Course",
                        "code": course.get("code") or "",
                        "payment_status": enrollment.get("payment_status"),
                        "is_active": enrollment.get("is_active", True),
                    }
                )
            branch = await db.branches.find_one({"id": enrollment.get("branch_id")})
            if branch:
                bname = (branch.get("branch") or {}).get("name") or branch.get("name") or "Branch"
                bid = branch.get("id")
                if bid and not any(b.get("id") == bid for b in branches):
                    branches.append(
                        {
                            "id": bid,
                            "name": bname,
                            "code": (branch.get("branch") or {}).get("code") or "",
                        }
                    )

        row = {
            "id": sid,
            "student_id": sid,
            "full_name": student.get("full_name")
            or f"{student.get('first_name', '')} {student.get('last_name', '')}".strip(),
            "email": student.get("email"),
            "phone": student.get("phone"),
            "is_active": bool(student.get("is_active", True)),
            "gender": student.get("gender"),
            "date_of_birth": student.get("date_of_birth"),
            "created_at": student.get("created_at"),
            "courses": courses,
            "branches": branches,
            "total_enrollments": len(enrollments),
            "active_enrollments": len([e for e in enrollments if e.get("is_active", True)]),
            "branch_names": ", ".join(b["name"] for b in branches) if branches else "",
            "course_names": ", ".join(
                dict.fromkeys(c["name"] for c in courses if c.get("name"))
            ),
        }
        enriched.append(row)

    return {
        "students": serialize_doc(enriched),
        "total": total,
        "count": len(enriched),
        "skip": skip,
        "limit": limit,
        "filters": {
            "q": q,
            "branch_id": effective_branch or branch_id,
            "course_id": course_ref or course_id,
            "is_active": is_active,
            "start_date": start_date,
            "end_date": end_date,
            "scoped_branches": allowed_branches,
        },
        "message": f"Found {total} student(s)",
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "active" if value else "inactive"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def build_student_export_csv(rows: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _cell(row.get(field)) for field in EXPORT_FIELDS})
    return output.getvalue()


def build_student_export_excel_xml(rows: List[Dict[str, Any]]) -> str:
    """Simple SpreadsheetML workbook Excel can open (no openpyxl dependency)."""
    header_cells = "".join(
        f'<Cell><Data ss:Type="String">{xml_escape(h)}</Data></Cell>' for h in EXPORT_FIELDS
    )
    body_rows = []
    for row in rows:
        cells = "".join(
            f'<Cell><Data ss:Type="String">{xml_escape(_cell(row.get(field)))}</Data></Cell>'
            for field in EXPORT_FIELDS
        )
        body_rows.append(f"<Row>{cells}</Row>")
    return (
        '<?xml version="1.0"?>\n'
        '<?mso-application progid="Excel.Sheet"?>\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
        "<Worksheet ss:Name=\"Students\">\n"
        "<Table>\n"
        f"<Row>{header_cells}</Row>\n"
        + "\n".join(body_rows)
        + "\n</Table>\n</Worksheet>\n</Workbook>\n"
    )


async def export_student_report(
    current_user: dict,
    *,
    format: str = "csv",
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    result = await query_student_report_rows(
        current_user,
        q=q,
        branch_id=branch_id,
        course_id=course_id,
        is_active=is_active,
        start_date=start_date,
        end_date=end_date,
        skip=0,
        limit=10000,
    )
    rows = result.get("students") or []
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fmt = (format or "csv").strip().lower()
    if fmt in {"xlsx", "xls", "excel"}:
        content = build_student_export_excel_xml(rows)
        return {
            "content": content,
            "filename": f"student_report_{stamp}.xls",
            "content_type": "application/vnd.ms-excel",
            "total": len(rows),
            "filters": result.get("filters"),
        }
    if fmt != "csv":
        raise HTTPException(status_code=400, detail="Unsupported export format. Use csv or excel.")
    content = build_student_export_csv(rows)
    return {
        "content": content,
        "filename": f"student_report_{stamp}.csv",
        "content_type": "text/csv",
        "total": len(rows),
        "filters": result.get("filters"),
    }
