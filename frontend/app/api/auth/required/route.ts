import { NextResponse } from "next/server";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

/** Lightweight Clerk session probe for client mock mutations. */
export async function GET() {
  const denied = await requireClerkSignIn();
  if (denied) return denied;
  return NextResponse.json({ ok: true });
}
