"""
M10-S01 KPI Master service.

CRUD + weight/max-score validation for `kpi_definitions`.
Does not touch student performance skills, attendance, or payments.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.kpi_models import (
    KpiDefinitionCreate,
    KpiDefinitionDocument,
    KpiDefinitionUpdate,
    KpiWeightUnit,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)

COL = "kpi_definitions"
# Soft tolerance for floating sum of percent weights
PERCENT_SUM_TARGET = 100.0
PERCENT_SUM_TOLERANCE = 0.01


def normalize_kpi_code(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "_")


async def ensure_kpi_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index("code", unique=True, name="kpi_definitions_code")
        await database[COL].create_index(
            [("is_active", 1), ("sort_order", 1)],
            name="kpi_definitions_active_sort",
        )
    except Exception:
        logger.exception("Failed ensuring kpi_definitions indexes")


def _validate_scores(min_score: float, max_score: float) -> None:
    if max_score <= 0:
        raise HTTPException(status_code=400, detail="max_score must be greater than 0")
    if min_score < 0:
        raise HTTPException(status_code=400, detail="min_score cannot be negative")
    if min_score > max_score:
        raise HTTPException(status_code=400, detail="min_score cannot exceed max_score")


def _validate_weight(weight: float, weight_unit: str) -> None:
    if weight is None or weight <= 0:
        raise HTTPException(status_code=400, detail="weight must be greater than 0")
    unit = str(weight_unit or "percent").lower()
    if unit == KpiWeightUnit.PERCENT.value and weight > 100:
        raise HTTPException(
            status_code=400,
            detail="Percent weight cannot exceed 100 for a single KPI",
        )


async def _active_weight_summary(
    db,
    *,
    exclude_id: Optional[str] = None,
    proposed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Summarize active KPI weights.

    For percent-mode KPIs, sum of active percent weights should equal 100
    (within tolerance) when the set is ready for rating calculation.
    """
    query: Dict[str, Any] = {"is_active": True}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    rows = await db[COL].find(query).to_list(length=500)
    if proposed and proposed.get("is_active", True):
        rows.append(proposed)

    percent_sum = 0.0
    points_sum = 0.0
    percent_count = 0
    points_count = 0
    for row in rows:
        unit = str(row.get("weight_unit") or "percent").lower()
        w = float(row.get("weight") or 0)
        if unit == KpiWeightUnit.POINTS.value:
            points_sum += w
            points_count += 1
        else:
            percent_sum += w
            percent_count += 1

    percent_ok = True
    percent_message = None
    if percent_count > 0:
        diff = abs(percent_sum - PERCENT_SUM_TARGET)
        if diff > PERCENT_SUM_TOLERANCE:
            percent_ok = False
            percent_message = (
                f"Active percent weights sum to {percent_sum:.2f}; "
                f"target is {PERCENT_SUM_TARGET:.0f}."
            )

    return {
        "active_count": len(rows),
        "percent_count": percent_count,
        "points_count": points_count,
        "percent_weight_sum": round(percent_sum, 4),
        "points_weight_sum": round(points_sum, 4),
        "percent_sum_ok": percent_ok,
        "percent_message": percent_message,
        "target_percent_sum": PERCENT_SUM_TARGET,
    }


