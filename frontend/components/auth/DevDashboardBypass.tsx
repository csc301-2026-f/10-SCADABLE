import Link from "next/link";
import { isDevDashboardBypassAllowed } from "@/lib/auth/bypass";
import styles from "./DevDashboardBypass.module.css";

/**
 * Shown only when bypass is allowed (local next dev, or ALLOW_DEV_DASHBOARD_BYPASS=true).
 * Partner handoff: leave the env unset/false on Vercel so this stays hidden.
 */
export function DevDashboardBypass() {
  if (!isDevDashboardBypassAllowed()) {
    return null;
  }

  return (
    <div className={styles.bar}>
      <p className={styles.note}>Dev only</p>
      <Link href="/api/dev/dashboard-bypass" className={styles.button}>
        Go to dashboard
      </Link>
    </div>
  );
}
