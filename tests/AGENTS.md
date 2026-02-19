# AGENTS Instructions (tests)

## Scope
- Applies to `/Users/riza/dev-projects/agent-apply/tests`.
- Covers all test policy, regression requirements, mocking strategy, and architecture guards.

## Testing Policy
- Use deterministic tests only. Do not depend on live external network APIs.
- Prefer explicit fixtures, mock transports, and controlled clocks for asynchronous flows.
- When behavior changes, tests must be added or updated in the same change.
- Changed-area suites must run before merge.

## Regression Priorities
- Preserve architecture guards (service boundaries, legacy import constraints).
- Preserve workflow/status regressions for discovery, matching, apply, callback, and GraphQL operations.
- For throughput-sensitive store changes, include query-count or statement-count coverage where practical.

## Baseline Test Check
- `source /Users/riza/dev-projects/agent-apply/.venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q /Users/riza/dev-projects/agent-apply/tests/test_architecture.py`
