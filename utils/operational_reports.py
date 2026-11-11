"""M23-S04/S05: Operational report list/export helpers + export jobs/audit."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import Response

from utils.database import get_db
from utils.helpers import serialize_doc

EXPORT_ROW_THRESHOLD = 5000
EXPORT_JOB_TTL_HOURS = 24


def _period(start_date: Optional[str], end_date: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
    if not start_date and not end_date:
        return None, None
    try:
        ps = datetime.strptime((start_date or "1970-01-01")[:10], "%Y-%m-%d")
        pe = datetime.strptime((end_date or datetime.utcnow().strftime("%Y-%m-%d"))[:10], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        return ps, pe
    except ValueError:
        return None, None


async def _branch_scope(current_user: dict, branch_id: Optional[str]) -> Dict[str, Any]:
    role = current_user.get("role")
    db = get_db()
    from utils.partner_tenant import partner_report_branch_ids

    partner_ids = await partner_report_branch_ids(db, current_user, branch_id)
    if partner_ids is not None:
        return {"branch_id": {"$in": partner_ids}}
    if role == "branch_manager":
        from utils.student_status_service import get_managed_branch_ids_for_user

        ids = await get_managed_branch_ids_for_user(db, current_user)
        if not ids:
            return {"branch_id": {"$in": []}}
        return {"branch_id": {"$in": ids}}
    if branch_id:
        return {"branch_id": branch_id}
    return {}


def _apply_lead_tenant_scope(
    collection: str, current_user: dict, query: Dict[str, Any]
) -> Dict[str, Any]:
    """Rock lead reports exclude white-label partner contact leads."""
    if collection != "leads":
        return query
    from utils.partner_tenant import (
        is_partner_super_admin,
        merge_and_clause,
        rock_owned_clause,
    )

    if is_partner_super_admin(current_user):
        return query
    return merge_and_clause(query, rock_owned_clause())


def _to_csv(rows: List[Dict[str, Any]], fieldnames: List[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return buf.getvalue()


def _to_excel_xml(rows: List[Dict[str, Any]], fieldnames: List[str]) -> str:
    """SpreadsheetML (Excel-compatible) used elsewhere in the codebase."""
    cells = []
    header = "".join(f'<Cell><Data ss:Type="String">{h}</Data></Cell>' for h in fieldnames)
    cells.append(f"<Row>{header}</Row>")
    for r in rows:
        row_cells = "".join(
            f'<Cell><Data ss:Type="String">{str(r.get(h, "")).replace("&", "&amp;").replace("<", "&lt;")}</Data></Cell>'
            for h in fieldnames
        )
        cells.append(f"<Row>{row_cells}</Row>")
    return (
        '<?xml version="1.0"?>'
        '<?mso-application progid="Excel.Sheet"?>'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        "<Worksheet ss:Name=\"Report\"><Table>"
        + "".join(cells)
        + "</Table></Worksheet></Workbook>"
    )


async def write_export_audit(
    current_user: dict,
    report_type: str,
    filters: dict,
    row_count: int,
    fmt: str,
    mode: str = "sync",
    job_id: Optional[str] = None,
) -> Optional[str]:
    """Record who exported what and when. Returns audit log id when inserted."""
    audit_id = str(uuid.uuid4())
    try:
        db = get_db()
        await db.export_audit_logs.insert_one(
            {
                "id": audit_id,
                "user_id": current_user.get("id"),
                "user_email": current_user.get("email"),
                "user_name": current_user.get("full_name")
                or f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip()
                or None,
                "role": current_user.get("role"),
                "report_type": report_type,
                "filters": filters,
                "row_count": row_count,
                "format": fmt,
                "mode": mode,
                "job_id": job_id,
                "created_at": datetime.utcnow(),
            }
        )
        return audit_id
    except Exception:
        import logging

        logging.exception("Failed to write export audit log for %s", report_type)
        return None


async def _enrich_named_entity_rows(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add readable student/course/branch names for enrollment and renewal (payment) rows."""
    student_cache: Dict[str, str] = {}
    course_cache: Dict[str, str] = {}
    branch_cache: Dict[str, str] = {}

    async def _student_name(sid: str) -> str:
        if sid in student_cache:
            return student_cache[sid]
        user = await db.users.find_one({"id": sid})
        name = ""
        if user:
            name = (
                user.get("full_name")
                or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                or user.get("email")
                or ""
            )
        student_cache[sid] = name or sid
        return student_cache[sid]

    async def _course_name(cid: str) -> str:
        if cid in course_cache:
            return course_cache[cid]
        course = await db.courses.find_one({"id": cid})
        name = ""
        if course:
            name = course.get("title") or course.get("name") or ""
        course_cache[cid] = name or cid
        return course_cache[cid]

    async def _branch_name(bid: str) -> str:
        if bid in branch_cache:
            return branch_cache[bid]
        branch = await db.branches.find_one({"id": bid})
        name = ""
        if branch:
            name = ((branch.get("branch") or {}).get("name")) or branch.get("name") or ""
        branch_cache[bid] = name or bid
        return branch_cache[bid]

    enriched: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        sid = str(item.get("student_id") or item.get("user_id") or "").strip()
        cid = str(item.get("course_id") or "").strip()
        bid = str(item.get("branch_id") or "").strip()

        course_details = item.get("course_details") if isinstance(item.get("course_details"), dict) else {}
        branch_details = item.get("branch_details") if isinstance(item.get("branch_details"), dict) else {}

        if sid:
            item["student_name"] = await _student_name(sid)
        if cid:
            item["course_name"] = (
                course_details.get("course_name")
                or course_details.get("name")
                or course_details.get("title")
                or await _course_name(cid)
            )
        elif course_details.get("course_name") or course_details.get("name") or course_details.get("title"):
            item["course_name"] = (
                course_details.get("course_name")
                or course_details.get("name")
                or course_details.get("title")
            )
        if bid:
            item["branch_name"] = (
                branch_details.get("branch_name")
                or branch_details.get("name")
                or await _branch_name(bid)
            )
        elif branch_details.get("branch_name") or branch_details.get("name"):
            item["branch_name"] = branch_details.get("branch_name") or branch_details.get("name")

        enriched.append(item)
    return enriched


