# agent-apply

This repository includes:

1. A FastAPI backend in `/backend` with SQL persistence (SQLite by default, PostgreSQL via `DATABASE_URL`).
2. A Next.js + GraphQL frontend app in `/frontend`.

## Backend quick start

Create environment file (optional, for overrides):

```bash
cp .env.example .env
```

Run backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Default local database:

- `sqlite+pysqlite:///./agent_apply.db` (no PostgreSQL required)

Use PostgreSQL instead (optional):

```bash
export DATABASE_URL=postgresql+psycopg://<db_user>:<db_password>@localhost:5432/agent_apply
```

If your local Postgres cluster doesn't have a `postgres` role, set `<db_user>` to an existing role (often your macOS username).

Backend URLs:

- `http://127.0.0.1:8000/docs` (Swagger UI)
- `http://127.0.0.1:8000/redoc` (ReDoc)
- `http://127.0.0.1:8000/admin` (HTML dashboard)

Complete backend documentation: `backend/README.md`

## Frontend capabilities delivered

- ✅ Account signup/login
- ✅ Preferences page (interests + applications/day rate)
- ✅ Resume upload flow
- ✅ Review of submitted applications
- ✅ Run agent action and contact details visibility
- ✅ Sleek dark UI with reusable navigation and dashboards

## Frontend quick start

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000`.

## Frontend structure

- `frontend/src/app/signup/page.tsx` – signup flow
- `frontend/src/app/login/page.tsx` – login flow
- `frontend/src/app/preferences/page.tsx` – update preferences and application rate
- `frontend/src/app/resume/page.tsx` – resume upload
- `frontend/src/app/applications/page.tsx` – review applications and run agent
- `frontend/src/app/api/graphql/route.ts` – GraphQL schema + resolvers
- `frontend/src/lib/store.ts` – in-memory data/session/application model

## Notes

- The Next.js app is fully functional with in-memory persistence suitable for local/demo use.
- For production, move storage/session logic from `store.ts` to a durable datastore and hardened auth.
- The FastAPI backend defaults to a local SQLite file and can be pointed at PostgreSQL via `DATABASE_URL`.