async def list_kpi_definitions(
    *,
    active_only: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_indexes(db)
    query: Dict[str, Any] = {}
    if active_only:
        query["is_active"] = True
    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(query)
    rows = (
        await db[COL]
        .find(query)
        .sort([("sort_order", 1), ("name", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    summary = await _active_weight_summary(db)
    return {
        "kpis": serialize_doc(rows),
        "total": total,
        "count": len(rows),
        "weight_summary": summary,
    }


async def get_kpi_definition(kpi_id: str) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": kpi_id})
    if not doc:
        raise HTTPException(status_code=404, detail="KPI definition not found")
    return {"kpi": serialize_doc(doc)}


async def create_kpi_definition(
    body: KpiDefinitionCreate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_kpi_indexes(db)

    code = normalize_kpi_code(body.code)
    if not code:
        raise HTTPException(status_code=400, detail="KPI code is required")
    if await db[COL].find_one({"code": code}):
        raise HTTPException(status_code=409, detail="A KPI with this code already exists")

    _validate_weight(body.weight, body.weight_unit.value)
    _validate_scores(body.min_score, body.max_score)

    doc = KpiDefinitionDocument(
        code=code,
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        weight=float(body.weight),
        weight_unit=body.weight_unit,
        max_score=float(body.max_score),
        min_score=float(body.min_score),
        sort_order=body.sort_order,
        is_active=body.is_active,
        branch_ids=[b for b in (body.branch_ids or []) if b],
        course_ids=[c for c in (body.course_ids or []) if c],
        created_by=current_user.get("id"),
        updated_by=current_user.get("id"),
    )
    payload = doc.dict()
    # Store enums as values
    payload["weight_unit"] = body.weight_unit.value

    # Validate resulting active weight set (warn via summary; block only if single percent > 100 already handled)
    summary = await _active_weight_summary(
        db,
        proposed={
            "weight": payload["weight"],
            "weight_unit": payload["weight_unit"],
            "is_active": payload["is_active"],
        },
    )

    await db[COL].insert_one(payload)
    return {
        "kpi": serialize_doc(payload),
        "weight_summary": summary,
        "message": "KPI created successfully",
    }


async def update_kpi_definition(
    kpi_id: str,
    body: KpiDefinitionUpdate,
    *,
    current_user: dict,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    existing = await db[COL].find_one({"id": kpi_id})
    if not existing:
        raise HTTPException(status_code=404, detail="KPI definition not found")

    patch = body.dict(exclude_unset=True)
    if "code" in patch and patch["code"] is not None:
        code = normalize_kpi_code(patch["code"])
        if not code:
            raise HTTPException(status_code=400, detail="KPI code is required")
        other = await db[COL].find_one({"code": code, "id": {"$ne": kpi_id}})
        if other:
            raise HTTPException(status_code=409, detail="A KPI with this code already exists")
        patch["code"] = code
    if "name" in patch and patch["name"] is not None:
        patch["name"] = str(patch["name"]).strip()
        if not patch["name"]:
            raise HTTPException(status_code=400, detail="KPI name is required")
    if "description" in patch and patch["description"] is not None:
        patch["description"] = str(patch["description"]).strip() or None
    if "weight_unit" in patch and patch["weight_unit"] is not None:
        unit = patch["weight_unit"]
        patch["weight_unit"] = unit.value if hasattr(unit, "value") else str(unit)
    if "branch_ids" in patch and patch["branch_ids"] is not None:
        patch["branch_ids"] = [b for b in patch["branch_ids"] if b]
    if "course_ids" in patch and patch["course_ids"] is not None:
        patch["course_ids"] = [c for c in patch["course_ids"] if c]

    merged_weight = float(patch.get("weight", existing.get("weight") or 0))
    merged_unit = str(patch.get("weight_unit", existing.get("weight_unit") or "percent"))
    merged_min = float(patch.get("min_score", existing.get("min_score") or 0))
    merged_max = float(patch.get("max_score", existing.get("max_score") or 100))
    _validate_weight(merged_weight, merged_unit)
    _validate_scores(merged_min, merged_max)

    patch["updated_at"] = datetime.utcnow()
    patch["updated_by"] = current_user.get("id")
    await db[COL].update_one({"id": kpi_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": kpi_id})
    summary = await _active_weight_summary(db)
    return {
        "kpi": serialize_doc(updated),
        "weight_summary": summary,
        "message": "KPI updated successfully",
    }


async def set_kpi_active(
    kpi_id: str,
    *,
    is_active: bool,
    current_user: dict,
) -> Dict[str, Any]:
    return await update_kpi_definition(
        kpi_id,
        KpiDefinitionUpdate(is_active=is_active),
        current_user=current_user,
    )


async def delete_kpi_definition(kpi_id: str) -> Dict[str, Any]:
    """Hard delete. Prefer deactivating for historical integrity once assessments exist."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    res = await db[COL].delete_one({"id": kpi_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="KPI definition not found")
    summary = await _active_weight_summary(db)
    return {"success": True, "weight_summary": summary}


async def get_weight_summary() -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    return await _active_weight_summary(db)
