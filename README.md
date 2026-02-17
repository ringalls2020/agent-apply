# agent-apply

An opinionated starter for an **agentic job application assistant** with a lightweight admin dashboard.

## Product scope

This project provides backend scaffolding for an agent that can:

1. Accept candidate resume text and interests.
2. Discover opportunities that align with those interests.
3. Simulate applying to each matching role.
4. Enrich each application with a point of contact.
5. Track and display notification status for each submitted application.

> Current behavior is intentionally mock/deterministic to make extension and testing straightforward.

## Architecture overview

- `app/models.py`: Pydantic request/response/domain models.
- `app/services.py`: In-memory persistence and agent pipeline orchestration.
- `app/main.py`: FastAPI app factory + HTTP routes.
- `app/templates/dashboard.html`: Admin dashboard UI.
- `tests/test_services.py`: Unit tests for store and pipeline behavior.
- `tests/test_api.py`: Integration tests for API + dashboard routes.

## API reference

### `GET /health`
Simple health-check endpoint.

**Response**

```json
{"status": "ok"}
```

### `POST /agent/run`
Runs the agent pipeline end-to-end for the given profile.

**Request body**

```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "resume_text": "5 years building ML products and automations",
    "interests": ["ai", "climate", "robotics"]
  },
  "max_opportunities": 5
}
```

**Validation notes**

- `interests` must contain at least one value.
- `max_opportunities` must be between `1` and `25`.

### `GET /applications`
Returns all currently tracked applications in reverse discovery order.

### `GET /admin`
Renders an HTML dashboard showing aggregate metrics and application details.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Dashboard: `http://127.0.0.1:8000/admin`

## Running tests

```bash
pytest
```

Test coverage includes:

- unit tests for `InMemoryStore` ordering behavior,
- unit tests for end-to-end pipeline state transitions,
- integration tests for `health`, `agent/run`, `applications`, and `admin` routes,
- validation coverage for empty `interests` payload rejection.

## Extension guide (recommended next steps)

1. **Discovery providers**
   - Replace `_discover` with real web/job search APIs (e.g., Tavily, SerpAPI, custom feeds).
2. **Application automation**
   - Add browser automation with explicit user approval gates and anti-abuse guardrails.
3. **Contact enrichment**
   - Integrate provider(s) for hiring manager/recruiter lookup and confidence scoring.
4. **Notification transport**
   - Send delivery events via email, Slack, or SMS with idempotency handling.
5. **Persistence and auth**
   - Move from in-memory store to a database.
   - Add admin authentication and role-based access control.
6. **Observability and compliance**
   - Add audit logs, structured tracing, policy checks, and error reporting.
