"use client";

import { hasDevDashboardBypassCookie } from "@/lib/auth/bypass";

/**
 * Read-only session: Clerk is configured and the TA dashboard bypass cookie
 * is set (unsigned evaluation). Prefer the cookie over useAuth so component
 * tests can render without <ClerkProvider />.
 *
 * Clear the cookie after a real Clerk sign-in so signed-in users are not
 * stuck in read-only.
 */
export function useDevDashboardBypass(): boolean {
  const clerkEnabled = Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim(),
  );
  if (!clerkEnabled) {
    return false;
  }
  return hasDevDashboardBypassCookie();
}
