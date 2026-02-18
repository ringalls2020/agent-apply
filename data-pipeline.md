STAGE 1: Build Seed Manifest
─────────────────────────────
seed_manifest_worker
│
├─ Scrapes 10 remoteintech.company pages
├─ Extracts career page links (630 found)
└─ Writes → seed_manifest_entries table
→ Served via GET /internal/seed-manifests/companies.json

STAGE 2: Crawl Career Pages & Extract ATS Tokens
─────────────────────────────────────────────────
discovery_worker (Method A) common_crawl_worker (Method B)
│ │
├─ Fetches seed manifest from ├─ Queries Common Crawl CDX index
│ cloud API (630 career URLs) │ for greenhouse/lever/SR URLs
├─ Checks robots.txt per domain └─ Extracts tokens from CC records
├─ Crawls each career page HTML │
├─ extract_ats_tokens_from_text() │
│ (finds board slugs like │
│ "boards.greenhouse.io/artlogic") │
└─ Writes → ats_tokens (status=pending) ┘
ats_token_evidence

STAGE 3: Validate Tokens Against Real ATS APIs
───────────────────────────────────────────────
discovery_worker (within same run, after crawl)
│
├─ For each pending token:
│ greenhouse → GET boards-api.greenhouse.io/v1/boards/{token}/jobs
│ lever → GET api.lever.co/v0/postings/{token}
│ smartrecr → GET api.smartrecruiters.com/v1/companies/{token}/postings
│
├─ 200 OK → status = "validated" ◄── THESE are usable
├─ 404 → status = "invalid"
└─ 5xx/429 → status = "pending" (retry later)

STAGE 4: Ingest Real Job Listings
──────────────────────────────────
discovery_worker (within same run, after validation)
│
├─ For each validated token, creates a live adapter:
│ GreenhouseLiveAdapter(["artlogic", "modernhealth", ...])
│ LeverLiveAdapter(["caremessage", "bluecatnetworks", ...])
│ SmartRecruitersLiveAdapter([...])
│
├─ Each adapter fetches full job listings from official APIs
├─ Parses into NormalizedJob (title, company, location, apply_url, ...)
└─ Writes → normalized_jobs table ◄── THIS is where real jobs live
raw_job_documents

STAGE 5: Match Jobs to User
────────────────────────────
User clicks "Run Agent" in frontend
│
├─ Frontend → POST /api/graphql (RunAgent mutation)
│ → Backend → cloud_client.kick_discovery()
│ → cloud_client.start_match_run(preferences)
│ → Polls GET /v1/match-runs/{id} until completed
│
│ match_worker picks up queued run:
│ ├─ store.search_jobs(keywords=user_interests, location=...)
│ ├─ Scores each job by keyword overlap
│ ├─ Returns top N ranked matches
│ └─ Writes → match_results, sets match_runs.status=completed
│
└─ Backend stores matches as application_attempts in agent_apply.db

STAGE 6: Apply to Jobs
───────────────────────
User selects applications → "Apply" in frontend
│
├─ Frontend → ApplySelectedApplications mutation
│ → Backend → cloud_client.start_apply_run(jobs)
│
│ apply_worker picks up queued run:
│ ├─ For each job:
│ │ ├─ Playwright navigates to apply_url
│ │ ├─ OpenAI generates form answers (cover letter, custom Qs)
│ │ ├─ Fills and submits application form
│ │ └─ Records attempt status (submitted/failed/needs_review)
│ │
│ └─ CallbackEmitter fires signed HTTP callback:
│ POST http://127.0.0.1:8000/internal/cloud/callbacks/apply-result
│ (JWT + HMAC signature + idempotency key)
│
└─ Backend receives callback → updates application_attempts status
