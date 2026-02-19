# AGENTS Instructions (cloud_automation)

## Scope
- Applies to `/Users/riza/dev-projects/agent-apply/cloud_automation`.
- Covers cloud API routes, services, adapters, and worker entrypoints.

## Critical Contracts To Preserve
- Cloud API auth contract (service JWT verification) must remain stable.
- Callback emitter signing/audience/issuer behavior must remain stable.
- Queue processing semantics must remain single-consumer safe:
  - claim first (`claim_*`)
  - execute
  - finalize status

## Token-First Pipeline Invariants
- Seed manifest generation remains deterministic and traceable.
- Method A/Method B token extraction feeds `ats_tokens` and `ats_token_evidence` correctly.
- Token validation outcomes remain consistent (`validated`, `invalid`, retryable `pending`).
- Ingestion uses validated tokens only.

## Database Throughput And Integrity Guardrails
- Prefer batched lookups/inserts over per-row N+1 query patterns in hot paths.
- Preserve explicit parent/child write ordering for FK safety (flush parent rows before dependent inserts).
- Keep changes additive and migration-safe; no destructive schema operations.

## Worker Loop Guardrails
- Keep worker loops idempotent and resilient to retries.
- Respect env-configured intervals/timeouts; avoid hard-coded production values.
- Maintain structured logging context for worker operations.

## Required Checks For Cloud Changes
- `source /Users/riza/dev-projects/agent-apply/.venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q /Users/riza/dev-projects/agent-apply/tests/test_cloud_automation_api.py /Users/riza/dev-projects/agent-apply/tests/test_token_first_discovery.py /Users/riza/dev-projects/agent-apply/tests/test_playwright_dev_review.py /Users/riza/dev-projects/agent-apply/tests/test_architecture.py`
