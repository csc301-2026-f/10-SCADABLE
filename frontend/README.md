# Live SWOT frontend

Next.js / React dashboard, Clerk invitation-based sign-in and TanStack Query. See the [developer guide](../DEVELOPERS.md) for full-stack setup, environment variables, tests and deployments.

From the repository root, run `./setup.sh`, then `cd frontend && npm run dev`. For UI-only fixtures, copy `.env.example` to `.env.local`, explicitly set `NEXT_PUBLIC_USE_MOCKS=true`, install with `npm ci` and run `npm run dev`. Fixtures are off by default. Real research needs configured Clerk and backend credentials.

Checks: `npm run lint`, `npm test`, `npm run typecheck`. The Node/TypeScript tests cover application logic and API-client contracts without external requests. Vercel root directory is `frontend`; configure each environment's API URLs and server-only variables separately. Use reviewed feature-branch PRs targeting course `main`.

See [auth access](docs/auth-access.md) for invitations and the [main README](../README.md) for workflows. Earlier [frontend plan](docs/Live-SWOT-Frontend-Plan.md) is historical design context, not current setup.
