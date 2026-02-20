# CLAUDE.md — agent-apply

## Project Overview

Monorepo for a two-plane automated job application system. Discovers remote jobs via ATS token extraction, matches them to user profiles, and autonomously applies on the user's behalf.

**Three services:**
- `backend/` — Main FastAPI backend (port 8000): GraphQL API, user/profile/resume storage, orchestration, callback ingestion
- `cloud_automation/` — Cloud FastAPI service (port 8100): job discovery, matching, autonomous apply workers
- `frontend/` — Next.js 14 frontend (port 3000): GraphQL BFF proxy at `/api/graphql`

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2, Alembic, Graphene (GraphQL) |
| Cloud | FastAPI, Playwright, async workers (single-consumer queue pattern) |
| Frontend | Next.js 14 (App Router), React 18, TypeScript, Apollo Client, Tailwind CSS |
| Databases | PostgreSQL (prod), SQLite in-memory (tests) |
| Auth | HS256 JWT (user auth), HMAC-signed callbacks (cloud→main) |

## Key Commands

```bash
# Activate virtualenv (always required for Python work)
source .venv/bin/activate

# Run main backend
uvicorn backend.main:app --reload --port 8000

# Run cloud automation service
uvicorn cloud_automation.main:app --reload --port 8100

# Run frontend
cd frontend && npm run dev

# Run all tests (use SQLite in-memory — no live DB required)
source .venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q

# Lint frontend
cd frontend && npm run lint

# Run database migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "describe change"
```

## Agent Guidance Files

Scoped `AGENTS.md` files contain non-negotiables, required checks, and conventions per domain:

- **Root:** `AGENTS.md` — global non-negotiables and baseline checks
- **Backend:** `backend/AGENTS.md`
- **Cloud:** `cloud_automation/AGENTS.md`
- **Frontend:** `frontend/AGENTS.md`
- **Tests:** `tests/AGENTS.md`

**Precedence:** most specific (closest to file) wins over root.

## Non-Negotiables

- Do not change GraphQL schema shape, callback headers/signing, or BFF proxy semantics without explicit request.
- All schema changes via Alembic migrations — no hand-editing.
- Preserve FK-safe write ordering (parent rows before child rows).
- Preserve idempotency on callbacks and queue processing.
- Never log secrets, tokens, raw resume content, or signed payload material.
- No live network calls in tests.
- Pair behavior changes with tests in the same changeset.

## Architecture Notes

**Authentication:**
- User JWT: HS256, signed with `USER_AUTH_SIGNING_SECRET`, stored in localStorage as `agent_apply_token`
- Service JWT (main → cloud): signed with `CLOUD_AUTOMATION_SIGNING_SECRET`
- Callback auth (cloud → main): dual JWT + HMAC with headers `x-cloud-timestamp`, `x-cloud-nonce`, `x-cloud-signature`

**GraphQL endpoint:** `POST /graphql` (backend), proxied via `frontend/src/app/api/graphql/route.ts`

**Worker pattern:** single-consumer, claim→execute→finalize, idempotent retries

**Database:** two DBs — `agent_apply` (main backend) and `jobs_intel` (cloud automation)

## Environment Setup

```bash
cp .env.example .env
createdb agent_apply
createdb jobs_intel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key env vars: `DATABASE_URL`, `JOBS_DATABASE_URL`, `CLOUD_AUTOMATION_SIGNING_SECRET`, `USER_AUTH_SIGNING_SECRET`, `USER_PROFILE_ENCRYPTION_KEY`, `CLOUD_AUTOMATION_BASE_URL`

## Definition of Done

1. Scope-specific AGENTS checks pass.
2. Relevant tests updated or confirmed unchanged with explicit reasoning.
3. Docs/comments updated where behavior or assumptions changed.
