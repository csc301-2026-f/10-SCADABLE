import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { requireSettingsAdmin } from "@/lib/server/requireAdmin";

export const dynamic = "force-dynamic";

export async function POST() {
  const denied = await requireSettingsAdmin();
  if (denied) return denied;

  const token = process.env.GITHUB_INTEGRATION_ADMIN_TOKEN?.trim();
  if (!token) {
    return NextResponse.json(
      {
        detail:
          "GitHub sync is not configured. Set GITHUB_INTEGRATION_ADMIN_TOKEN on the Next.js server.",
      },
      { status: 503 },
    );
  }

  let response: Response;
  try {
    response = await backendFetch("/github/sync", {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Backend URL not configured.";
    return NextResponse.json({ detail: message }, { status: 503 });
  }

  const text = await response.text();
  if (!response.ok) {
    return new NextResponse(text || "GitHub sync failed", {
      status: response.status,
    });
  }

  // Sync may return status payload or empty — refresh via status endpoint shape.
  let payload: Record<string, unknown> | null = null;
  if (text.trim()) {
    try {
      payload = JSON.parse(text) as Record<string, unknown>;
    } catch {
      payload = null;
    }
  }

  if (!payload) {
    try {
      const statusResponse = await backendFetch("/github/status", {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
      });
      const statusText = await statusResponse.text();
      if (
        statusResponse.ok &&
        statusText.trim() &&
        statusText.trim() !== "null"
      ) {
        payload = JSON.parse(statusText) as Record<string, unknown>;
      }
    } catch {
      payload = null;
    }
  }

  return NextResponse.json({
    installationId: payload?.installationId ?? payload?.installation_id ?? null,
    repositoryId: payload?.repositoryId ?? payload?.repository_id ?? null,
    fullName: payload?.fullName ?? payload?.full_name ?? null,
    defaultBranch: payload?.defaultBranch ?? payload?.default_branch ?? null,
    filePath: payload?.filePath ?? payload?.file_path ?? null,
    syncStatus: payload?.syncStatus ?? payload?.sync_status ?? "synced",
    lastSyncedAt: payload?.lastSyncedAt ?? payload?.last_synced_at ?? null,
    lastError: payload?.lastError ?? payload?.last_error ?? null,
    revision: payload?.revision ?? null,
    configured: true,
    message: "Sync requested.",
  });
}
