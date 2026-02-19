# AGENTS Instructions (backend)

## Scope
- Applies to `/Users/riza/dev-projects/agent-apply/backend`.
- Covers FastAPI app setup, GraphQL schema/resolvers, orchestration, auth, callback verification, and persistence for main backend records.

## Critical Contracts To Preserve
- GraphQL public contract remains backward-compatible unless explicitly requested.
- Callback verification contract remains intact:
  - JWT auth (`authorization: Bearer ...`)
  - signature headers (`x-cloud-timestamp`, `x-cloud-nonce`, `x-cloud-signature`)
  - idempotency header (`x-idempotency-key`)
- User auth and profile encryption semantics must not regress.

## Backend Guardrails
- Keep orchestration behavior deterministic: run refs, status transitions, and callback mapping must remain stable.
- Do not bypass model validation for user-facing payloads.
- Keep schema updates migration-driven via Alembic in `/Users/riza/dev-projects/agent-apply/alembic` only.
- Keep backend/cloud API boundary explicit through cloud client service interfaces.

## Required Checks For Backend Changes
- `source /Users/riza/dev-projects/agent-apply/.venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q /Users/riza/dev-projects/agent-apply/tests/test_api.py /Users/riza/dev-projects/agent-apply/tests/test_services.py /Users/riza/dev-projects/agent-apply/tests/test_orchestration_api.py /Users/riza/dev-projects/agent-apply/tests/test_db.py /Users/riza/dev-projects/agent-apply/tests/test_architecture.py`

## Extra Requirements
- If GraphQL behavior changes, add or update tests in `tests/test_api.py`.
- If callback/auth logic changes, add or update tests in `tests/test_orchestration_api.py` and related API tests.
