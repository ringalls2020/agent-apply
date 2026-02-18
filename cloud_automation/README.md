# Cloud Automation Service (`cloud_automation`)

Independent FastAPI service for:

- token-first ATS discovery (Method A crawler + Method B Common Crawl extraction)
- official feed ingestion for Greenhouse/Lever/SmartRecruiters from validated tokens
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
- `ENABLE_CLOUD_SCHEMA_CREATE` (default: `true` in local/dev/test, `false` otherwise)

### Auth (main -> cloud)

- `CLOUD_AUTOMATION_SIGNING_SECRET` (default: `dev-cloud-signing-secret`)
- `CLOUD_AUTOMATION_EXPECTED_AUDIENCE` (default: `job-intel-api`)
- `CLOUD_AUTOMATION_EXPECTED_ISSUER` (default: `main-api`)
- `CLOUD_AUTOMATION_REQUIRED_CLIENT_SUBJECT` (optional mTLS subject assertion)

### Logging

- JSON structured logs are emitted for API + workers.
- API requests include `request_id`, `http_method`, `http_path`, `status_code`, and `duration_ms`.
- `LOG_LEVEL` (default: `INFO`)
- `SQLALCHEMY_LOG_QUERIES` (default: `false`)

### Discovery cadence

- `DISCOVERY_INTERVAL_SECONDS` (default: `21600`, 6h)
- `COMMON_CRAWL_INTERVAL_SECONDS` (default: `86400`, daily)
- `ENABLE_EMBEDDED_DISCOVERY_LOOP` (default: `false`; enable only for single-process local development)

### Job listing freshness

- `JOB_LISTING_TTL_DAYS` (default: `21`; listings older than this window are archived and excluded unless `include_archived=true`)

### Token-first discovery (Method A + Method B)

- `SEED_MANIFEST_URLS` (comma-separated HTTP URLs to JSON/CSV/newline seed manifests)
- `DISCOVERY_USER_AGENT` (crawler user-agent; include contact channel)
- `DISCOVERY_CONTACT_EMAIL` (optional contact appended to user-agent)
- `DISCOVERY_DEFAULT_CRAWL_DELAY_SECONDS` (default: `2`)
- `DISCOVERY_MAX_RETRIES` (default: `3`)
- `DISCOVERY_TIMEOUT_SECONDS` (default: `20`)
- `TOKEN_VALIDATION_RECHECK_HOURS` (default: `24`)
- `COMMON_CRAWL_LOOKBACK_INDEXES` (default: `2`)
- `COMMON_CRAWL_MAX_PAGES_PER_PATTERN` (default: `3`)
- `COMMON_CRAWL_MAX_RECORDS_PER_PATTERN` (default: `1500`)

Discovery flow:

1. Method A crawls company career pages from seed manifests with strict robots checks.
2. Method B scans Common Crawl index pages for ATS token patterns.
3. Tokens are validated against official ATS feeds.
4. Only validated tokens are used for job ingestion.

### Callback emitter (cloud -> main)

- `MAIN_CALLBACK_URL` (optional; if unset, callback delivery is disabled)
- `CLOUD_CALLBACK_ISSUER` (default: `job-intel-api`)
- `CLOUD_CALLBACK_AUDIENCE` (default: `main-api`)
- `CLOUD_CALLBACK_SIGNING_SECRET` (default: falls back to `CLOUD_AUTOMATION_SIGNING_SECRET`)
- `CLOUD_CALLBACK_SIGNATURE_SECRET` (default: same as callback signing secret)
- `CALLBACK_RETRY_MAX_ATTEMPTS` (default: `3`)
- `CALLBACK_RETRY_BASE_DELAY_MS` (default: `250`)

### Autonomous apply + LLM

- `ENABLE_AUTONOMOUS_BROWSING` (default: `false`; enables Playwright executor)
- `PLAYWRIGHT_HEADLESS` (default: `true`)
- `PLAYWRIGHT_NAV_TIMEOUT_SECONDS` (default: `20`)
- `PLAYWRIGHT_ACTION_TIMEOUT_SECONDS` (default: `5`)
- `PLAYWRIGHT_CAPTURE_SCREENSHOTS` (default: `true`)
- `ENABLE_APPLY_DEV_REVIEW_MODE` (default: `false`; local/dev/test only; opens headed browser, fills form, waits for user submit)
- `APPLY_DEV_REVIEW_SUBMIT_TIMEOUT_SECONDS` (default: `300`)
- `APPLY_DEV_REVIEW_POLL_INTERVAL_MS` (default: `500`)
- `APPLY_DEV_REVIEW_SLOW_MO_MS` (default: `120`)
- `OPENAI_API_KEY` (optional; enables LLM-generated long-form answers)
- `OPENAI_MODEL` (default: `gpt-4.1-mini`)
- `OPENAI_TIMEOUT_SECONDS` (default: `20`)
- `CLOUD_HTTP_TIMEOUT_SECONDS` (default: `20`; reused by callback emitter, OpenAI, crawler, and live feeds)

## Local run

```bash
uvicorn cloud_automation.main:app --reload --port 8100
```

## Worker entrypoints

Optional separate worker process entrypoints are included:

- `python -m cloud_automation.workers.discovery_worker`
- `python -m cloud_automation.workers.common_crawl_worker`
- `python -m cloud_automation.workers.match_worker`
- `python -m cloud_automation.workers.apply_worker`
- `python -m cloud_automation.workers.maintenance_worker`
- `python -m cloud_automation.workers.job_dedupe_backfill` (one-time cleanup runner)

Worker concurrency controls:

- `MATCH_WORKER_CONCURRENCY` (default: `1`)
- `APPLY_WORKER_CONCURRENCY` (default: `1`)

## Data model notes

- `ats_tokens` + `ats_token_evidence` store extracted token registry and provenance.
- `discovery_seeds` + `domain_robots_cache` store seed inventory and robots policy cache.
- `job_identities` provides canonical dedupe keys:
  - preferred: `provider:token:provider_job_id`
  - fallback: `provider:urlhash(normalized_apply_url)`
