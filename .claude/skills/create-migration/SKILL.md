---
name: create-migration
description: Create an Alembic migration with FK-safe validation
disable-model-invocation: true
arguments:
  - name: message
    description: Short description for the migration (e.g. "add status column to applications")
    required: true
---

# Create Alembic Migration

Generate a new Alembic auto-migration from the current SQLAlchemy model state.

## Steps

1. Activate the virtualenv:
   ```bash
   source .venv/bin/activate
   ```

2. Run Alembic autogenerate with the provided message:
   ```bash
   alembic revision --autogenerate -m "<message>"
   ```

3. Read the generated migration file and validate:
   - FK-safe ordering: parent table operations come before child table operations
   - No destructive operations (DROP TABLE, DROP COLUMN) unless the message explicitly says so
   - Uses `batch_alter_table` for SQLite compatibility where needed
   - Downgrade function correctly reverses the upgrade

4. Report the generated migration file path and a summary of operations to the user.

## Rules

- Never hand-edit existing migrations — only validate the auto-generated output
- If validation finds issues, report them to the user rather than auto-fixing
- All schema changes MUST go through this flow (per AGENTS.md)
