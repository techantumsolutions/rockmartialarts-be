"""
M10-S03 pure rating engine (no DB / FastAPI imports).

See docs/M10_S03_RATING_FORMULA.md — formula_version m10-s03-v1.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

FORMULA_VERSION = "m10-s03-v1"

BAND_OUTSTANDING = "outstanding"
BAND_EXCELLENT = "excellent"
BAND_GOOD = "good"
BAND_FAIR = "fair"
BAND_NEEDS_IMPROVEMENT = "needs_improvement"


def normalize_score(raw: float, min_score: float, max_score: float) -> float:
    if max_score <= min_score:
        return 0.0
    clamped = max(float(min_score), min(float(max_score), float(raw)))
    return round(((clamped - min_score) / (max_score - min_score)) * 100.0, 4)


def rating_band(rating: float) -> str:
    r = float(rating)
    if r >= 90:
        return BAND_OUTSTANDING
    if r >= 75:
        return BAND_EXCELLENT
    if r >= 60:
        return BAND_GOOD
    if r >= 40:
        return BAND_FAIR
    return BAND_NEEDS_IMPROVEMENT


def kpi_is_applicable(
    kpi: Dict[str, Any],
    *,
    course_id: str,
    branch_id: Optional[str],
) -> bool:
    if not kpi or not kpi.get("is_active", True):
        return False
    course_ids = kpi.get("course_ids") or []
    if course_ids and course_id not in {str(c) for c in course_ids}:
        return False
    branch_ids = kpi.get("branch_ids") or []
    if branch_ids:
        if not branch_id or str(branch_id) not in {str(b) for b in branch_ids}:
            return False
    return True


def calculate_weighted_rating(
    *,
    kpis: List[Dict[str, Any]],
    assessments: List[Dict[str, Any]],
    course_id: str,
    branch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute rating from active applicable KPIs and assessment rows.

    Returns dict with rating, band, is_complete, weight_sum, breakdown, missing_kpi_ids.
    Raises ValueError when no applicable KPIs or no scored KPIs to include.
    """
    applicable = [
        k for k in kpis if kpi_is_applicable(k, course_id=course_id, branch_id=branch_id)
    ]
    if not applicable:
        raise ValueError("No active applicable KPIs for this course/branch")

    by_kpi = {}
    for a in assessments or []:
        kid = a.get("kpi_id")
        if kid:
            by_kpi[str(kid)] = a

    breakdown: List[Dict[str, Any]] = []
    missing: List[str] = []
    weighted_sum = 0.0
    weight_sum = 0.0

    for kpi in sorted(applicable, key=lambda x: (int(x.get("sort_order") or 100), str(x.get("name") or ""))):
        kid = str(kpi.get("id"))
        assessment = by_kpi.get(kid)
        if not assessment:
            missing.append(kid)
            continue

        min_score = float(
            assessment.get("min_score")
            if assessment.get("min_score") is not None
            else kpi.get("min_score") or 0
        )
        max_score = float(
            assessment.get("max_score")
            if assessment.get("max_score") is not None
            else kpi.get("max_score") or 100
        )
        raw = float(assessment.get("raw_score"))
        if assessment.get("normalized_score") is not None:
            normalized = float(assessment["normalized_score"])
        else:
            normalized = normalize_score(raw, min_score, max_score)

        weight = float(kpi.get("weight") or 0)
        if weight <= 0:
            continue

        contribution = round(normalized * weight, 6)
        weighted_sum += contribution
        weight_sum += weight
        breakdown.append(
            {
                "kpi_id": kid,
                "kpi_code": kpi.get("code") or assessment.get("kpi_code"),
                "kpi_name": kpi.get("name") or assessment.get("kpi_name"),
                "weight": weight,
                "weight_unit": str(kpi.get("weight_unit") or "percent"),
                "raw_score": raw,
                "min_score": min_score,
                "max_score": max_score,
                "normalized_score": round(normalized, 4),
                "contribution": contribution,
            }
        )

    if weight_sum <= 0 or not breakdown:
        raise ValueError("No scored KPIs available to calculate rating")

    rating = round(weighted_sum / weight_sum, 4)
    return {
        "rating": rating,
        "band": rating_band(rating),
        "is_complete": len(missing) == 0,
        "weight_sum": round(weight_sum, 4),
        "weighted_sum": round(weighted_sum, 6),
        "breakdown": breakdown,
        "missing_kpi_ids": missing,
        "formula_version": FORMULA_VERSION,
        "applicable_kpi_count": len(applicable),
        "included_kpi_count": len(breakdown),
    }


def reference_case_expected() -> Tuple[float, str]:
    """Documented QA reference: rating 83.0, band excellent."""
    return 83.0, BAND_EXCELLENT
