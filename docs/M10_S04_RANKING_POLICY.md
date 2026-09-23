# M10-S04 — Student Ranking Policy

**Status:** Approved for coding  
**Date:** 2026-09-17  
**Epic:** M10 – KPI, Rating & Ranking  
**Version:** `m10-s04-v1`

Does **not** modify warrior free-text `rank` or student performance skill cards.

---

## 1) Source data

Rankings are computed from **stored** `student_kpi_ratings` for a period (S03).

| Eligible | Rule |
|----------|------|
| Has rating for period | Required |
| `is_complete = true` | Required (missing KPI scores excluded) |
| Student `is_active ≠ false` | Required |
| Rating in scope population | See §2 |

Incomplete ratings and inactive students are **excluded**, not ranked as zero.

---

## 2) Populations (`scope_type`)

| Scope | `scope_id` | Population |
|-------|------------|------------|
| `overall` | `*` | All eligible ratings in the period |
| `branch` | branch UUID | Ratings whose `branch_id` matches |
| `course` | course UUID | Ratings whose `course_id` matches |
| `category` | category UUID | Ratings whose course `category_id` matches |

If a student has **multiple** eligible ratings inside one population (e.g. two courses under overall), use the **highest rating**. Ties on rating among the same student's courses: prefer lower `course_id` (stable).

---

## 3) Sort & tie policy

1. Sort by `rating` **descending**.
2. **Competition (standard) ranking:** equal ratings share the same rank; next rank skips.  
   Example ratings `[95, 90, 90, 80]` → ranks `[1, 2, 2, 4]`.
3. Within the same rating, order by `student_id` ascending for stable list order only (shared rank unchanged).

`dense` ranking is **not** used in v1.

---

## 4) Persistence

Collection: `student_kpi_rankings`

Unique: `(period_id, scope_type, scope_id, student_id)`

Fields: `rank`, `rating`, `band`, `course_id` (source), `branch_id`, `population_size`, `tied` (bool — more than one student at this rank), `formula_version`, calculator audit, timestamps.

Recalculation **replaces** all rows for the requested period + scope set (delete then insert).

---

## 5) Recalculation gates

| Period status | Allowed |
|---------------|---------|
| `open` / `draft` | Yes (scoped roles) |
| `closed` | Only `force=true` by Super Admin / Coach Admin |

Branch Manager / Coach: may only recalculate populations intersecting their managed branch(es).

---

## 6) Non-goals

- No dashboard UI (S05)
- No change to warrior rank string
- No auto-ranking on every assessment save (explicit recalculate only)
