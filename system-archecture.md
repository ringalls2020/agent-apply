┌─────────────────────────────────────────────────────────────────────────────────┐
│ USER'S BROWSER │
│ http://localhost:3000 │
└──────────────────────────────────┬──────────────────────────────────────────────┘
│
Apollo Client (GraphQL over HTTP)
Authorization: Bearer <user JWT>
│
▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ FRONTEND (Next.js :3000) │
│ │
│ Pages: /login /signup /dashboard /profile │
│ │
│ GraphQL BFF: /api/graphql/route.ts │
│ - Proxies POST /api/graphql → backend POST /graphql │
│ - Forwards Authorization header │
│ - BACKEND_API_BASE_URL = http://127.0.0.1:8000 │
│ │
│ Operations: │
│ Signup, Login, Me, UpdatePreferences, UploadResume, │
│ RunAgent, ApplicationsSearch, ApplySelectedApplications, │
│ MarkApplicationViewed, MarkApplicationApplied, Profile, │
│ UpdateProfile │
└──────────────────────────────────┬──────────────────────────────────────────────┘
│
POST /graphql (JSON)
│
▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ MAIN BACKEND (FastAPI :8000) │
│ DB: agent_apply.db │
│ │
│ Endpoints: │
│ POST /graphql ─────── Graphene schema (queries + mutations) │
│ GET /admin ────────── Admin dashboard (Jinja2 HTML) │
│ GET /health │
│ POST /internal/cloud/callbacks/apply-result ◄── callback from cloud │
│ │
│ Key Services: │
│ CloudOrchestrationService │
│ ├─ RunAgent mutation triggers: │
│ │ 1. cloud_client.kick_discovery() │
│ │ 2. cloud_client.start_match_run(preferences) │
│ │ 3. Polls cloud_client.get_match_run(id) until completed │
│ │ 4. Stores matched jobs as application records │
│ ├─ ApplySelectedApplications mutation triggers: │
│ │ cloud_client.start_apply_run(jobs) │
│ └─ process_apply_attempt_callback() on callback receipt │
│ │
│ CloudAutomationClient (HTTP + service JWT) │
│ POST /v1/discovery/kick ──────────┐ │
│ POST /v1/match-runs ──────────────┤ │
│ GET /v1/match-runs/{id} ─────────┤ │
│ POST /v1/apply-runs ──────────────┤ │
│ GET /v1/apply-runs/{id} ─────────┘ │
│ │ │
│ Auth: │ Callback verification: │
│ User JWTs (HS256) │ JWT + HMAC body signature │
│ USER_AUTH_SIGNING_SECRET │ x-idempotency-key dedup │
│ │ clock skew check │
│ Tables: │ │
│ users, resumes, preferences, │ │
│ job_matches, application_attempts, │ │
│ external_run_refs, webhook_events │ │
└────────────────────────────────────────┼────────────────────────────────────────┘
│
Service JWT (HS256)
CLOUD_AUTOMATION_SIGNING_SECRET
│
▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLOUD AUTOMATION API (FastAPI :8100) │
│ DB: jobs_intel.db │
│ │
│ Endpoints: │
│ POST /v1/discovery/kick ──── enqueues discovery_refresh_requests row │
│ POST /v1/discovery/run ───── runs discovery synchronously (blocking) │
│ GET /v1/jobs/search ─────── keyword search over normalized_jobs │
│ POST /v1/match-runs ──────── creates match_runs row (status=queued) │
│ GET /v1/match-runs/{id} ─── returns match run status + results │
│ POST /v1/apply-runs ──────── creates apply_runs row (status=queued) │
│ GET /v1/apply-runs/{id} ─── returns apply run status + attempts │
│ GET /internal/seed-manifests/companies.json ── serves manifest to workers │
│ GET /internal/seed-manifests/companies.csv │
│ │
│ NOTE: This API only ENQUEUES work. Workers do the processing. │
└──────────────────┬──────────────────────────────────────────────────────────────┘
│
│ All workers share jobs_intel.db (SQLite)
│ All workers poll DB tables for queued rows
│
┌─────────────┼──────────────┬──────────────────┬───────────────────┐
│ │ │ │ │
▼ ▼ ▼ ▼ ▼
┌─────────┐ ┌───────────┐ ┌───────────┐ ┌────────────────┐ ┌──────────────────┐
│ SEED │ │ DISCOVERY │ │ COMMON │ │ MATCH │ │ APPLY │
│MANIFEST │ │ WORKER │ │ CRAWL │ │ WORKER │ │ WORKER │
│ WORKER │ │ │ │ WORKER │ │ │ │ │
└────┬────┘ └─────┬─────┘ └─────┬─────┘ └───────┬────────┘ └────────┬─────────┘
│ │ │ │ │
▼ ▼ ▼ ▼ ▼

Scrapes Fetches Queries Polls for Polls for
remotein- seed mani- Common queued match_runs queued apply_runs
tech.company fest from Crawl index ────────────────── ──────────────────
HTML pages cloud API, for ATS URL Searches Playwright browser
for careers crawls each patterns normalized_jobs automation fills
links career page (greenhouse, by user keywords application forms
──────────── for ATS lever, etc.) + location, + LLM-generated
Writes to: tokens ──────────── scores & ranks, answers (OpenAI)
seed_mani- ────────────── writes results to ──────────────────
fest_entries Writes to: Writes to: match_results Fires signed
ats_tokens ats_tokens callback to main + evidence + evidence backend on each
attempt completion
