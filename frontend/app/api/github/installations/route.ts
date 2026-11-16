import { NextResponse } from "next/server";
import {
  getGitHubAdminToken,
  githubAdminFetch,
  missingGitHubTokenResponse,
  readBackendError,
} from "@/lib/server/githubAdmin";
import { requireSettingsAdmin } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

export async function GET() {
  const denied = await requireSettingsAdmin();
  if (denied) return denied;

  if (!getGitHubAdminToken()) return missingGitHubTokenResponse();

  try {
    const response = await githubAdminFetch("/github/installations");
    if (!response.ok) {
      return NextResponse.json(
        { detail: await readBackendError(response) },
        { status: response.status },
      );
    }
    const data = (await response.json()) as Array<Record<string, unknown>>;
    const installations = data.map((item) => ({
      id: typeof item.id === "number" ? item.id : Number(item.id),
      account: typeof item.account === "string" ? item.account : "unknown",
      accountType:
        typeof item.account_type === "string"
          ? item.account_type
          : typeof item.accountType === "string"
            ? item.accountType
            : "",
    }));
    return NextResponse.json({ installations });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }
}
