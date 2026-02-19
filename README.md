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
- **Main API**
  - GraphQL operations are served from `POST /graphql`; `/admin` remains available.

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

Create local PostgreSQL databases (one-time):

```bash
createdb agent_apply
createdb jobs_intel
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

Match/apply runs are enqueue-only at the API layer and are processed by workers
(`match_worker`, `apply_worker`) for single-consumer execution.

Token-first sourcing is also worker-driven:

- `seed_manifest_worker` builds canonical JSON/CSV manifests from upstream source pages.
- `discovery_worker` consumes async refresh kicks and periodic discovery cycles.
- `common_crawl_worker` performs daily Method B token extraction.

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

## Refactor Tracking

The current refactor is being shipped in compatibility-safe slices.

Non-breaking policy:

- Keep existing HTTP/GraphQL contracts stable unless explicitly documented.
- Keep legacy compatibility endpoints (`/agent/run`, `/applications`) as `410 Gone`.
- Keep `/admin` gating behavior controlled by env + optional secret.

Slice checkpoints:

1. Stability/security hardening (`/v1/agent/run` async polling, atomic callback idempotency).
2. Service module boundaries with compatibility shims.
3. Cloud HTTP client lifecycle + callback retry/backoff + worker concurrency controls.
4. UTC default cleanup + index additions + runtime cloud index ensure.
5. Frontend GraphQL/frontend modularization without schema/operation breakage.
6. Dependency upgrade sweep with full regression validation.

## Notes

- Token-first discovery now uses:
  - Seed manifest builder (`SEED_SOURCE_PAGE_URLS` -> internal JSON/CSV manifests)
  - Method A strict-robots crawler seeded by `SEED_MANIFEST_URLS`
  - Method B Common Crawl token extraction
  - validated ATS feed ingestion for Greenhouse/Lever/SmartRecruiters
- Cloud apply service includes autonomous run lifecycle, attempt statuses, signed callback wiring, and optional Playwright/LLM-driven form answering via env flags.
- For production scraping and auto-submit usage, add legal/compliance review and provider-specific hardening.
