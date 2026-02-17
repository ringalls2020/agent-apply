# Agent Apply Backend Documentation

## 1. Overview

The backend is a FastAPI service that simulates a job-application automation pipeline:

1. Discover matching opportunities.
2. Create an application record ("apply").
3. Enrich each record with a recruiter contact.
4. Mark the record as "notified".

Application records are now persisted in PostgreSQL.

## 2. Technology Stack

- Python
- FastAPI (`fastapi==0.115.0`)
- Pydantic v2 (`pydantic==2.8.2`)
- SQLAlchemy (`sqlalchemy==2.0.36`)
- Psycopg v3 (`psycopg[binary]==3.2.3`)
- Uvicorn (`uvicorn==0.30.6`)
- Jinja2 (`jinja2==3.1.4`)
- Pytest + HTTPX (`pytest==8.3.3`, `httpx==0.27.2`)

Dependencies are pinned in `requirements.txt`.

## 3. Project Structure

```text
backend/
  main.py                  # FastAPI app factory + HTTP routes
  models.py                # Pydantic request/response/domain models
  db.py                    # Engine/session factory + DATABASE_URL resolution
  db_models.py             # SQLAlchemy ORM table definitions
  services.py              # PostgresStore + OpportunityAgent pipeline
  templates/
    dashboard.html         # Admin dashboard rendered by /admin

tests/
  test_api.py              # Endpoint-level tests
  test_services.py         # Store + agent pipeline unit tests
```

## 4. Configuration

Environment variables:

- `DATABASE_URL` (required for production/local usage)
  - Example: `postgresql+psycopg://postgres:postgres@localhost:5432/agent_apply`

Default fallback when `DATABASE_URL` is not set:

- `postgresql+psycopg://postgres:postgres@localhost:5432/agent_apply`

## 5. Local Setup and Run

From repository root (`/Users/riza/dev-projects/agent-apply`):

1. Start PostgreSQL (example with Docker):

```bash
docker run --name agent-apply-postgres \
  -e POSTGRES_DB=agent_apply \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -d postgres:16
```

2. Set database URL:

```bash
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/agent_apply
```

3. Start the API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Server defaults:

- Base URL: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- Admin dashboard: `http://127.0.0.1:8000/admin`

## 6. Architecture and Runtime Behavior

### 6.1 App initialization

`create_app()` in `backend/main.py`:

- Resolves database URL from argument/env.
- Creates SQLAlchemy engine and session factory.
- Creates database tables if they do not exist.
- Attaches `PostgresStore` to `app.state.store`.
- Attaches `OpportunityAgent` to `app.state.agent`.
- Disposes the DB engine on app shutdown.

### 6.2 Persistence model

`PostgresStore` persists each `ApplicationRecord` in the `applications` table with relational columns for:

- Core status/timestamps
- Opportunity fields (`id`, `title`, `company`, `url`, `reason`, `discovered_at`)
- Contact fields (`name`, `email`, `role`, `source`)

`list_all()` returns records sorted by `opportunity_discovered_at` descending.

### 6.3 Agent pipeline

`OpportunityAgent.run()` executes:

1. `_discover(request)` creates `max_opportunities` synthetic opportunities.
2. `_apply(opportunity)` creates an `ApplicationRecord` with:
   - status `applied`
   - `submitted_at` timestamp
3. `_find_point_of_contact(record)` attaches a synthetic recruiter contact.
4. `_notify(record)` sets:
   - status `notified`
   - `notified_at` timestamp
5. Final record is written through `store.upsert(record)`.

Returned API records are already in `notified` state for this prototype.

## 7. API Reference

### 7.1 GET `/health`

Health probe endpoint.

Response:

```json
{
  "status": "ok"
}
```

### 7.2 POST `/agent/run`

Runs the full discovery -> apply -> enrich -> notify pipeline and returns generated application records.

Request schema (`AgentRunRequest`):

```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "resume_text": "ML engineer with interest in climate and robotics.",
    "interests": ["ai", "climate"]
  },
  "max_opportunities": 3
}
```

Validation rules:

- `profile.interests` must contain at least 1 item.
- `max_opportunities` must be between 1 and 25 (inclusive).

### 7.3 GET `/applications`

Returns all stored application records in descending `discovered_at` order.

Response schema:

- `AgentRunResponse` (`{"applications": [...]}`)

### 7.4 GET `/admin`

Renders an HTML dashboard (`backend/templates/dashboard.html`) with:

- Summary stats:
  - Total Opportunities
  - Applied (records where `submitted_at` exists)
  - Notified (records where `notified_at` exists)
- Table of application records and contact details.

## 8. Testing

Run from repository root:

```bash
python3 -m pytest -q
```

Test behavior:

- API and service tests run against an in-memory SQLite database.
- Production/local runtime uses PostgreSQL through `DATABASE_URL`.

## 9. Operational Limitations (Current Prototype)

- No auth or route-level authorization.
- No background job queue; all processing is synchronous in request cycle.
- No external provider integrations (search, application automation, contact enrichment, notifications are mocked).
- No structured logging, tracing, or metrics export.
- No migration tool configured yet (schema currently created with SQLAlchemy `create_all`).

## 10. Productionization Checklist

1. Add schema migrations (Alembic) and versioned database changes.
2. Add authentication and route-level authorization.
3. Move pipeline execution to asynchronous workers (e.g., Celery/RQ/Temporal).
4. Integrate real providers in place of `_discover`, `_apply`, `_find_point_of_contact`, `_notify`.
5. Add retries, idempotency keys, and dead-letter handling around external calls.
6. Add observability (structured logs, metrics, tracing, error monitoring).
7. Add contract tests and integration tests for external provider adapters.
8. Add environment-based settings and secrets management.
