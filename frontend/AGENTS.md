# AGENTS Instructions (frontend)

## Scope
- Applies to `/Users/riza/dev-projects/agent-apply/frontend`.
- Covers Next.js UI, GraphQL BFF route, resolver layer, mappers, and client auth handling.

## Critical Contracts To Preserve
- BFF proxy behavior remains:
  - `POST /api/graphql` forwards to backend `POST /graphql`
  - forwards `authorization` header when present
- Frontend auth token handling remains consistent with backend expectations.
- Resolver and mapper changes remain compatible with backend response shapes.

## UI And State Guardrails
- Preserve application status semantics and action eligibility rules.
- Keep optimistic updates and fallback behavior aligned with backend mutation outcomes.
- Avoid introducing UI-only status labels that conflict with backend enums.

## Required Checks For Frontend Changes
- `cd /Users/riza/dev-projects/agent-apply/frontend && npm run lint`
- For contract-impacting frontend changes:
  - `cd /Users/riza/dev-projects/agent-apply/frontend && npm run build`
