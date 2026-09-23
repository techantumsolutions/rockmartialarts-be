## ESSL Attendance Integration (FastAPI + MongoDB)

This integration is intentionally **modular and isolated** from the main backend (`server.py`) and the Next.js app.

> **M09-S03-T01:** The provisional application contract (normalized punch event, poll/webhook/file modes, mapping & dedup rules) lives in  
> [`M09_S03_T01_VENDOR_INTEGRATION_CONTRACT.md`](./M09_S03_T01_VENDOR_INTEGRATION_CONTRACT.md).  
> Vendor sign-off ratifies production; it does **not** block implementing ingest against the provisional contract.

### 1) Environment variables

Set these on the machine running the service:

- **`ESSL_URL`**: ESSL HTTP endpoint (POST)
- **`ESSL_SERIAL`**: Device serial
- **`ESSL_USER`**: API username
- **`ESSL_PASS`**: API password
- **`MONGO_URI`**: MongoDB connection string (do not expose to browsers)

Optional:

- **`ESSL_MONGO_DB`**: default `attendance_db`
- **`ESSL_MONGO_COLLECTION`**: default `logs`
- **`ESSL_TZ`**: default `Asia/Kolkata` (used when device timestamps are naive)
- **`ESSL_TIMEOUT_SECS`**: default `20`
- **`ESSL_VERIFY_SSL`**: default `true`

### 2) Run the FastAPI middleware

From `rockmartialarts-be`:

```bash
uvicorn essl_service:app --host 0.0.0.0 --port 8001
```

Endpoints:

- `GET /health`
- `GET /fetch-attendance` (polling)

### 3) Automate polling (cron)

Every minute:

```bash
*/1 * * * * curl -fsS http://localhost:8001/fetch-attendance >/dev/null
```

### 4) Next.js API (read logs)

The Next.js app has:

- `GET /api/attendance?limit=100`

It reads from MongoDB (`MONGO_URI`, `ESSL_MONGO_DB`, `ESSL_MONGO_COLLECTION`) and returns the latest logs sorted by timestamp.

### 5) Deduplication & indexing

The middleware creates indexes:

- unique `log_id`
- unique `(user_id, timestamp, type)` as a safety net
- `timestamp` for sort performance

### 6) User mapping (ESSL user_id → app user)

The device logs contain `user_id` (employee code). The app users have an optional `essl_user_id`.

- When a log is fetched, the middleware attempts to find a matching app user:
  - match: `users.biometric_id == log.user_id` (primary; matches the admin Add/Edit Student "Biometric ID" field)
  - fallback: `users.essl_user_id == log.user_id` (backwards compatible)
  - on match, it enriches the log with `app_user_id` and `app_user_name`
  - on no match, it also stores the log in `ESSL_UNMATCHED_COLLECTION` (default `unmatched_logs`)

The main backend also creates a **unique sparse index** on `users.essl_user_id`.
It also creates a **unique sparse index** on `users.biometric_id`.

### 7) Create user on device (optional)

`POST /create-user` on the middleware:

- body: `{ "essl_user_id": "STU0001", "name": "Rahul" }`
- target endpoint:
  - `ESSL_CREATE_USER_URL` if set, else `ESSL_URL + "/createuser"`

### 6) Timestamp normalization

All stored `timestamp` values are normalized to **UTC** (naive datetime, consistent with the existing backend conventions).

