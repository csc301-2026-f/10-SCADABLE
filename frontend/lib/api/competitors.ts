import { apiFetch, shouldUseMocks } from "@/lib/api/client";
import type {
  AgentRun,
  CompetitorChanges,
  CompetitorProfile,
  CompetitorSummary,
  CreateCompetitorInput,
  CreateCompetitorResult,
} from "@/lib/api/types";
import { normalizeSourceUrl } from "@/lib/format";
import { mockCompetitors, mockProfiles, mockRuns } from "@/lib/mocks/data";

function delay(ms = 200) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function findCompetitorIndex(id: string): number {
  return mockCompetitors.findIndex((c) => c.id === id);
}

function patchCompetitorSummary(
  id: string,
  patch: Partial<CompetitorSummary>,
): CompetitorSummary {
  const index = findCompetitorIndex(id);
  if (index === -1) {
    throw new Error(`Competitor not found: ${id}`);
  }
  mockCompetitors[index] = { ...mockCompetitors[index], ...patch };
  if (mockProfiles[id]) {
    mockProfiles[id] = { ...mockProfiles[id], ...patch };
  }
  return mockCompetitors[index];
}

/** Demo profile payload after a successful research run. */
function buildResearchedProfile(summary: CompetitorSummary): CompetitorProfile {
  const now = new Date().toISOString();

  return {
    ...summary,
    researchStatus: "success",
    lastResearchedAt: now,
    nextRefreshAt: new Date(Date.now() + 4 * 24 * 60 * 60 * 1000).toISOString(),
    openRoles: 2,
    recentChangeSummary: "Initial research completed",
    summary:
      "Research finished successfully. The competitor profile has been populated from the queued agent run across the provided data sources.",
    findings: [
      {
        id: `${summary.id}-f1`,
        competitorId: summary.id,
        competitorName: summary.name,
        category: "opportunity",
        text: "Public site and source pages are now indexed for ongoing monitoring",
        sourceUrl: summary.website,
        sourceLabel: "Primary site",
        detectedAt: now,
        isNew: true,
      },
    ],
    recentActivity: [
      {
        id: `${summary.id}-a1`,
        text: "Initial research run completed and profile populated",
        detectedAt: now,
      },
    ],
    chartSeries: {
      scores: [
        { label: "Data collection", competitor: 6, scadable: 7 },
        { label: "Workflow execution", competitor: 5, scadable: 8 },
        { label: "Analytics", competitor: 6, scadable: 6 },
        { label: "AI recommendations", competitor: 5, scadable: 7 },
      ],
      activityOverTime: [
        { label: "W1", value: 0 },
        { label: "W2", value: 1 },
        { label: "W3", value: 1 },
        { label: "W4", value: 2 },
      ],
      signalsBySource: [
        { label: "website", value: 2 },
        { label: "jobs", value: 1 },
        { label: "reviews", value: 0 },
        { label: "press", value: 0 },
      ],
      hiringTrend: [
        { label: "Jul", value: 0 },
        { label: "Aug", value: 1 },
        { label: "Sep", value: 1 },
        { label: "Oct", value: 2 },
      ],
    },
  };
}

function enqueueSuccessfulRun(summary: CompetitorSummary): AgentRun {
  const startedAt = new Date().toISOString();
  const run: AgentRun = {
    id: `run-${summary.id}-${Date.now()}`,
    competitorId: summary.id,
    competitorName: summary.name,
    status: "success",
    costUsd: 0.04,
    stepCount: 4,
    cacheHits: 2,
    cacheMisses: 2,
    startedAt,
    finishedAt: startedAt,
    pullRequestUrl: null,
    errorMessage: null,
    steps: [
      { name: "Load data sources", result: "Success", status: "success" },
      { name: "Fetch public pages", result: "Cache miss", status: "success" },
      {
        name: "Generate competitor summary",
        result: "Cache miss",
        status: "success",
      },
      {
        name: "Update competitor profile",
        result: "Success",
        status: "success",
      },
    ],
  };
  mockRuns.unshift(run);
  return run;
}

export async function listCompetitors(): Promise<CompetitorSummary[]> {
  if (shouldUseMocks()) {
    await delay();
    // Return a new array so React Query sees a data change after mock mutations.
    return [...mockCompetitors];
  }
  return apiFetch<CompetitorSummary[]>("/competitors");
}

