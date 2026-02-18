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

### User auth

- `POST /v1/auth/signup`
- `POST /v1/auth/login`
- `GET /v1/auth/me`

### Core data

- `PUT /v1/users/{user_id}`
- `GET /v1/users/{user_id}`
- `PUT /v1/users/{user_id}/preferences`
- `GET /v1/users/{user_id}/preferences`
- `PUT /v1/users/{user_id}/resume`
- `GET /v1/users/{user_id}/resume`
- `PUT /v1/users/{user_id}/profile`
- `GET /v1/users/{user_id}/profile`

All `/v1/users/{user_id}/*` routes require a user bearer token and enforce `token.sub == {user_id}`.

### Cloud orchestration

- `POST /v1/users/{user_id}/match-runs`
- `GET /v1/users/{user_id}/match-runs/{run_id}`
- `POST /v1/users/{user_id}/apply-runs`
- `GET /v1/users/{user_id}/apply-runs/{run_id}`

### Per-user legacy-compatible apply flow

- `POST /v1/agent/run`
- `GET /v1/applications`

`POST /v1/agent/run` triggers cloud discovery + matching and persists results per user.
To avoid synthetic fallback listings, set `USE_ONLY_LIVE_ADAPTERS=true` in the cloud automation env and configure live adapter seed variables.
When `autosubmit_enabled=true` in the user profile, `/v1/agent/run` also starts async apply runs and application statuses progress via callback updates.
When cloud apply reports `blocked` with failure code `manual_review_timeout` (dev review mode timeout), the application is mapped back to `review` instead of `failed`.

Legacy routes `/agent/run` and `/applications` now return `410 Gone`.

### Admin dashboard

- `GET /admin`

Admin dashboard is enabled by default in local/dev/test environments and disabled by default elsewhere.
Set `ENABLE_ADMIN_DASHBOARD=true` to enable explicitly and optionally protect it with
`ADMIN_DASHBOARD_SECRET` passed as `x-admin-secret`.

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
- `ENABLE_MAIN_SCHEMA_CREATE` (default: `true` in local/dev/test, `false` otherwise)

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
- `JOB_LISTING_TTL_DAYS` (default: `21`; listings with anchor time older than this are archived/hidden by default)

### User auth

- `USER_AUTH_SIGNING_SECRET` (default: `dev-user-auth-secret`)
- `USER_AUTH_ISSUER` (default: `main-api`)
- `USER_AUTH_AUDIENCE` (default: `agent-apply-frontend`)
- `USER_AUTH_TOKEN_TTL_SECONDS` (default: `604800`)
- `USER_PROFILE_ENCRYPTION_KEY` (required outside dev/test to encrypt sensitive profile fields)

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
