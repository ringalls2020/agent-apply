# Cloud Automation Service (`cloud_automation`)

Independent FastAPI service for:

- scheduled source discovery and normalized job indexing
- async match run orchestration
- async apply run orchestration
- signed callbacks to main backend per apply attempt

## Endpoints

- `GET /health`
- `POST /v1/discovery/run`
- `GET /v1/jobs/search`
- `POST /v1/match-runs`
- `GET /v1/match-runs/{run_id}`
- `POST /v1/apply-runs`
- `GET /v1/apply-runs/{run_id}`

All `/v1/*` endpoints require service JWT auth.

## Environment variables

### Database

- `JOBS_DATABASE_URL` (default: `sqlite+pysqlite:///./jobs_intel.db`)

### Auth (main -> cloud)

- `CLOUD_AUTOMATION_SIGNING_SECRET` (default: `dev-cloud-signing-secret`)
- `CLOUD_AUTOMATION_EXPECTED_AUDIENCE` (default: `job-intel-api`)
- `CLOUD_AUTOMATION_EXPECTED_ISSUER` (default: `main-api`)
- `CLOUD_AUTOMATION_REQUIRED_CLIENT_SUBJECT` (optional mTLS subject assertion)

### Discovery cadence

- `DISCOVERY_INTERVAL_SECONDS` (default: `21600`, 6h)
- `ENABLE_EMBEDDED_DISCOVERY_LOOP` (default: `false`; enable only for single-process local development)

### Job listing freshness

- `JOB_LISTING_TTL_DAYS` (default: `21`; listings older than this window are archived and excluded unless `include_archived=true`)

### Live adapter seeds (optional)

- `USE_ONLY_LIVE_ADAPTERS` (default: `false`; set to `true` to disable synthetic fallback sources)
- `GREENHOUSE_BOARD_TOKENS` (comma-separated board tokens)
- `LEVER_COMPANIES` (comma-separated company slugs)
- `SMARTRECRUITERS_COMPANIES` (comma-separated company slugs)

When `USE_ONLY_LIVE_ADAPTERS=true`, discovery only uses the configured live connectors above.
If seed variables are empty in that mode, discovery returns no jobs.

### Callback emitter (cloud -> main)

- `MAIN_CALLBACK_URL` (optional; if unset, callback delivery is disabled)
- `CLOUD_CALLBACK_ISSUER` (default: `job-intel-api`)
- `CLOUD_CALLBACK_AUDIENCE` (default: `main-api`)
- `CLOUD_CALLBACK_SIGNING_SECRET` (default: falls back to `CLOUD_AUTOMATION_SIGNING_SECRET`)
- `CLOUD_CALLBACK_SIGNATURE_SECRET` (default: same as callback signing secret)

### Autonomous apply + LLM

- `ENABLE_AUTONOMOUS_BROWSING` (default: `false`; enables Playwright executor)
- `PLAYWRIGHT_HEADLESS` (default: `true`)
- `PLAYWRIGHT_NAV_TIMEOUT_SECONDS` (default: `20`)
- `PLAYWRIGHT_ACTION_TIMEOUT_SECONDS` (default: `5`)
- `PLAYWRIGHT_CAPTURE_SCREENSHOTS` (default: `true`)
- `OPENAI_API_KEY` (optional; enables LLM-generated long-form answers)
- `OPENAI_MODEL` (default: `gpt-4.1-mini`)
- `OPENAI_TIMEOUT_SECONDS` (default: `20`)

## Local run

```bash
uvicorn cloud_automation.main:app --reload --port 8100
```

## Worker entrypoints

Optional separate worker process entrypoints are included:

- `python -m cloud_automation.workers.discovery_worker`
- `python -m cloud_automation.workers.match_worker`
- `python -m cloud_automation.workers.apply_worker`
- `python -m cloud_automation.workers.maintenance_worker`

## Adapter interface

Each adapter follows:

- `discover(seeds, cursor) -> list[JobURL]`
- `fetch(url) -> RawDocument`
- `parse(raw, url) -> NormalizedJob`
- `next_cursor() -> Optional[str]`

Live adapters are currently implemented for:

- greenhouse
- lever
- smartrecruiters

Synthetic fallback adapters remain available (when `USE_ONLY_LIVE_ADAPTERS=false`) for:

- linkedin
- indeed
- greenhouse
- lever
- workday
- smartrecruiters
- ashby
- ziprecruiter
- wellfound
- generic careers
