import { NextResponse } from "next/server";
import { accountResearchHeaders } from "@/lib/server/accountResearch";
import { backendFetch } from "@/lib/server/backend";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

/** Create competitor + queue research, forwarding Clerk OpenAI key when present. */
export async function POST(request: Request) {
  const denied = await requireClerkSignIn();
  if (denied) return denied;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  try {
    const headers = await accountResearchHeaders();
    const response = await backendFetch("/competitors", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const text = await response.text();
    return new NextResponse(text || null, {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }
}
