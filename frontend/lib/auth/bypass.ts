export const DEV_DASHBOARD_BYPASS_COOKIE = "live_swot_dev_dashboard";

/**
 * TA / course evaluation bypass (not real auth).
 * - Local `next dev`: allowed without the env flag.
 * - Hosted (Vercel): set ALLOW_DEV_DASHBOARD_BYPASS=true for TAs; leave unset/false for partner handoff.
 */
export function isDevDashboardBypassAllowed(): boolean {
  return (
    process.env.NODE_ENV === "development" ||
    process.env.ALLOW_DEV_DASHBOARD_BYPASS === "true"
  );
}
