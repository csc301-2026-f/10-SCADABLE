import { NextResponse } from "next/server";
import { accountResearchHeaders } from "@/lib/server/accountResearch";
import { backendFetch } from "@/lib/server/backend";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ id: string }> };

/** Re-queue research, forwarding Clerk OpenAI key when present. */
export async function POST(_request: Request, { params }: Params) {
  const denied = await requireClerkSignIn();
  if (denied) return denied;

  const { id } = await params;
  if (!id?.trim()) {
    return NextResponse.json({ detail: "Missing competitor id." }, { status: 422 });
  }

  try {
    const headers = await accountResearchHeaders();
    const response = await backendFetch(
      `/competitors/${encodeURIComponent(id)}/rerun`,
      {
        method: "POST",
        headers,
      },
    );
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
