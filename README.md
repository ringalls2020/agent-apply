# agent-apply

A full starter implementation of an **agentic job-application application** that takes a resume + interests, discovers opportunities, applies, enriches point-of-contact details, notifies, and provides an admin dashboard for management.

## Implemented app capabilities

This PR now includes the complete app skeleton discussed, not only a partial API:

- **Agent pipeline orchestration** (`discover -> apply -> contact -> notify`)
- **HTTP API** for execution and management
- **Admin dashboard** to view/manage application state
- **Mutable application lifecycle controls** (update status, archive, delete)
- **Validation layer** for input constraints
- **Optional JSON-file persistence** via environment variable
- **Unit and integration test suite**

## Architecture

```text
app/
  main.py          # app factory, routes, dashboard wiring
  models.py        # request/response/domain models
  services.py      # discover/apply/contact/notify services + agent coordinator
  store.py         # in-memory + JSON-file store
  templates/
    dashboard.html # admin UI
tests/
  test_services.py # unit tests
  test_api.py      # integration tests
```

## API reference

### Core

- `GET /health`
- `POST /agent/run`
- `GET /applications`

### Management

- `PATCH /applications/{application_id}`
  - Update application status (`discovered`, `applied`, `contacted`, `notified`, `archived`)
  - Add optional notes
- `DELETE /applications/{application_id}`
  - Remove an application from admin view/state

### Dashboard

- `GET /admin`
  - Renders HTML dashboard with summary cards + application table

## Example run payload

```json
{
  "profile": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "resume_text": "5+ years building agentic and ML automation systems across recruiting and workflow tooling.",
    "interests": ["ai", "climate", "robotics"],
    "locations": ["Remote", "NYC"]
  },
  "max_opportunities": 5,
  "auto_apply": true
}
```

## Validation rules

- `profile.resume_text` minimum 50 characters
- `profile.interests` must be non-empty
- `profile.email` and contact emails validated
- `max_opportunities` must be between 1 and 25

## Persistence mode

By default, the app uses in-memory storage.

To persist data to disk:

```bash
export AGENT_APPLY_STORE_FILE=.data/applications.json
```

Then start the app normally.

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

## Tests

```bash
pytest -q
```

Coverage includes:

- service/pipeline unit tests,
- store ordering behavior,
- auto-apply on/off behavior,
- API integration tests for run/list/update/delete flows,
- validation failure behavior,
- dashboard rendering,
- file persistence behavior with environment configuration.

## Productionization checklist

1. Plug in real web search providers in `DiscoveryService`.
2. Add secure browser automation and consent gates in `ApplyService`.
3. Integrate contact enrichment APIs and confidence scoring.
4. Add notification channels (email/slack/sms/webhooks).
5. Move to real DB + migrations (PostgreSQL recommended).
6. Add authN/authZ for dashboard management.
7. Add observability, retries, audit logs, and policy controls.
