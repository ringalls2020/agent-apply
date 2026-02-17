# Agent Apply Backend Documentation

## 1. Overview

The backend is a FastAPI service that simulates a job-application automation pipeline:

1. Discover matching opportunities.
2. Create an application record ("apply").
3. Enrich each record with a recruiter contact.
4. Mark the record as "notified".

Current implementation is intentionally deterministic and in-memory for local development and demos.

## 2. Technology Stack

- Python
- FastAPI (`fastapi==0.115.0`)
- Pydantic v2 (`pydantic==2.8.2`)
- Uvicorn (`uvicorn==0.30.6`)
- Jinja2 (`jinja2==3.1.4`)
- Pytest + HTTPX (`pytest==8.3.3`, `httpx==0.27.2`)

Dependencies are pinned in `requirements.txt`.

## 3. Project Structure

```text
backend/
  main.py                  # FastAPI app factory + HTTP routes
  models.py                # Pydantic request/response/domain models
  services.py              # InMemoryStore + OpportunityAgent pipeline
  templates/
    dashboard.html         # Admin dashboard rendered by /admin

tests/
  test_api.py              # Endpoint-level tests
  test_services.py         # Store + agent pipeline unit tests
```

## 4. Local Setup and Run

From repository root (`/Users/riza/dev-projects/agent-apply`):

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

## 5. Architecture and Runtime Behavior

### 5.1 App initialization

`create_app()` in `backend/main.py`:

- Creates a FastAPI app.
- Attaches `InMemoryStore` to `app.state.store`.
- Attaches `OpportunityAgent` to `app.state.agent`.
- Registers REST endpoints and admin HTML route.

### 5.2 State model

Persistence is in-process memory only:

- `InMemoryStore.applications` is a `Dict[str, ApplicationRecord]`.
- Restarting the process clears all data.
- `upsert()` overwrites records by application ID.
- `list_all()` returns records sorted by `opportunity.discovered_at` descending.

### 5.3 Agent pipeline

`OpportunityAgent.run()` executes:

1. `_discover(request)` creates `max_opportunities` synthetic opportunities.
2. `_apply(opportunity)` creates an `ApplicationRecord` with:
   - status `applied`
   - `submitted_at` timestamp
3. `_find_point_of_contact(record)` attaches a synthetic recruiter contact.
4. `_notify(record)` sets:
   - status `notified`
   - `notified_at` timestamp
5. Final record is stored via `store.upsert(record)`.

Returned API records are already in `notified` state for this prototype.

## 6. API Reference

### 6.1 GET `/health`

Health probe endpoint.

Response:

```json
{
  "status": "ok"
}
```

### 6.2 POST `/agent/run`

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

Successful response (`AgentRunResponse`) shape:

```json
{
  "applications": [
    {
      "id": "application-uuid",
      "opportunity": {
        "id": "opportunity-uuid",
        "title": "Ai Fellow",
        "company": "Novel Labs 1",
        "url": "https://example.com/jobs/1",
        "reason": "Matched resume with interests (ai, climate) and found a novel role with high skills overlap.",
        "discovered_at": "2026-02-17T18:00:00.000000"
      },
      "status": "notified",
      "contact": {
        "name": "Recruiter for Novel Labs 1",
        "email": "recruiting@novellabs1.com",
        "role": "Talent Acquisition",
        "source": "Company careers page"
      },
      "submitted_at": "2026-02-17T18:00:01.000000",
      "notified_at": "2026-02-17T18:00:02.000000"
    }
  ]
}
```

Failure mode:

- `422 Unprocessable Entity` for validation errors (for example, empty `interests`).

### 6.3 GET `/applications`

Returns all stored application records in descending `discovered_at` order.

Response schema:

- `AgentRunResponse` (`{"applications": [...]}`)

### 6.4 GET `/admin`

Renders an HTML dashboard (`backend/templates/dashboard.html`) with:

- Summary stats:
  - Total Opportunities
  - Applied (records where `submitted_at` exists)
  - Notified (records where `notified_at` exists)
- Table of application records and contact details.

## 7. Data Model Reference

Defined in `backend/models.py`.

### 7.1 Enums

- `ApplicationStatus`
  - `discovered`
  - `applied`
  - `notified`

### 7.2 Domain models

- `Opportunity`
  - `id: str`
  - `title: str`
  - `company: str`
  - `url: str`
  - `reason: str`
  - `discovered_at: datetime` (auto defaults to current UTC time)

- `Contact`
  - `name: str`
  - `email: str`
  - `role: Optional[str]`
  - `source: str`

- `ApplicationRecord`
  - `id: str`
  - `opportunity: Opportunity`
  - `status: ApplicationStatus`
  - `contact: Optional[Contact]`
  - `submitted_at: Optional[datetime]`
  - `notified_at: Optional[datetime]`

### 7.3 API models

- `CandidateProfile`
  - `full_name: str`
  - `email: str`
  - `resume_text: str`
  - `interests: List[str]` (minimum length 1)

- `AgentRunRequest`
  - `profile: CandidateProfile`
  - `max_opportunities: int` (default 5, min 1, max 25)

- `AgentRunResponse`
  - `applications: List[ApplicationRecord]`

## 8. Testing

Run from repository root:

```bash
python3 -m pytest -q
```

Test coverage includes:

- API health endpoint behavior.
- End-to-end agent run plus list retrieval.
- Validation rejection for empty `interests`.
- Admin dashboard rendering and stats markers.
- Store sorting by discovery timestamp.
- Agent pipeline completion (submitted/notified/contact fields).
- Discovery title generation that rotates through interests.

## 9. Operational Limitations (Current Prototype)

- No durable datastore (memory only).
- No authentication or authorization.
- No request rate limiting.
- No background job queue; all processing is synchronous in request cycle.
- No external provider integrations (search, application automation, contact enrichment, notifications are mocked).
- No structured logging, tracing, or metrics export.

## 10. Productionization Checklist

To evolve this backend into production:

1. Replace `InMemoryStore` with a persistent database repository (PostgreSQL recommended).
2. Add authentication and route-level authorization.
3. Move pipeline execution to asynchronous workers (e.g., Celery/RQ/Temporal).
4. Integrate real providers in place of `_discover`, `_apply`, `_find_point_of_contact`, `_notify`.
5. Add retries, idempotency keys, and dead-letter handling around external calls.
6. Add observability (structured logs, metrics, tracing, error monitoring).
7. Add contract tests and integration tests for external provider adapters.
8. Add environment-based settings and secrets management.
