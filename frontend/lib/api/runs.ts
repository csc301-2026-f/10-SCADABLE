import { ApiError, apiFetch, shouldUseMocks } from "./client";
import type { AgentRun } from "@/lib/api/types";
import { mockRuns } from "../mocks/data";

function delay(ms = 200) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function listRuns(): Promise<AgentRun[]> {
  if (shouldUseMocks()) {
    await delay();
    // Copy so React Query sees a stable snapshot after mock mutations.
    return [...mockRuns];
  }
  return apiFetch<AgentRun[]>("/runs");
}

export async function getRun(id: string): Promise<AgentRun> {
  if (shouldUseMocks()) {
    await delay();
    const run = mockRuns.find((r) => r.id === id);
    if (!run) {
      throw new Error(`Run not found: ${id}`);
    }
    return run;
  }
  return apiFetch<AgentRun>(`/runs/${id}`);
}

/**
 * Stop a queued or running run. Earlier results stay, and the cost already
 * spent is still recorded. Goes through the signed in server route.
 */
export async function cancelRun(id: string): Promise<AgentRun> {
  if (shouldUseMocks()) {
    await delay();
    const run = mockRuns.find((r) => r.id === id);
    if (!run) {
      throw new Error(`Run not found: ${id}`);
    }
    run.status = "cancelled";
    run.finishedAt = new Date().toISOString();
    return run;
  }

  const response = await fetch(
    `/api/research/runs/${encodeURIComponent(id)}/cancel`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    if (response.status >= 500) {
      throw new ApiError("Something went wrong. Please try again.", response.status);
    }
    const detail = await response.text().catch(() => "");
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return (await response.json()) as AgentRun;
}
