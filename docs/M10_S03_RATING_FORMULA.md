# M10-S03-T01 — Rating Formula (Approved for Implementation)

**Status:** Approved for coding (provisional product default; change only via version bump)  
**Date:** 2026-09-17  
**Epic:** M10 – KPI, Rating & Ranking  
**Task:** S03-T01 Finalize rating formula  

This document freezes the calculation used by `utils/kpi_rating_engine.py` and stored in `student_kpi_ratings`.

---

## 1) Inputs

| Input | Source | Notes |
|-------|--------|-------|
| Raw KPI score | `student_kpi_assessments.raw_score` | Entered in S02 while period is `open` |
| KPI min / max | Snapshot on assessment **or** current `kpi_definitions` | Engine prefers assessment snapshot |
| KPI weight + unit | Current active `kpi_definitions` at calculation time | `percent` or `points` |
| Scope | Student × Course × Period | One rating document per triple |

---

## 2) Normalization (0–100)

For each assessed KPI:

```
span = max_score - min_score
if span <= 0:
  normalized = 0
else:
  clamped = clamp(raw_score, min_score, max_score)
  normalized = ((clamped - min_score) / span) * 100
```

Rounded to **4 decimal places**. Same rule as S02 `normalize_score`.

---

## 3) Weighted rating (canonical)

**Formula version:** `m10-s03-v1`

Let `K` be the set of **included** KPIs (see §4).

```
weight_sum = Σ weight_i   for i in K
if weight_sum <= 0:
  rating = 0
else:
  rating = Σ (normalized_i × weight_i) / weight_sum
```

Rounded to **4 decimal places**. Result is always on a **0–100** scale.

### Equivalence when percent weights sum to 100

If every included KPI uses `weight_unit = percent` and `Σ weight_i = 100`:

```
rating = Σ (normalized_i × weight_i / 100)
```

which matches the weighted-average form above.

### Points weights

`points` KPIs use the **same** weighted-average formula. Absolute point values are relative only within the included set (no requirement that points sum to 100).

### Mixed percent + points

Allowed. Each KPI contributes with its numeric `weight` in the same Σ formula. Prefer a single unit in production for clarity; mixed sets are still mathematically defined.

---

## 4) Inclusion rules

A KPI is **applicable** for a student/course/branch when:

1. `kpi_definitions.is_active = true`
2. If `course_ids` is non-empty → course must be in the list
3. If `branch_ids` is non-empty → branch must be in the list

A KPI is **included** in the rating when it is applicable **and** an assessment row exists for that student/course/period/kpi.

| Case | Behavior |
|------|----------|
| No applicable active KPIs | Error — cannot calculate |
| Applicable KPIs missing scores | Calculate from scored KPIs; mark `is_complete = false` |
| All applicable KPIs scored | `is_complete = true` |
| Inactive KPI with old score | **Excluded** (not in applicable set) |

Missing scores are **not** treated as zero unless an assessment is explicitly saved as zero.

---

## 5) Rating band (display label)

Derived from `rating` (not stored as source of truth; also persisted for convenience):

| Band | Range |
|------|-------|
| `outstanding` | ≥ 90 |
| `excellent` | ≥ 75 and < 90 |
| `good` | ≥ 60 and < 75 |
| `fair` | ≥ 40 and < 60 |
| `needs_improvement` | < 40 |

---

## 6) Persistence

Collection: `student_kpi_ratings`  
Unique key: `(student_id, course_id, period_id)`

Stored fields include: `rating`, `band`, `is_complete`, `weight_sum`, `formula_version`, breakdown lines (`kpi_id`, `normalized_score`, `weight`, `contribution`), calculator audit, timestamps.

---

## 7) Recalculation policy

| Period status | Recalculate |
|---------------|-------------|
| `open` | Allowed (SA / Coach Admin / Branch Manager in scope / Coach in scope) |
| `draft` | Allowed (admin roles) — rare; useful after test data |
| `closed` | Only with `force=true` and SA / Coach Admin |

Recalculation **overwrites** the existing rating document for that student/course/period and refreshes breakdown from current assessments + current active KPI weights.

Batch recalculation: all distinct student/course pairs that have assessments in the period (optional branch filter).

---

## 8) Explicit non-goals (S03)

- Does **not** compute leaderboard ranks (S04)
- Does **not** change student dashboard cards (S05)
- Does **not** modify attendance, payments, biometric, or `student_performance_*` skill metrics
- Does **not** auto-derive KPIs from attendance

---

## 9) Reference example (QA)

KPIs (percent, sum = 100):

| KPI | Weight | Min | Max | Raw | Normalized |
|-----|--------|-----|-----|-----|------------|
| Technique | 40 | 0 | 100 | 80 | 80 |
| Discipline | 30 | 0 | 10 | 9 | 90 |
| Fitness | 30 | 0 | 50 | 40 | 80 |

```
rating = (80×40 + 90×30 + 80×30) / 100 = 83.0
band = excellent
is_complete = true
```