async def list_operational(
    collection: str,
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    extra_query: Optional[dict] = None,
) -> dict:
    db = get_db()
    q: Dict[str, Any] = dict(extra_query or {})
    q.update(await _branch_scope(current_user, branch_id))
    if course_id:
        q["course_id"] = course_id
    ps, pe = _period(start_date, end_date)
    if ps and pe:
        q["created_at"] = {"$gte": ps, "$lte": pe}
    q = _apply_lead_tenant_scope(collection, current_user, q)
    coll = db[collection]
    total = await coll.count_documents(q)
    rows = await coll.find(q).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    serialized = serialize_doc(rows)
    if collection in ("enrollments", "payments") and isinstance(serialized, list):
        serialized = await _enrich_named_entity_rows(db, serialized)
    return {"rows": serialized, "total": total, "skip": skip, "limit": limit}


async def export_operational(
    collection: str,
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    format: str = "csv",
    fieldnames: Optional[List[str]] = None,
    extra_query: Optional[dict] = None,
    report_type: str = "operational",
    background_tasks: Optional[BackgroundTasks] = None,
) -> Any:
    db = get_db()
    q: Dict[str, Any] = dict(extra_query or {})
    q.update(await _branch_scope(current_user, branch_id))
    if course_id:
        q["course_id"] = course_id
    ps, pe = _period(start_date, end_date)
    if ps and pe:
        q["created_at"] = {"$gte": ps, "$lte": pe}
    q = _apply_lead_tenant_scope(collection, current_user, q)

    coll = db[collection]
    total = await coll.count_documents(q)
    fmt = (format or "csv").lower()
    if fmt not in ("csv", "excel"):
        raise HTTPException(status_code=400, detail="format must be csv or excel")

    filters = {
        "branch_id": branch_id,
        "course_id": course_id,
        "start_date": start_date,
        "end_date": end_date,
        "collection": collection,
    }

    if total > EXPORT_ROW_THRESHOLD and background_tasks is not None:
        job_id = str(uuid.uuid4())
        download_token = str(uuid.uuid4())
        audit_id = await write_export_audit(
            current_user, report_type, filters, total, fmt, mode="async", job_id=job_id
        )
        await db.export_jobs.insert_one(
            {
                "id": job_id,
                "user_id": current_user.get("id"),
                "user_email": current_user.get("email"),
                "role": current_user.get("role"),
                "report_type": report_type,
                "status": "pending",
                "format": fmt,
                "filters": filters,
                "download_token": download_token,
                "audit_id": audit_id,
                "created_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(hours=EXPORT_JOB_TTL_HOURS),
            }
        )
        background_tasks.add_task(
            _run_export_job,
            job_id,
            collection,
            q,
            fieldnames,
            fmt,
            current_user,
            report_type,
            filters,
        )
        return {"async": True, "job_id": job_id, "status": "pending", "estimated_rows": total}

    rows = await coll.find(q).sort("created_at", -1).limit(10000).to_list(length=10000)
    serialized = serialize_doc(rows) or []
    if collection in ("enrollments", "payments") and isinstance(serialized, list):
        serialized = await _enrich_named_entity_rows(db, serialized)
    if not fieldnames and serialized:
        fieldnames = list(serialized[0].keys())[:20]
    fieldnames = fieldnames or ["id"]
    content = _to_csv(serialized, fieldnames) if fmt == "csv" else _to_excel_xml(serialized, fieldnames)
    filename = f"{report_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{'csv' if fmt == 'csv' else 'xls'}"
    await write_export_audit(current_user, report_type, filters, len(serialized), fmt, mode="sync")
    media = "text/csv" if fmt == "csv" else "application/vnd.ms-excel"
    return {
        "content": content,
        "filename": filename,
        "row_count": len(serialized),
        "async": False,
        "content_type": media,
    }


