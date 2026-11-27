import { SignUp } from "@clerk/nextjs";
import { redirect } from "next/navigation";
import { DevDashboardBypass } from "@/components/auth/DevDashboardBypass";
import { hasClerkInviteTicket } from "@/lib/auth/invite";
import { isClerkConfigured } from "@/lib/auth/config";
import styles from "../../auth.module.css";

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function SignUpPage({ searchParams }: Props) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string") query.set(key, value);
    else if (Array.isArray(value) && value[0]) query.set(key, value[0]);
  }

  // Bare /sign-up has no UI — invitation email links include a ticket
  if (!hasClerkInviteTicket(query)) {
    redirect("/sign-in");
  }

  const clerkEnabled = isClerkConfigured();

  return (
    <div className={styles.authPage}>
      <DevDashboardBypass />
      <div className={styles.copy}>
        <p className={styles.eyebrow}>Live SWOT</p>
        <h1>Accept your invite</h1>
        <p className={styles.sub}>
          Finish creating your account to access Live SWOT.
        </p>
      </div>
      {clerkEnabled ? (
        <SignUp forceRedirectUrl="/overview" signInUrl="/sign-in" />
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
