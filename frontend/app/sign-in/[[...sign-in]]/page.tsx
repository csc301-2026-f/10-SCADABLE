import { SignIn } from "@clerk/nextjs";
import { DevDashboardBypass } from "@/components/auth/DevDashboardBypass";
import { isDevDashboardBypassAllowed } from "@/lib/auth/bypass";
import { isClerkConfigured } from "@/lib/auth/config";
import styles from "../../auth.module.css";

export default function SignInPage() {
  const clerkEnabled = isClerkConfigured();
  const bypassAllowed = isDevDashboardBypassAllowed();

  return (
    <div className={styles.authPage}>
      <DevDashboardBypass />
      <div className={styles.copy}>
        <p className={styles.eyebrow}>Live SWOT</p>
        <h1>Sign in</h1>
        <p className={styles.sub}>
          {clerkEnabled
            ? "Access is invite-only for SCADABLE and the course team. Use the invitation email if you do not have an account yet."
            : bypassAllowed
              ? "Clerk keys are not configured. Use “Go to dashboard” (top right) to explore without signing in."
              : "Clerk keys are not configured. Add development keys to .env.local to sign in."}
        </p>
      </div>
      {clerkEnabled ? (
        <SignIn
          forceRedirectUrl="/overview"
          // No public sign-up entry from this page — invites arrive by email only
          appearance={{
            elements: {
              footerAction: { display: "none" },
            },
          }}
        />
      ) : (
        <div className={styles.fallbackCard}>
          <p>
            No <code>.env.local</code> Clerk keys found. Auth is skipped for local
            UI testing.
          </p>
        </div>
      )}
    </div>
  );
}
