import { auth, clerkClient } from "@clerk/nextjs/server";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  DEV_DASHBOARD_BYPASS_COOKIE,
  isDevDashboardBypassAllowed,
} from "@/lib/auth/bypass";

/** Real Clerk session only — dashboard bypass does not count. */
export async function requireClerkSignIn(): Promise<NextResponse | null> {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim()) {
    return NextResponse.json({ detail: "Sign-in is not configured." }, { status: 503 });
  }
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }
  return null;
}

/** Signed-in Clerk user, or local dashboard bypass. */
export async function requireSignedIn(): Promise<NextResponse | null> {
  if (isDevDashboardBypassAllowed()) {
    const jar = await cookies();
    if (jar.get(DEV_DASHBOARD_BYPASS_COOKIE)?.value === "1") {
      return null;
    }
  }
  return requireClerkSignIn();
}

/**
 * Gate mutating settings / GitHub admin gateways.
 * Requires a signed-in Clerk user (or local dashboard bypass).
 * If SETTINGS_ADMIN_EMAILS is set (comma-separated), the user's primary/any
 * verified email must be on that list; otherwise any signed-in user is allowed
 * (invite-only app).
 */
export async function requireSettingsAdmin(): Promise<NextResponse | null> {
  const denied = await requireSignedIn();
  if (denied) return denied;

  const allowlist = (process.env.SETTINGS_ADMIN_EMAILS ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);

  if (allowlist.length === 0) {
    return null;
  }

  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }

  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  const emails = user.emailAddresses.map((e) => e.emailAddress.toLowerCase());
  if (!emails.some((email) => allowlist.includes(email))) {
    return NextResponse.json(
      { detail: "Admin access required to change this setting." },
      { status: 403 },
    );
  }

  return null;
}
