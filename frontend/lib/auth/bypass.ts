export const DEV_DASHBOARD_BYPASS_COOKIE = "live_swot_dev_dashboard";

/**
 * TA / course evaluation bypass (not real auth).
 * - Local `next dev`: allowed without the env flag.
 * - Hosted (Vercel): set ALLOW_DEV_DASHBOARD_BYPASS=true for TAs; leave unset/false for partner handoff.
 * Bypass sessions are read-only: no delete/create/rerun/settings mutations.
 */
export function isDevDashboardBypassAllowed(): boolean {
  return (
    process.env.NODE_ENV === "development" ||
    process.env.ALLOW_DEV_DASHBOARD_BYPASS === "true"
  );
}

/** Client-side: true when the TA bypass cookie is present. */
export function hasDevDashboardBypassCookie(): boolean {
  if (typeof document === "undefined") return false;
  return document.cookie
    .split(";")
    .some((part) => part.trim() === `${DEV_DASHBOARD_BYPASS_COOKIE}=1`);
}

/** Drop the bypass cookie after a real Clerk sign-in. */
export function clearDevDashboardBypassCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${DEV_DASHBOARD_BYPASS_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}
