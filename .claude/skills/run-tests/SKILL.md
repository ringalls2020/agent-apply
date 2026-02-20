---
name: run-tests
description: Run the full test suite with in-memory SQLite databases
disable-model-invocation: true
arguments:
  - name: scope
    description: "Optional test scope: 'all' (default), 'backend', 'cloud', 'frontend', or a specific test file path"
    required: false
---

# Run Tests

Run the project test suite using in-memory SQLite (no live database required).

## Commands by scope

### all (default)
```bash
source .venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q
```

### backend
```bash
source .venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q tests/test_api.py tests/test_services.py tests/test_orchestration_api.py tests/test_db.py tests/test_architecture.py
```

### cloud
```bash
source .venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q tests/test_cloud_automation_api.py tests/test_token_first_discovery.py
```

### frontend
```bash
cd frontend && npm run lint
```

### specific file
```bash
source .venv/bin/activate && DATABASE_URL=sqlite+pysqlite:///:memory: JOBS_DATABASE_URL=sqlite+pysqlite:///:memory: pytest -q <scope>
```

## Rules

- Always use in-memory SQLite — never connect to a live database
- No live network calls in tests (per AGENTS.md)
- Report pass/fail counts and any failures to the user
