import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { requireClerkSignIn } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

/** Delete competitor — Clerk sign-in required (dashboard bypass is read-only). */
export async function DELETE(_request: Request, context: Ctx) {
  const denied = await requireClerkSignIn();
  if (denied) return denied;

  const { id } = await context.params;
  if (!id?.trim()) {
    return NextResponse.json({ detail: "Missing competitor id." }, { status: 400 });
  }

  try {
    const response = await backendFetch(
      `/competitors/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
    if (response.status === 204 || response.ok) {
      return new NextResponse(null, { status: 204 });
    }
    const text = await response.text();
    return new NextResponse(text || "Failed to delete competitor", {
      status: response.status,
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }
}
