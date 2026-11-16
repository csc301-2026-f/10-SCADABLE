import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";

export function missingGitHubTokenResponse() {
  return NextResponse.json(
    {
      detail:
        "GitHub management is not configured. Set GITHUB_INTEGRATION_ADMIN_TOKEN on the Next.js server.",
    },
    { status: 503 },
  );
}

export function getGitHubAdminToken(): string | null {
  return process.env.GITHUB_INTEGRATION_ADMIN_TOKEN?.trim() || null;
}

export async function githubAdminFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = getGitHubAdminToken();
  if (!token) {
    throw new Error("GITHUB_INTEGRATION_ADMIN_TOKEN is not set");
  }
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  return backendFetch(path, { ...init, headers });
}

export function mapSelectedRepository(data: Record<string, unknown>) {
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

export async function readBackendError(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  if (!text.trim()) return `Request failed (${response.status})`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* use raw text */
  }
  return text;
}
