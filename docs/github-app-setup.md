# GitHub App foundation (backend)

This is an **admin-only backend integration**, not yet the user-facing GitHub
selector or an automatic SCADABLE baseline refresh. The signed `push` webhook
records selected-repo changes in PostgreSQL with `syncStatus: pending`; it does
**not** run the scraper/LLM or mark the product baseline synced.

## GitHub App setup

Create a GitHub App in GitHub's developer settings with:

- Repository **Metadata: Read-only** (automatically available).
- Repository **Contents: Read-only** (needed for feature-file retrieval later).
- Subscribe to the **Push** event.
- Webhook URL: `https://<backend-host>/github/webhook`.
- A cryptographically random webhook secret.

The account admin installs the app on the organization and selects the
repositories the app may access. SCADABLE's own repository choice is then
made through the protected backend `POST /github/selection` endpoint.

## Backend environment variables

Configure these as private secrets in the backend's environment (e.g. Railway):

- `GITHUB_APP_ID` — GitHub App ID.
- `GITHUB_APP_PRIVATE_KEY` — PEM private key, including its BEGIN/END markers.
  Multiline or `\n`-escaped values both work.
- `GITHUB_WEBHOOK_SECRET` — matches the secret in GitHub App settings.
- `GITHUB_INTEGRATION_ADMIN_TOKEN` — separate random bearer token for server-side
  administrative endpoints, **not** a GitHub PAT.

Do not commit secrets, expose the admin token in React/Next.js, or print the
GitHub App private key. Before building the Our Company repository dropdown,
add proper application-user authentication and organization authorization.

## Database / run

The existing root Dockerfile runs `python -m app.db.init_db` before launching
Uvicorn. That `Base.metadata.create_all()` also creates
`github_repository_selection` and `github_webhook_deliveries`. For local setups,
run `cd backend && python -m app.db.init_db` with PostgreSQL `DATABASE_URL` set.

Management endpoints (all require `Authorization: Bearer <ADMIN_TOKEN>`):

- `GET /github/installations` lists authorized installations.
- `GET /github/repositories?installation_id=<id>` lists authorized repositories.
- `POST /github/selection` body: `{"installationId": 1, "repositoryId": 2}`.
- `GET /github/status` returns selected repo / last delivered SHA / pending status.

The GitHub App calls `POST /github/webhook` with its signed request and push
headers. It does **not** use the admin bearer token. Only non-deleted pushes to
`refs/heads/main` for the selected installation/repository are recorded;
merged PRs to `main` produce these pushes too. Duplicate deliveries are ignored.

## Still pending Ali's answer

Once the authoritative repository/path is confirmed, implement extraction of
its product feature file, conversion to the SCADABLE baseline format, and a
background sync worker. `pending` must change to `synced` only when this actual
baseline update finishes. Until then the hardcoded baseline continues to work.
