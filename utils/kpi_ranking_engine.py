"""
M10-S04 pure ranking engine (no DB / FastAPI).

See docs/M10_S04_RANKING_POLICY.md — version m10-s04-v1.
Competition (standard) ranking: 1,2,2,4 for ties.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RANKING_VERSION = "m10-s04-v1"
SCOPE_OVERALL = "overall"
SCOPE_BRANCH = "branch"
SCOPE_COURSE = "course"
SCOPE_CATEGORY = "category"
OVERALL_SCOPE_ID = "*"


def pick_best_rating_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse to one row per student_id using highest rating.
    Tie on rating: lower course_id wins (stable).
    """
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("student_id") or "")
        if not sid:
            continue
        rating = float(row.get("rating") or 0)
        course_id = str(row.get("course_id") or "")
        prev = best.get(sid)
        if prev is None:
            best[sid] = row
            continue
        prev_rating = float(prev.get("rating") or 0)
        prev_course = str(prev.get("course_id") or "")
        if rating > prev_rating or (
            rating == prev_rating and course_id < prev_course
        ):
            best[sid] = row
    return list(best.values())


def assign_competition_ranks(
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Sort by rating desc, student_id asc; assign competition ranks.
    Mutates copies; returns new list with rank, tied, population_size.
    """
    sorted_rows = sorted(
        entries,
        key=lambda r: (-float(r.get("rating") or 0), str(r.get("student_id") or "")),
    )
    n = len(sorted_rows)
    out: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        rating = float(sorted_rows[i].get("rating") or 0)
        j = i + 1
        while j < n and float(sorted_rows[j].get("rating") or 0) == rating:
            j += 1
        # competition rank = 1-based index of first in tie group
        rank = i + 1
        tied = (j - i) > 1
        for k in range(i, j):
            row = dict(sorted_rows[k])
            row["rank"] = rank
            row["tied"] = tied
            row["population_size"] = n
            out.append(row)
        i = j
    return out


def filter_eligible_ratings(
    ratings: List[Dict[str, Any]],
    *,
    inactive_student_ids: Optional[set] = None,
    require_complete: bool = True,
) -> List[Dict[str, Any]]:
    inactive = inactive_student_ids or set()
    out = []
    for r in ratings or []:
        sid = str(r.get("student_id") or "")
        if not sid or sid in inactive:
            continue
        if require_complete and not r.get("is_complete", False):
            continue
        if r.get("rating") is None:
            continue
        out.append(r)
    return out


def build_populations(
    ratings: List[Dict[str, Any]],
    *,
    course_category_map: Optional[Dict[str, str]] = None,
    scope_types: Optional[List[str]] = None,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    category_id: Optional[str] = None,
) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
    """
    Returns list of (scope_type, scope_id, member_rows) before best-of collapse + ranking.
    Member rows are raw eligible ratings (may include multiple courses per student).
    """
    scopes = scope_types or [
        SCOPE_OVERALL,
        SCOPE_BRANCH,
        SCOPE_COURSE,
        SCOPE_CATEGORY,
    ]
    cat_map = course_category_map or {}
    populations: List[Tuple[str, str, List[Dict[str, Any]]]] = []

    if SCOPE_OVERALL in scopes:
        rows = list(ratings)
        if branch_id:
            rows = [r for r in rows if str(r.get("branch_id")) == str(branch_id)]
        if course_id:
            rows = [r for r in rows if str(r.get("course_id")) == str(course_id)]
        if category_id:
            rows = [
                r
                for r in rows
                if str(cat_map.get(str(r.get("course_id")), "")) == str(category_id)
            ]
        populations.append((SCOPE_OVERALL, OVERALL_SCOPE_ID, rows))

    if SCOPE_BRANCH in scopes:
        by_branch: Dict[str, List[Dict[str, Any]]] = {}
        for r in ratings:
            bid = str(r.get("branch_id") or "")
            if not bid:
                continue
            if branch_id and bid != str(branch_id):
                continue
            if course_id and str(r.get("course_id")) != str(course_id):
                continue
            if category_id and str(cat_map.get(str(r.get("course_id")), "")) != str(
                category_id
            ):
                continue
            by_branch.setdefault(bid, []).append(r)
        for bid, rows in sorted(by_branch.items()):
            populations.append((SCOPE_BRANCH, bid, rows))

    if SCOPE_COURSE in scopes:
        by_course: Dict[str, List[Dict[str, Any]]] = {}
        for r in ratings:
            cid = str(r.get("course_id") or "")
            if not cid:
                continue
            if course_id and cid != str(course_id):
                continue
            if branch_id and str(r.get("branch_id")) != str(branch_id):
                continue
            if category_id and str(cat_map.get(cid, "")) != str(category_id):
                continue
            by_course.setdefault(cid, []).append(r)
        for cid, rows in sorted(by_course.items()):
            populations.append((SCOPE_COURSE, cid, rows))

    if SCOPE_CATEGORY in scopes:
        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for r in ratings:
            cid = str(r.get("course_id") or "")
            cat = str(cat_map.get(cid) or "")
            if not cat:
                continue
            if category_id and cat != str(category_id):
                continue
            if branch_id and str(r.get("branch_id")) != str(branch_id):
                continue
            if course_id and cid != str(course_id):
                continue
            by_cat.setdefault(cat, []).append(r)
        for cat, rows in sorted(by_cat.items()):
            populations.append((SCOPE_CATEGORY, cat, rows))

    return populations


def rank_population(
    rows: List[Dict[str, Any]],
    *,
    scope_type: str,
    scope_id: str,
) -> List[Dict[str, Any]]:
    """Collapse best-per-student, assign ranks, attach scope fields."""
    best = pick_best_rating_rows(rows)
    ranked = assign_competition_ranks(best)
    for row in ranked:
        row["scope_type"] = scope_type
        row["scope_id"] = scope_id
        row["formula_version"] = RANKING_VERSION
        # category_id convenience when ranking by category
        if scope_type == SCOPE_CATEGORY:
            row["category_id"] = scope_id
    return ranked


def compute_all_rankings(
    ratings: List[Dict[str, Any]],
    *,
    inactive_student_ids: Optional[set] = None,
    course_category_map: Optional[Dict[str, str]] = None,
    scope_types: Optional[List[str]] = None,
    branch_id: Optional[str] = None,
    course_id: Optional[str] = None,
    category_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    eligible = filter_eligible_ratings(
        ratings, inactive_student_ids=inactive_student_ids, require_complete=True
    )
    pops = build_populations(
        eligible,
        course_category_map=course_category_map,
        scope_types=scope_types,
        branch_id=branch_id,
        course_id=course_id,
        category_id=category_id,
    )
    out: List[Dict[str, Any]] = []
    for scope_type, scope_id, members in pops:
        if not members:
            continue
        out.extend(
            rank_population(members, scope_type=scope_type, scope_id=scope_id)
        )
    return out
