# SCADABLE GitHub App product feature sync (backend)

The GitHub backend accepts an installed GitHub App, lets an administrator select an
installation and one repository, loads a **team-defined JSON feature file** from
that repository, validates it, and persists the last successful product baseline
in PostgreSQL. Competitor research and overall SWOT comparisons now use that
synced baseline rather than only the original static profile. The existing
frontend has **not** been changed.

## 1. Create and install the GitHub App

In GitHub Developer Settings → GitHub Apps, register an app with:

- Repository **Metadata: Read** (implicit), **Contents: Read**.
- **Push** event subscription.
- Webhook URL `https://YOUR-BACKEND/github/webhook`, with a strong webhook secret.
- Install on SCADABLE's GitHub account/org; GitHub's own installation UI lets
  the owner authorize only the repositories they wish to make available.

Do not grant repository write, pull-request write, or admin permissions. This
integration reads feature documentation only; it does not edit source code or
create PRs. GitHub App installation is an external, one-time admin action.

## 2. Configure backend environment

Store these values in Railway backend service variables (or local `backend/.env`):

| Variable | Meaning |
| --- | --- |
| `GITHUB_APP_ID` | Numeric GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | Private PEM key, literal newline or `\\n` escaped |
| `GITHUB_WEBHOOK_SECRET` | GitHub App webhook signing secret |
| `GITHUB_INTEGRATION_ADMIN_TOKEN` | Separate, random, server-side admin bearer token |
| `GITHUB_SYNC_ENABLED` | `true` by default when GitHub App credentials exist |
| `GITHUB_SYNC_POLL_SECONDS` | Poll interval; default 60 seconds |
| `GITHUB_RECOMPARE_ON_SYNC` | `false` by default; set `true` to automatically run potentially billable LLM comparisons when features change |

**Do not commit these secrets** or place the admin token in browser code. The
existing backend uses an admin token because there is no proper application-
user/org auth for GitHub operations yet. A future frontend should call a secure
server-side gateway or first implement real user authentication and authorization.

The root `Dockerfile` starts `python -m app.db.init_db`, which creates the new
`github_feature_state` table on existing PostgreSQL databases. It uses the
existing GitHub selection/delivery tables; no destructive migration is needed.

## 3. Choose the file format

The team chooses a fixed, documented file path, defaulting to
`docs/scadable-features.json` inside the **selected** repo. To get started, copy
`backend/examples/scadable-features.json` into that path in a GitHub repository
authorized to the App. This is a **schema example**, not verified live SCADABLE
product information. Replace the examples with SCADABLE-approved facts.

Version 1 uses an ordinary JSON object:

```json
{
  "schema_version": 1,
  "company_name": "SCADABLE",
  "website_url": "https://scadable.com",
  "domain": "Compliance automation",
  "summary": "A security compliance automation platform.",
  "supported_frameworks": ["SOC 2", "ISO 27001"],
  "core_capabilities": ["Continuous evidence collection", "Audit readiness workflows"],
  "differentiators": ["Developer-friendly remediation guidance"]
}
```

`core_capabilities` is required (1–100 distinct nonempty text entries).
`schema_version` must be `1`. Other fields have defaults but should be supplied
for accurate comparisons. Extra keys, unexpected company names, hidden/traversal
paths, malformed JSON, and files over 128 KiB are rejected. Invalid changes do
**not** replace the last validated profile.

## 4. Use the API (backend only)

For local testing, set the bearer token and host in your own private shell:

```bash
export API_URL='http://localhost:8000'
export GITHUB_ADMIN_TOKEN='replace-with-private-token'

# List installed accounts and their installation IDs
curl -fsS -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" \
  "$API_URL/github/installations"

# List authorized repositories for a returned installation (replace 77)
curl -fsS -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" \
  "$API_URL/github/repositories?installation_id=77"

# Select an authorized repository using its GitHub repository ID (replace 101)
curl -fsS -X POST "$API_URL/github/selection" \
  -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"installationId":77,"repositoryId":101}'

# OPTIONAL: override the default docs/scadable-features.json path
curl -fsS -X POST "$API_URL/github/file" \
  -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"filePath":"docs/scadable-features.json"}'

# Manual sync (no LLM calls by default)
curl -fsS -X POST "$API_URL/github/sync" \
  -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN"

# Inspect stored status and the effective baseline
curl -fsS -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" "$API_URL/github/status"
curl -fsS -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN" "$API_URL/github/features"

# Optional: sync and immediately re-run billable LLM comparisons if features changed
curl -fsS -X POST "$API_URL/github/sync?recompare=true" \
  -H "Authorization: Bearer $GITHUB_ADMIN_TOKEN"
```

The ordinary public `GET /company` route now reflects the last synced features,
repository path and sync status, while retaining its existing response shape.
Frontend work is not needed for backend testing but **will be needed** for a
browser-based repository picker. Until frontend auth is implemented, do not pass
`GITHUB_INTEGRATION_ADMIN_TOKEN` into Next.js/React public variables.

## 5. Automatic updates from GitHub

A signed `push` webhook to the **selected repository's default branch** marks
that revision `pending` in PostgreSQL, while duplicate deliveries and unrelated
repos/branches are ignored. The in-process worker picks up pending changes at
startup and on each polling interval, reads the chosen file at the recorded
commit, and validates it before publishing the new profile. Webhook response
returns promptly; the sync may take up to a polling interval. Polling works only
while the backend is running.

`syncStatus` values:

- `not_synced` — repo selected, no sync yet.
- `pending` — pushed changes queued for validation; last good baseline remains.
- `synced` — selected file successfully fetched, validated and saved.
- `failed` — last attempt failed; prior good baseline still used, or static
  fallback if none existed. `lastError` gives a safe, bounded error message.

`fileSha`, `filePath` and `lastSyncedAt` in `/github/status` describe the
successful source version. `lastCommitSha` identifies the latest **received
push event**, not necessarily the commit the baseline was generated from.

Each semantic baseline change marks the overall SWOT as stale, preventing old
comparison results from being presented as current. To recalculate immediately,
use `/github/sync?recompare=true` or opt in to
`GITHUB_RECOMPARE_ON_SYNC=true`. Recomparison needs the existing LLM provider
configuration and may cost money; the backend does **not** invent new scores.

## 6. Tests

```bash
# From repository root, after installing backend dependencies
cd backend
python -m pytest tests/test_github_integration.py \
  tests/test_github_db.py tests/test_github_feature_sync.py -q
```

These test the HTTP endpoints, validation, database state, webhook signatures,
deduplication, baseline fallback, race guards and revision invalidation using
mock GitHub responses. They do **not** validate a real GitHub installation or
production Railway secrets. For production, install the App, copy a real feature
manifest to an authorized repo, and perform a manual sync + push smoke test.
