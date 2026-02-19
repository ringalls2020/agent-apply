# Frontend (Next.js + GraphQL)

This frontend is a complete Next.js app with an integrated GraphQL BFF (`/api/graphql`) that proxies to the backend GraphQL API, and UI flows for:

- account signup/login,
- user preferences management,
- application profile management (including resume upload),
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

Set backend base URL for the GraphQL BFF (server-side):

```bash
export BACKEND_API_BASE_URL=http://127.0.0.1:8000
```

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

- Signup/login returns a backend-issued JWT.
- Token is stored in `localStorage` as `agent_apply_token`.
- Apollo client sends it as `Authorization: Bearer <token>`.
