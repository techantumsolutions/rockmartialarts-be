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
    if role == "branch_manager":
        managed = await db.branches.find(
            {"manager_id": current_user.get("id"), "is_active": True}
        ).to_list(length=None)
        ids = [b["id"] for b in managed]
        if not ids:
            return {"branch_id": {"$in": []}}
        return {"branch_id": {"$in": ids}}
    if branch_id:
        return {"branch_id": branch_id}
    return {}


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
) -> None:
    try:
        db = get_db()
        await db.export_audit_logs.insert_one(
            {
                "id": str(uuid.uuid4()),
                "user_id": current_user.get("id"),
                "role": current_user.get("role"),
                "report_type": report_type,
                "filters": filters,
                "row_count": row_count,
                "format": fmt,
                "mode": mode,
                "created_at": datetime.utcnow(),
            }
        )
    except Exception:
        pass


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
    coll = db[collection]
    total = await coll.count_documents(q)
    rows = await coll.find(q).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    return {"rows": serialize_doc(rows), "total": total, "skip": skip, "limit": limit}


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
        await db.export_jobs.insert_one(
            {
                "id": job_id,
                "user_id": current_user.get("id"),
                "role": current_user.get("role"),
                "report_type": report_type,
                "status": "pending",
                "format": fmt,
                "filters": filters,
                "download_token": download_token,
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
        await write_export_audit(current_user, report_type, filters, total, fmt, mode="async")
        return {"async": True, "job_id": job_id, "status": "pending", "estimated_rows": total}

    rows = await coll.find(q).sort("created_at", -1).limit(10000).to_list(length=10000)
    serialized = serialize_doc(rows) or []
    if not fieldnames and serialized:
        fieldnames = list(serialized[0].keys())[:20]
    fieldnames = fieldnames or ["id"]
    content = _to_csv(serialized, fieldnames) if fmt == "csv" else _to_excel_xml(serialized, fieldnames)
    filename = f"{report_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{'csv' if fmt == 'csv' else 'xls'}"
    await write_export_audit(current_user, report_type, filters, len(serialized), fmt, mode="sync")
    return {"content": content, "filename": filename, "row_count": len(serialized), "async": False}


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
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


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
