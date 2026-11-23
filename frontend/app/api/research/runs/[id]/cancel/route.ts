import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ id: string }> };

/**
 * Cancel a queued or running research run. Signed in users only, like rerun.
 * The backend only accepts the call with the bridge token, which never reaches the browser.
 */
export async function POST(_request: Request, { params }: Params) {
  const denied = await requireClerkSignIn();
  if (denied) return denied;

  const bridge = process.env.ACCOUNT_RESEARCH_BRIDGE_TOKEN?.trim();
  if (!bridge) {
    return NextResponse.json(
      {
        detail:
          "Cancelling runs is not configured. Set ACCOUNT_RESEARCH_BRIDGE_TOKEN on the Next.js server.",
      },
      { status: 503 },
    );
  }

  const { id } = await params;
  if (!id?.trim()) {
    return NextResponse.json({ detail: "Missing run id." }, { status: 422 });
  }

  try {
    const response = await backendFetch(
      `/runs/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Scadable-Research-Bridge-Token": bridge,
        },
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
