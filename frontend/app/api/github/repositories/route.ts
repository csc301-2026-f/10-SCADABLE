import { NextResponse } from "next/server";
import {
  getGitHubAdminToken,
  githubAdminFetch,
  missingGitHubTokenResponse,
  readBackendError,
} from "@/lib/server/githubAdmin";
import { requireSettingsAdmin } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const denied = await requireSettingsAdmin();
  if (denied) return denied;

  if (!getGitHubAdminToken()) return missingGitHubTokenResponse();

  const installationId = Number(
    new URL(request.url).searchParams.get("installationId") ?? "",
  );
  if (!Number.isInteger(installationId) || installationId <= 0) {
    return NextResponse.json(
      { detail: "installationId query param is required." },
      { status: 422 },
    );
  }

  try {
    const response = await githubAdminFetch(
      `/github/repositories?installation_id=${installationId}`,
    );
    if (!response.ok) {
      return NextResponse.json(
        { detail: await readBackendError(response) },
        { status: response.status },
      );
    }
    const data = (await response.json()) as Array<Record<string, unknown>>;
    const repositories = data.map((item) => ({
      id: typeof item.id === "number" ? item.id : Number(item.id),
      fullName:
        typeof item.fullName === "string"
          ? item.fullName
          : typeof item.full_name === "string"
            ? item.full_name
            : "unknown",
      defaultBranch:
        typeof item.defaultBranch === "string"
          ? item.defaultBranch
          : typeof item.default_branch === "string"
            ? item.default_branch
            : "main",
      private: Boolean(item.private),
    }));
    return NextResponse.json({ repositories });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }
}
