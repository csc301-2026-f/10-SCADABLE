import { SignIn } from "@clerk/nextjs";
import { isClerkConfigured } from "@/lib/auth/config";
import styles from "../../auth.module.css";

export default function SignInPage() {
  const clerkEnabled = isClerkConfigured();

  return (
    <div className={styles.authPage}>
      <div className={styles.copy}>
        <p className={styles.eyebrow}>Live SWOT</p>
        <h1>Sign in</h1>
        <p className={styles.sub}>
          {clerkEnabled
            ? "Access is invite-only for SCADABLE and the course team. Use the invitation email if you do not have an account yet."
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
