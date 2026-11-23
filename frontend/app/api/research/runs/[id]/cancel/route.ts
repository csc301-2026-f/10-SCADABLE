import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ id: string }> };

/** Cancel a queued or running research run. Signed in users only, like rerun. */
export async function POST(_request: Request, { params }: Params) {
  const denied = await requireClerkSignIn();
  if (denied) return denied;

  const { id } = await params;
  if (!id?.trim()) {
    return NextResponse.json({ detail: "Missing run id." }, { status: 422 });
  }

  try {
    const response = await backendFetch(
      `/runs/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
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
