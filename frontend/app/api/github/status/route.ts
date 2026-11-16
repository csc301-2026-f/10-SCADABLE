import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";

export const dynamic = "force-dynamic";

function emptyStatus(message: string) {
  return {
    installationId: null,
    repositoryId: null,
    fullName: null,
    defaultBranch: null,
    filePath: null,
    syncStatus: null,
    lastSyncedAt: null,
    lastError: null,
    revision: null,
    configured: false,
    message,
  };
}

function mapStatus(data: Record<string, unknown> | null) {
  if (!data) {
    return emptyStatus("No GitHub repository selected yet.");
  }
  return {
    installationId:
      typeof data.installationId === "number"
        ? data.installationId
        : typeof data.installation_id === "number"
          ? data.installation_id
          : null,
    repositoryId:
      typeof data.repositoryId === "number"
        ? data.repositoryId
        : typeof data.repository_id === "number"
          ? data.repository_id
          : null,
    fullName:
      typeof data.fullName === "string"
        ? data.fullName
        : typeof data.full_name === "string"
          ? data.full_name
          : null,
    defaultBranch:
      typeof data.defaultBranch === "string"
        ? data.defaultBranch
        : typeof data.default_branch === "string"
          ? data.default_branch
          : null,
    filePath:
      typeof data.filePath === "string"
        ? data.filePath
        : typeof data.file_path === "string"
          ? data.file_path
          : null,
    syncStatus:
      typeof data.syncStatus === "string"
        ? data.syncStatus
        : typeof data.sync_status === "string"
          ? data.sync_status
          : null,
    lastSyncedAt:
      typeof data.lastSyncedAt === "string"
        ? data.lastSyncedAt
        : typeof data.last_synced_at === "string"
          ? data.last_synced_at
          : null,
    lastError:
      typeof data.lastError === "string"
        ? data.lastError
        : typeof data.last_error === "string"
          ? data.last_error
          : null,
    revision: typeof data.revision === "number" ? data.revision : null,
    configured: true,
  };
}

export async function GET() {
  const token = process.env.GITHUB_INTEGRATION_ADMIN_TOKEN?.trim();
  if (!token) {
    return NextResponse.json(
      emptyStatus(
        "GitHub management is not configured. Set GITHUB_INTEGRATION_ADMIN_TOKEN on the Next.js server.",
      ),
    );
  }

  let response: Response;
  try {
    response = await backendFetch("/github/status", {
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json(emptyStatus(message));
  }

  if (response.status === 404 || response.status === 204) {
    return NextResponse.json(emptyStatus("No GitHub repository selected yet."));
  }

  const text = await response.text();
  if (!response.ok) {
    return new NextResponse(text || "Failed to load GitHub status", {
      status: response.status,
    });
  }

  if (!text.trim() || text.trim() === "null") {
    return NextResponse.json(emptyStatus("No GitHub repository selected yet."));
  }

  return NextResponse.json(mapStatus(JSON.parse(text) as Record<string, unknown>));
}
