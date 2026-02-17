# Frontend (Next.js + GraphQL)

This frontend is a complete Next.js app with an integrated GraphQL API (`/api/graphql`) and UI flows for:

- account signup/login,
- user preferences management,
- resume upload,
- review of submitted applications,
- adjusting application-per-day rate,
- manually running the application agent.

## Run locally

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

## GraphQL operations

### Queries
- `me`: current authenticated user and preferences.
- `applications`: all applications generated for the current user.

### Mutations
- `signup(name, email, password)`
- `login(email, password)`
- `updatePreferences(interests, applicationsPerDay)`
- `uploadResume(filename, text)`
- `runAgent`

## Auth model

- Signup/login returns a session token.
- Token is stored in `localStorage` as `agent_apply_token`.
- Apollo client sends it as `Authorization: Bearer <token>`.
