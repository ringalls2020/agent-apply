# agent-apply

This repository now includes:

1. A Python backend prototype (`/app`) from early scaffolding work.
2. A full **Next.js + GraphQL frontend app** in `/frontend` implementing the requested product UX.

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
