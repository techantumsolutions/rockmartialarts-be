"""
M09-S04 attendance history report helpers (filters + CSV/Excel export).

Additive utilities used by AttendanceController.get_attendance_reports / export.
Does not change daily mark flows.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException

EXPORT_FIELDS = [
    "attendance_date",
    "student_name",
    "course_name",
    "branch_name",
    "status",
    "is_present",
    "check_in_time",
    "check_out_time",
    "method",
    "notes",
]


def normalize_status_value(record: Dict[str, Any]) -> str:
    raw = str(record.get("status") or "").strip().lower()
    if raw in {"present", "absent", "late"}:
        return raw
    if record.get("is_present") is True:
        return "present"
    if record.get("is_present") is False:
        return "absent"
    return "unknown"


def apply_status_filter(filter_query: Dict[str, Any], status: Optional[str]) -> None:
    """Mutate filter_query with Mongo conditions for present/absent/late."""
    if not status:
        return
    s = str(status).strip().lower()
    if s not in {"present", "absent", "late"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid status filter. Use present, absent, or late.",
        )
    if s == "late":
        filter_query["status"] = {"$regex": "^late$", "$options": "i"}
        return
    if s == "present":
        filter_query["$or"] = [
            {"status": {"$regex": "^present$", "$options": "i"}},
            {
                "$and": [
                    {
                        "$or": [
                            {"status": {"$exists": False}},
                            {"status": None},
                            {"status": ""},
                        ]
                    },
                    {"is_present": True},
                ]
            },
        ]
        return
    # absent
    filter_query["$or"] = [
        {"status": {"$regex": "^absent$", "$options": "i"}},
        {
            "$and": [
                {
                    "$or": [
                        {"status": {"$exists": False}},
                        {"status": None},
                        {"status": ""},
                    ]
                },
                {"is_present": False},
            ]
        },
    ]


def apply_method_filter(filter_query: Dict[str, Any], method: Optional[str]) -> None:
    if not method:
        return
    m = str(method).strip()
    if not m or m.lower() == "all":
        return
    filter_query["method"] = {"$regex": f"^{re.escape(m)}$", "$options": "i"}


def summarize_records(records: List[Dict[str, Any]]) -> Dict[str, int]:
    present = absent = late = biometric = 0
    for row in records:
        st = normalize_status_value(row)
        if st == "present":
            present += 1
        elif st == "absent":
            absent += 1
        elif st == "late":
            late += 1
        method = str(row.get("method") or "").lower()
        if "biometric" in method:
            biometric += 1
    return {
        "total": len(records),
        "present": present,
        "absent": absent,
        "late": late,
        "biometric": biometric,
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def build_attendance_export_csv(rows: List[Dict[str, Any]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        enriched = dict(row)
        enriched["status"] = normalize_status_value(row)
        writer.writerow({field: _cell(enriched.get(field)) for field in EXPORT_FIELDS})
    return output.getvalue()


def build_attendance_export_excel_xml(rows: List[Dict[str, Any]]) -> str:
    """SpreadsheetML workbook Excel can open (no openpyxl dependency)."""
    header_cells = "".join(
        f'<Cell><Data ss:Type="String">{xml_escape(h)}</Data></Cell>' for h in EXPORT_FIELDS
    )
    body_rows = []
    for row in rows:
        enriched = dict(row)
        enriched["status"] = normalize_status_value(row)
        cells = "".join(
            f'<Cell><Data ss:Type="String">{xml_escape(_cell(enriched.get(field)))}</Data></Cell>'
            for field in EXPORT_FIELDS
        )
        body_rows.append(f"<Row>{cells}</Row>")
    return (
        '<?xml version="1.0"?>\n'
        '<?mso-application progid="Excel.Sheet"?>\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
        '<Worksheet ss:Name="Attendance">\n'
        "<Table>\n"
        f"<Row>{header_cells}</Row>\n"
        + "\n".join(body_rows)
        + "\n</Table>\n</Worksheet>\n</Workbook>\n"
    )


def build_attendance_export(
    rows: List[Dict[str, Any]],
    *,
    format: str = "csv",
) -> Dict[str, Any]:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fmt = (format or "csv").strip().lower()
    if fmt in {"xlsx", "xls", "excel"}:
        return {
            "content": build_attendance_export_excel_xml(rows),
            "filename": f"attendance_report_{stamp}.xls",
            "content_type": "application/vnd.ms-excel",
            "total": len(rows),
        }
    if fmt != "csv":
        raise HTTPException(status_code=400, detail="Unsupported export format. Use csv or excel.")
    return {
        "content": build_attendance_export_csv(rows),
        "filename": f"attendance_report_{stamp}.csv",
        "content_type": "text/csv",
        "total": len(rows),
    }
