# Main Backend (`backend`)

FastAPI service that is now the **system of record** for:

- users
- preferences
- resumes
- external match/apply run references
- matched jobs
- application attempt outcomes
- webhook idempotency/audit events

Legacy demo endpoints (`/agent/run`, `/applications`, `/admin`) are still present.

## Stack

- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL/SQLite
- HTTPX (cloud API client)

## Key endpoints

### Core data

- `PUT /v1/users/{user_id}`
- `GET /v1/users/{user_id}`
- `PUT /v1/users/{user_id}/preferences`
- `GET /v1/users/{user_id}/preferences`
- `PUT /v1/users/{user_id}/resume`
- `GET /v1/users/{user_id}/resume`

### Cloud orchestration

- `POST /v1/users/{user_id}/match-runs`
- `GET /v1/users/{user_id}/match-runs/{run_id}`
- `POST /v1/users/{user_id}/apply-runs`
- `GET /v1/users/{user_id}/apply-runs/{run_id}`

### Callback ingestion

- `POST /internal/cloud/callbacks/apply-result`

Callback requirements:

- Bearer JWT (`HS256`)
- Signature headers:
  - `x-cloud-timestamp`
  - `x-cloud-nonce`
  - `x-cloud-signature`
- Idempotency header:
  - `x-idempotency-key`

## Environment variables

### Database

- `DATABASE_URL` (default: `sqlite+pysqlite:///./agent_apply.db`)

### Logging

- `LOG_LEVEL` (default: `INFO`)
- `SQLALCHEMY_LOG_QUERIES` (`true`/`false`)

### Cloud client (main -> cloud automation)

- `CLOUD_AUTOMATION_BASE_URL` (default: `http://127.0.0.1:8100`)
- `CLOUD_AUTOMATION_SERVICE_ID` (default: `main-api`)
- `CLOUD_AUTOMATION_AUDIENCE` (default: `job-intel-api`)
- `CLOUD_AUTOMATION_SIGNING_SECRET` (default: `dev-cloud-signing-secret`)
- `CLOUD_AUTOMATION_TIMEOUT_SECONDS` (default: `20`)

### Callback verification (cloud -> main)

- `CLOUD_CALLBACK_SIGNING_SECRET` (default: falls back to `CLOUD_AUTOMATION_SIGNING_SECRET`)
- `CLOUD_CALLBACK_SIGNATURE_SECRET` (default: same as signing secret)
- `CLOUD_CALLBACK_AUDIENCE` (default: `main-api`)
- `CLOUD_CALLBACK_ISSUER` (default: `job-intel-api`)
- `CLOUD_CALLBACK_MAX_CLOCK_SKEW_SECONDS` (default: `300`)
- `CLOUD_CALLBACK_REQUIRED_CLIENT_SUBJECT` (optional mTLS subject assertion)

### Limits

- `DEFAULT_APPLY_DAILY_CAP` (default: `25`)

## Local run

```bash
uvicorn backend.main:app --reload --port 8000
```

## Migrations

```bash
alembic upgrade head
```

## Tests

```bash
.venv/bin/python -m pytest -q
```
