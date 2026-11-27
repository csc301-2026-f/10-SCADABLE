# Auth access (invite-only)

Live SWOT uses Clerk with **restricted sign-up**. Random people cannot create accounts. Access is by **invitation** (and optionally an allowlist in the Clerk dashboard).

## Current Clerk settings

We run the Clerk **Development** instance (not Production).

- Clerk application name: **LIVE SWOT**
- Instance: **Development** (`pk_test_` / `sk_test_` on Vercel and in `.env.local`)
- `sign_up_mode`: **restricted** (invite-only)
- Public self-serve sign-up: **off**
- App opens to **`/sign-in` only** — bare `/sign-up` redirects away
- Invite acceptance UI only loads when the email link includes a Clerk ticket

Production Clerk needs a custom domain (or a special proxy setup). We stay on Development so localhost and our free Vercel URL work without that.

## How teammates / SCADABLE get access

### Option A — Clerk Dashboard (easiest)

1. Open [dashboard.clerk.com](https://dashboard.clerk.com/) → **LIVE SWOT** → make sure the instance switcher is on **Development**
2. **Users** → **Invitations**
3. Invite emails (team + SCADABLE)
4. They click the email link → finish account on `/sign-up` → then use **Sign in**

### Option B — Clerk CLI

From `frontend/`:

```bash
npx -y clerk@latest api /invitations --file scripts/invite-body.json --yes
```

Edit `scripts/invite-body.json` first:

```json
{
  "email_address": "person@example.com",
  "redirect_url": "http://localhost:3000/sign-up",
  "notify": true,
  "ignore_existing": true
}
```

For the hosted app, set `redirect_url` to your Vercel URL, e.g. `https://your-app.vercel.app/sign-up`.

### Option C — Create a user directly (no email invite)

```bash
npx -y clerk@latest users create --email person@example.com --password <temp-password> --yes
```

They can then sign in on `/sign-in`.

## App entry points

| Route | Role |
| --- | --- |
| `/sign-in` | Primary entry for invited users |
| `/sign-up` | Used when accepting an **invitation** link |

## Dev dashboard bypass (TA evaluation)

**Go to dashboard** on the sign-in page skips Clerk so TAs can mark without an invite.

| Environment | How to enable |
| --- | --- |
| Local `next dev` | On automatically |
| Hosted (Vercel) | Set `ALLOW_DEV_DASHBOARD_BYPASS=true` |
| Partner handoff | Leave unset or `false` so the button stays hidden |

Bypass sessions are **read-only**: mutation controls (add/delete competitor, rerun/cancel, settings, GitHub sync) stay visible so TAs can see them, but they are disabled until a real Clerk sign-in.

This is not real authentication — prefer Clerk invites for normal users.

## Who to invite (fill in)

| Person | Email | Status |
| --- | --- | --- |
| Clerk owner | scadable.swot@gmail.com | invite as needed |
| FE partner | | |
| SCADABLE (Ali / team) | | |
| Other teammates | | |
