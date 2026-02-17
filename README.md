# agent-apply

Monorepo for a two-plane job automation system:

1. Main backend (`/backend`) for users/resumes/preferences, run orchestration, and callback ingestion.
2. Cloud automation service (`/cloud_automation`) for job discovery, match indexing, and autonomous apply workflows.
3. Next.js frontend (`/frontend`) demo UI.

## Architecture at a glance

- **Main backend (Render Project A)**
  - API + system of record (`users`, `resumes`, `preferences`, `job_matches`, `application_attempts`, `external_run_refs`, `webhook_events`)
  - Calls cloud API with signed service JWT
  - Receives signed/idempotent apply attempt callbacks
- **Cloud automation (Render Project B)**
  - Job discovery adapters + normalized job index
  - Async match runs and apply runs
  - Callback emitter back to main backend
- **Legacy compatibility**
  - Existing `/agent/run`, `/applications`, and `/admin` endpoints remain available.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy env file:

```bash
cp .env.example .env
```

## Run main backend

```bash
uvicorn backend.main:app --reload --port 8000
```

Main backend docs: [backend/README.md](backend/README.md)

## Run cloud automation service

```bash
uvicorn cloud_automation.main:app --reload --port 8100
```

Cloud service docs: [cloud_automation/README.md](cloud_automation/README.md)

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

## Migrations (main backend)

```bash
alembic upgrade head
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Notes

- Current source adapters are scaffolded synthetic adapters with the target interface for real integrations.
- Cloud apply service includes autonomous run lifecycle, attempt statuses, and signed callback wiring.
- For production scraping and auto-submit usage, add legal/compliance review and provider-specific hardening.
