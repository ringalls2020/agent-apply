# AGENTS Instructions (Repository Root)

## Scope And Precedence
- This file applies to the entire repository rooted at `/Users/riza/dev-projects/agent-apply`.
- Child `AGENTS.md` files in subdirectories override or refine this file for files under their subtree.
- If rules conflict, use the most specific `AGENTS.md` closest to the edited file.

## Repository Map
- `backend/`: Main FastAPI backend, GraphQL API, user/profile/resume storage, orchestration, callback ingestion.
- `cloud_automation/`: Cloud FastAPI service, discovery/matching/apply services, async workers.
- `frontend/`: Next.js app and GraphQL BFF route (`/api/graphql`).
- `tests/`: Regression and architecture test suite.
- `alembic/`: Main backend database migrations.

## Global Non-Negotiables
- Do not change public API contracts (GraphQL schema shape, callback headers/signing expectations, BFF proxy semantics) unless explicitly requested.
- Prefer additive, migration-safe database changes. No destructive migrations without explicit approval.
- Keep security-sensitive behavior intact: auth token verification, signature validation, idempotency, and encryption handling.
- Do not add live-network dependencies in tests.
- Keep behavior changes paired with tests in the same change set.

## Change-Class To Required Checks
- Backend-only code changes:
  - Run backend AGENTS checks.
- Cloud-only code changes:
  - Run cloud AGENTS checks.
- Frontend-only code changes:
  - Run frontend AGENTS checks.
- Cross-cutting or uncertain blast radius:
  - Run root baseline checks plus affected domain checks.
- Test-only changes:
  - Run tests AGENTS checks and affected suites.

## Security And Migration Safety
- Use Alembic for backend schema evolution; do not hand-edit production schema out-of-band.
- Avoid logging secrets, tokens, raw resume file contents, or signed payload material.
- Preserve idempotency behavior for callback and queue processing code.
- Preserve FK-safe write ordering in DB paths (parent rows before child rows).

## Required Baseline Checks
- `source /Users/riza/dev-projects/agent-apply/.venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q`
- `cd /Users/riza/dev-projects/agent-apply/frontend && npm run lint`

## Definition Of Done
- Scope-specific AGENTS checks passed.
- Relevant tests updated or confirmed unchanged by explicit reasoning.
- Documentation and comments updated where behavior or operating assumptions changed.