async def _run_export_job(
    job_id: str,
    collection: str,
    query: dict,
    fieldnames: Optional[List[str]],
    fmt: str,
    current_user: dict,
    report_type: str,
    filters: dict,
) -> None:
    db = get_db()
    try:
        await db.export_jobs.update_one({"id": job_id}, {"$set": {"status": "processing"}})
        rows = await db[collection].find(query).sort("created_at", -1).limit(50000).to_list(length=50000)
        serialized = serialize_doc(rows) or []
        if collection in ("enrollments", "payments") and isinstance(serialized, list):
            serialized = await _enrich_named_entity_rows(db, serialized)
        fn = fieldnames or (list(serialized[0].keys())[:20] if serialized else ["id"])
        content = _to_csv(serialized, fn) if fmt == "csv" else _to_excel_xml(serialized, fn)
        await db.export_jobs.update_one(
            {"id": job_id},
            {
                "$set": {
                    "status": "ready",
                    "content": content,
                    "row_count": len(serialized),
                    "filename": f"{report_type}_{job_id[:8]}.{'csv' if fmt == 'csv' else 'xls'}",
                    "completed_at": datetime.utcnow(),
                }
            },
        )
    except Exception as e:
        await db.export_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "failed", "error": str(e)}},
        )


async def get_export_job(job_id: str, current_user: dict) -> dict:
    db = get_db()
    job = await db.export_jobs.find_one({"id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.get("user_id") != current_user.get("id") and current_user.get("role") not in (
        "super_admin",
        "superadmin",
    ):
        raise HTTPException(status_code=403, detail="Not allowed to access this export")
    return {
        "id": job["id"],
        "status": job.get("status"),
        "filename": job.get("filename"),
        "row_count": job.get("row_count"),
        "format": job.get("format"),
        "report_type": job.get("report_type"),
        "filters": job.get("filters"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "expires_at": job.get("expires_at"),
    }


async def list_attendance_operational(
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    record_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> dict:
    """Paginated attendance rows for the unified admin reports matrix."""
    from controllers.attendance_controller import AttendanceController

    cap = min(max(skip + limit, limit), 1000)
    result = await AttendanceController.get_attendance_reports(
        branch_id=branch_id,
        course_id=course_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        limit=cap,
        status=status,
        record_type=record_type or "all",
    )
    records = result.get("attendance_records") or []
    total = int(result.get("total") or len(records))
    page = records[skip : skip + limit]
    return {
        "rows": page,
        "total": total,
        "skip": skip,
        "limit": limit,
        "summary": result.get("summary"),
    }


async def export_attendance_operational(
    current_user: dict,
    *,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    record_type: Optional[str] = None,
    format: str = "csv",
    background_tasks: Optional[BackgroundTasks] = None,
) -> Any:
    from controllers.attendance_controller import AttendanceController
    from utils.attendance_report_query import build_attendance_export

    fmt = (format or "csv").lower()
    if fmt not in ("csv", "excel"):
        raise HTTPException(status_code=400, detail="format must be csv or excel")

    filters = {
        "branch_id": branch_id,
        "course_id": course_id,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "record_type": record_type or "all",
    }

    preview = await AttendanceController.get_attendance_reports(
        branch_id=branch_id,
        course_id=course_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
        limit=EXPORT_ROW_THRESHOLD + 1,
        status=status,
        record_type=record_type or "all",
    )
    records = preview.get("attendance_records") or []
    total = len(records)

    if total > EXPORT_ROW_THRESHOLD and background_tasks is not None:
        job_id = str(uuid.uuid4())
        audit_id = await write_export_audit(
            current_user, "attendance", filters, total, fmt, mode="async", job_id=job_id
        )
        db = get_db()
        await db.export_jobs.insert_one(
            {
                "id": job_id,
                "user_id": current_user.get("id"),
                "user_email": current_user.get("email"),
                "role": current_user.get("role"),
                "report_type": "attendance",
                "status": "pending",
                "format": fmt,
                "filters": filters,
                "audit_id": audit_id,
                "kind": "attendance",
                "created_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(hours=EXPORT_JOB_TTL_HOURS),
            }
        )
        background_tasks.add_task(
            _run_attendance_export_job,
            job_id,
            current_user,
            filters,
            fmt,
        )
        return {"async": True, "job_id": job_id, "status": "pending", "estimated_rows": total}

    export = build_attendance_export(records, format=fmt)
    await write_export_audit(current_user, "attendance", filters, total, fmt, mode="sync")
    return {
        "content": export["content"],
        "filename": export["filename"],
        "row_count": total,
        "async": False,
        "content_type": export.get("content_type"),
    }


async def _run_attendance_export_job(job_id: str, current_user: dict, filters: dict, fmt: str) -> None:
    from controllers.attendance_controller import AttendanceController
    from utils.attendance_report_query import build_attendance_export

    db = get_db()
    try:
        await db.export_jobs.update_one({"id": job_id}, {"$set": {"status": "processing"}})
        attendance_data = await AttendanceController.get_attendance_reports(
            branch_id=filters.get("branch_id"),
            course_id=filters.get("course_id"),
            start_date=filters.get("start_date"),
            end_date=filters.get("end_date"),
            current_user=current_user,
            limit=50000,
            status=filters.get("status"),
            record_type=filters.get("record_type") or "all",
        )
        rows = attendance_data.get("attendance_records") or []
        export = build_attendance_export(rows, format=fmt)
        await db.export_jobs.update_one(
            {"id": job_id},
            {
                "$set": {
                    "status": "ready",
                    "content": export["content"],
                    "row_count": len(rows),
                    "filename": export.get("filename") or f"attendance_{job_id[:8]}.csv",
                    "completed_at": datetime.utcnow(),
                }
            },
        )
    except Exception as e:
        await db.export_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "failed", "error": str(e), "completed_at": datetime.utcnow()}},
        )


async def download_export_job(job_id: str, current_user: dict) -> Response:
    db = get_db()
    job = await db.export_jobs.find_one({"id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.get("user_id") != current_user.get("id") and current_user.get("role") not in (
        "super_admin",
        "superadmin",
    ):
        raise HTTPException(status_code=403, detail="Not allowed")
    if job.get("status") != "ready" or not job.get("content"):
        raise HTTPException(status_code=409, detail="Export not ready")
    expires = job.get("expires_at")
    if expires and datetime.utcnow() > expires:
        raise HTTPException(status_code=410, detail="Export expired")
    media = "text/csv" if job.get("format") == "csv" else "application/vnd.ms-excel"
    return Response(
        content=job["content"],
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{job.get("filename", "export.csv")}"'},
    )
