import type { AgentRun } from "@/lib/api/types";

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

export type WeeklyRunStats = {
  runsThisWeek: number;
  successful: number;
  /** Sum of known run costs this week; null if no run has costUsd yet. */
  estimatedCostUsd: number | null;
  cacheHitRatePct: number;
};

/** Runs started in the last 7 days (weekly totals for the summary cards). */
export function filterRunsThisWeek(
  runs: AgentRun[],
  now = Date.now(),
): AgentRun[] {
  const cutoff = now - WEEK_MS;
  return runs.filter((run) => new Date(run.startedAt).getTime() >= cutoff);
}

/**
 * One row per competitor: their most recent run.
 * Used for the Agent Runs table (not the full weekly history).
 */
function sortRunsNewestFirst(runs: AgentRun[]): AgentRun[] {
  return [...runs].sort(
    (a, b) =>
      new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime(),
  );
}

/** Completed runs only (success / failed) — pending/running are still in progress. */
export function filterCompletedRuns(runs: AgentRun[]): AgentRun[] {
  return runs.filter((r) => r.status === "success" || r.status === "failed");
}

export function latestRunPerCompetitor(runs: AgentRun[]): AgentRun[] {
  const sorted = sortRunsNewestFirst(filterCompletedRuns(runs));

  const latest = new Map<string, AgentRun>();
  for (const run of sorted) {
    const key = run.competitorId ?? run.competitorName;
    if (!latest.has(key)) {
      latest.set(key, run);
    }
  }

  return [...latest.values()];
}

/**
 * Latest completed run for a competitor, only if it succeeded.
 * Pending profiles / failed latest runs get no profile → runs link.
 */
export function latestSuccessfulRunForCompetitor(
  runs: AgentRun[],
  competitorId: string,
): AgentRun | null {
  const latestCompleted =
    sortRunsNewestFirst(filterCompletedRuns(runs)).find(
      (run) => run.competitorId === competitorId,
    ) ?? null;

  if (!latestCompleted || latestCompleted.status !== "success") {
    return null;
  }
  return latestCompleted;
}

export function computeWeeklyRunStats(runs: AgentRun[]): WeeklyRunStats {
  const week = filterRunsThisWeek(runs);
  const successful = week.filter((r) => r.status === "success").length;
  const priced = week.filter((r) => r.costUsd != null);
  const estimatedCostUsd =
    priced.length === 0
      ? null
      : priced.reduce((sum, r) => sum + (r.costUsd as number), 0);
  const hits = week.reduce((sum, r) => sum + (r.cacheHits ?? 0), 0);
  const misses = week.reduce((sum, r) => sum + (r.cacheMisses ?? 0), 0);
  const total = hits + misses;

  return {
    runsThisWeek: week.length,
    successful,
    estimatedCostUsd,
    cacheHitRatePct: total === 0 ? 0 : Math.round((hits / total) * 100),
  };
}

export function formatRunStatus(status: AgentRun["status"]): string {
  if (status === "success") return "Success";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Pending";
}

/** Format API `costUsd` for the runs UI. Null means not available yet (Langfuse lag). */
export function formatRunCost(costUsd: number | null): string {
  if (costUsd == null) return "—";
  if (costUsd > 0 && costUsd < 0.01) return `$${costUsd.toFixed(4)}`;
  return `$${costUsd.toFixed(2)}`;
}

export function formatCacheStat(
  hits: number | null,
  misses: number | null,
): { hitsLabel: string; missesLabel: string } | null {
  if (hits == null || misses == null) return null;
  const hitsLabel = hits === 1 ? "1 hit" : `${hits} hits`;
  const missesLabel = misses === 1 ? "1 miss" : `${misses} misses`;
  return { hitsLabel, missesLabel };
}
