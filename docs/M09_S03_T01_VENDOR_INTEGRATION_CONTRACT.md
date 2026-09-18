# M09-S03-T01 — Biometric Vendor Integration Contract (Provisional)

**Status:** Provisional (unblocks S03-T02+)  
**Date:** 2026-09-17  
**Epic:** M09 – Biometric Attendance  
**Task:** S03-T01 Finalize vendor integration  

## Is this a blocker?

**No — not a hard technical blocker.**

| Meaning | Answer |
|---------|--------|
| Can we implement ingest (T02+) now? | **Yes**, against this provisional contract |
| Must vendor sign before writing code? | **No** — sign-off is ratification |
| When does vendor confirmation matter? | Before **production go-live** / changing field names or transport |

This document freezes the **application contract** used by Rock Martial Arts. Vendor-specific quirks are adapted at the edge (ESSL poller / future webhook adapter) into this shape.

---

## 1) Selected vendor & mode (current)

| Item | Provisional choice | Notes |
|------|--------------------|-------|
| Vendor family | **ESSL-compatible HTTP device API** | Matches existing `essl_service.py` |
| Primary transport | **Polling (pull)** | `GET` middleware → vendor `POST` with serial/credentials |
| Alternate transports (future) | Webhook push, file drop | Adapters must emit the same normalized event |
| Main app write target | Mongo `attendance` | Via future secure ingest (T02); today ESSL writes `attendance_db.logs` only |

Existing references:

- `rockmartialarts-be/essl_service.py`
- `rockmartialarts-be/docs/ESSL_ATTENDANCE.md`
- Stub: `POST /api/attendance/biometric` (`BiometricAttendance`: `device_id`, `biometric_id`, `timestamp`)

---

## 2) Transport contracts

### 2.1 Polling (implemented edge)

**Direction:** Our middleware → Vendor API  

**Auth (vendor):** serial + username + password in JSON body  

**Request (to vendor):**

```json
{
  "serial": "<ESSL_SERIAL>",
  "username": "<ESSL_USER>",
  "password": "<ESSL_PASS>"
}
```

**Response (from vendor) — expected shape:**

```json
{
  "logs": [
    {
      "id": "vendor-unique-log-id",
      "user_id": "external-biometric-code",
      "timestamp": "2026-09-17 09:15:00",
      "type": "IN",
      "device": "optional-device-name"
    }
  ]
}
```

Accepted aliases (already handled in `_normalize_log`):

| Canonical | Accepted aliases |
|-----------|------------------|
| `id` / `log_id` | `_id` |
| `user_id` | `userId`, `emp_id` |
| `timestamp` | `time`, `datetime` |
| `type` | `log_type` (`IN` / `OUT`; unknown kept as `UNKNOWN`) |
| `device` | `device_name`, `terminal` |

**Env:** `ESSL_URL`, `ESSL_SERIAL`, `ESSL_USER`, `ESSL_PASS`, optional `ESSL_TZ`, timeouts, SSL verify.

### 2.2 Webhook push (planned adapter — same normalized event)

**Direction:** Vendor → Our FastAPI  

**Proposed endpoint (T02):** `POST /api/attendance/ingest/biometric`  

**Auth:** shared secret / HMAC header (not public anonymous)  

**Body:** either a single normalized event or `{ "events": [ ... ] }` matching §3.

### 2.3 File drop (optional later)

CSV/JSON lines mapped 1:1 into §3. Not required for first ingest cut.

---

## 3) Normalized punch event (application contract)

All transports must reduce to this event before writing `attendance`:

```json
{
  "external_event_id": "string (required, unique per vendor event)",
  "external_user_id": "string (required, maps to users.biometric_id or users.essl_user_id)",
  "vendor_device_id": "string (required for branch attribution once S01 devices exist)",
  "event_type": "IN | OUT | UNKNOWN",
  "occurred_at": "ISO-8601 UTC (or UTC-naive matching app convention)",
  "vendor": "essl",
  "raw": {}
}
```

### Mapping rules (T03)

1. Resolve student: `users.biometric_id == external_user_id` **or** `users.essl_user_id == external_user_id`
2. Resolve branch: prefer **device → branch** (S01 registry); fallback enrollment / `users.branch_id` only if device unknown
3. Unmatched `external_user_id` → store unmatched log; **do not** invent attendance

### Dedup rules (T04)

1. Primary: unique `external_event_id` (`log_id`)
2. Secondary: `(external_user_id, occurred_at, event_type)`
3. Same calendar day attendance row: upsert check-in/out (do not double-insert present rows)

### Check-in / check-out (T05)

| `event_type` | Effect on same-day `attendance` row |
|--------------|-------------------------------------|
| `IN` | Set / keep earliest `check_in_time`; mark present |
| `OUT` | Set / update `check_out_time` |
| `UNKNOWN` | Treat as `IN` for first punch of day unless vendor confirms otherwise |

---

## 4) What vendor must confirm (sign-off checklist)

Use this with the vendor before production. Items not confirmed stay on **provisional defaults** above.

- [ ] Confirm transport: poll vs webhook vs file  
- [ ] Confirm auth scheme and credential rotation  
- [ ] Confirm exact JSON field names for id / user / time / IN-OUT  
- [ ] Confirm timestamp timezone and format  
- [ ] Confirm whether `device` / serial is on every punch  
- [ ] Confirm replay behavior (can same `log_id` reappear?)  
- [ ] Confirm create-user API (optional push of student to device)  
- [ ] Confirm rate limits / batch size  

**Sign-off:** _________________ (vendor) / _________________ (Rock) / Date: __________

---

## 5) Out of scope for T01

- Device CRUD UI (S01)  
- Secure ingest implementation (T02)  
- Correction APIs (S05)  

T01 output is **this contract only**.

---

## 6) Next step after T01

Proceed with **S03-T02** (attendance ingestion endpoint) implementing against §3, using the existing ESSL poller as the first adapter. Vendor checklist above can be completed in parallel without blocking development.

---

## 7) Implemented ingest (M09-S03)

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /api/attendance/ingest/biometric` | `X-Biometric-Ingest-Key` **or** Admin JWT | Batch normalized events (§3) |
| `POST /api/attendance/biometric` | same | Legacy single punch → same pipeline |
| `GET /api/attendance/ingest/unmatched` | Admin JWT | Review unknown student/device punches |
| `GET /api/attendance/ingest/stats` | Admin JWT | 7-day summary |

Env: `BIOMETRIC_INGEST_API_KEY` (recommended for device middleware).

ESSL poller should forward normalized events to `POST /api/attendance/ingest/biometric` after writing `attendance_db.logs` (or instead, once cut over).
