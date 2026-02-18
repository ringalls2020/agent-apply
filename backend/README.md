# Main Backend (`backend`)

FastAPI service that is now the **system of record** for:

- users
- preferences
- resumes
- external match/apply run references
- matched jobs
- application attempt outcomes
- webhook idempotency/audit events

## Stack

- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL/SQLite
- HTTPX (cloud API client)

## Key endpoints

### GraphQL

- `POST /graphql`

Main operations are exposed via GraphQL mutations and queries:

- `signup`, `login`, `me`
- `runAgent`
- `applications`, `applicationsSearch`
- `applySelectedApplications`
- `markApplicationViewed`, `markApplicationApplied`
- `updatePreferences`, `uploadResume`, `profile`, `updateProfile`

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
