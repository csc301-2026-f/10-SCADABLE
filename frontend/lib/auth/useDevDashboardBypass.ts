"use client";

import { useAuth } from "@clerk/nextjs";

/**
 * Read-only session: Clerk is configured but the user is not signed in
 * (TA dashboard bypass, or signed-out). Mutations stay visible but disabled.
 */
export function useDevDashboardBypass(): boolean {
  const { isSignedIn, isLoaded } = useAuth();
  const clerkEnabled = Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim(),
  );

  if (!clerkEnabled) {
    return false;
  }
  // Avoid a flash of enabled controls before Clerk loads
  if (!isLoaded) {
    return true;
  }
  return !isSignedIn;
}