export async function getCompetitor(id: string): Promise<CompetitorProfile> {
  if (shouldUseMocks()) {
    await delay();
    const profile = mockProfiles[id];
    if (!profile) {
      const summary = mockCompetitors.find((c) => c.id === id);
      if (!summary) {
        throw new Error(`Competitor not found: ${id}`);
      }
      return {
        ...summary,
        summary: "Profile not researched yet.",
        findings: [],
        recentActivity: [],
        openRoles: null,
        nextRefreshAt: null,
        chartSeries: {
          scores: [],
          activityOverTime: [],
          signalsBySource: [],
          hiringTrend: [],
        },
      };
    }
    return profile;
  }
  return apiFetch<CompetitorProfile>(`/competitors/${id}`);
}

/** What changed between the competitor's two most recent completed research runs. */
export async function getCompetitorChanges(id: string): Promise<CompetitorChanges> {
  if (shouldUseMocks()) {
    await delay();
    return {
      competitorId: id,
      currentRunId: null,
      previousRunId: null,
      currentAt: null,
      previousAt: null,
      websiteChanged: false,
      hasChanges: false,
      addedFindings: [],
      removedFindings: [],
      scoreChanges: [],
    };
  }
  return apiFetch<CompetitorChanges>(`/competitors/${id}/changes`);
}

export async function createCompetitor(
  input: CreateCompetitorInput,
): Promise<CreateCompetitorResult> {
  if (shouldUseMocks()) {
    await delay();
    const sources = input.dataSources
      .map(normalizeSourceUrl)
      .filter(Boolean);
    if (!sources.length) {
      throw new Error("Add at least one data source");
    }

    const baseId = input.name.toLowerCase().replace(/\s+/g, "-") || "competitor";
    let id = baseId;
    let suffix = 2;
    while (
      mockCompetitors.some((c) => c.id === id) ||
      mockProfiles[id]
    ) {
      id = `${baseId}-${suffix}`;
      suffix += 1;
    }

    const competitor: CompetitorSummary = {
      id,
      name: input.name.trim(),
      website: sources[0],
      trend: "flat",
      researchStatus: "pending",
      lastResearchedAt: null,
      recentChangeSummary: null,
    };
    mockCompetitors.unshift(competitor);
    return {
      competitor,
      queuedRunId: `run-${id}-queued`,
    };
  }
  // Server gateway attaches Clerk privateMetadata OpenAI key + bridge token.
  const response = await fetch("/api/research/competitors", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(input),
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status >= 500) {
      throw new Error("Something went wrong. Please try again.");
    }
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as CreateCompetitorResult;
}

export async function deleteCompetitor(id: string): Promise<void> {
  if (shouldUseMocks()) {
    await delay();
    const index = findCompetitorIndex(id);
    if (index === -1) {
      throw new Error(`Competitor not found: ${id}`);
    }
    mockCompetitors.splice(index, 1);
    delete mockProfiles[id];
    return;
  }
  await apiFetch<void>(`/competitors/${id}`, { method: "DELETE" });
}

/**
 * Re-queue research after a failed run (or manual retry).
 * Mock mode: marks running, waits briefly, then fills the profile as success.
 */
export async function rerunCompetitorResearch(
  id: string,
): Promise<CompetitorProfile> {
  if (shouldUseMocks()) {
    patchCompetitorSummary(id, { researchStatus: "running" });
    await delay(2200);

    const summary = patchCompetitorSummary(id, {
      researchStatus: "success",
      trend: "flat",
      lastResearchedAt: new Date().toISOString(),
      recentChangeSummary: "Research rerun completed",
    });
    const profile = buildResearchedProfile(summary);
    mockProfiles[id] = profile;
    enqueueSuccessfulRun(summary);
    return profile;
  }

  // Server gateway attaches Clerk privateMetadata OpenAI key + bridge token.
  const response = await fetch(
    `/api/research/competitors/${encodeURIComponent(id)}/rerun`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    if (response.status >= 500) {
      throw new Error("Something went wrong. Please try again.");
    }
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as CompetitorProfile;
}
