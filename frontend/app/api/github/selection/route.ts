import { NextResponse } from "next/server";
import {
  getGitHubAdminToken,
  githubAdminFetch,
  mapSelectedRepository,
  missingGitHubTokenResponse,
  readBackendError,
} from "@/lib/server/githubAdmin";
import { requireSettingsAdmin } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const denied = await requireSettingsAdmin();
  if (denied) return denied;

  if (!getGitHubAdminToken()) return missingGitHubTokenResponse();

  let body: {
    installationId?: number;
    repositoryId?: number;
    filePath?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const installationId = Number(body.installationId);
  const repositoryId = Number(body.repositoryId);
  if (
    !Number.isInteger(installationId) ||
    installationId <= 0 ||
    !Number.isInteger(repositoryId) ||
    repositoryId <= 0
  ) {
    return NextResponse.json(
      { detail: "installationId and repositoryId are required." },
      { status: 422 },
    );
  }

  try {
    const selectResponse = await githubAdminFetch("/github/selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installationId,
        repositoryId,
      }),
    });
    if (!selectResponse.ok) {
      return NextResponse.json(
        { detail: await readBackendError(selectResponse) },
        { status: selectResponse.status },
      );
    }

    let selected = mapSelectedRepository(
      (await selectResponse.json()) as Record<string, unknown>,
    );

    const filePath = body.filePath?.trim();
    if (filePath && filePath !== selected.filePath) {
      const fileResponse = await githubAdminFetch("/github/file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath }),
      });
      if (!fileResponse.ok) {
        return NextResponse.json(
          { detail: await readBackendError(fileResponse) },
          { status: fileResponse.status },
        );
      }
      selected = mapSelectedRepository(
        (await fileResponse.json()) as Record<string, unknown>,
      );
    }

    return NextResponse.json(selected);
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }
}
