
import type { CompetitorProfile, CompetitorSummary, AgentRun } from "@/lib/api/types";

const POLL_MS = 3000;
const SCORE_WINDOW_MS = 10 * 60 * 1000;
const COST_WINDOW_MS = 20 * 60 * 1000;

function withinRecentWindow(timestamp: string | null, windowMs: number): boolean {
  if (!timestamp) return false;
  const elapsed = Date.now() - new Date(timestamp).getTime();
  return Number.isFinite(elapsed) && elapsed >= 0 && elapsed < windowMs;
}

function isInProgress(status: string | undefined): boolean {
  return status === "pending" || status === "running";
}

/** Poll while any competitor research is still pending/running. */
export function competitorsPollInterval(
  competitors: CompetitorSummary[] | undefined,
): number | false {
  if (!competitors?.some((c) => isInProgress(c.researchStatus))) return false;
  return POLL_MS;
}

/** Poll research, plus a short window for scores produced after the run completes. */
export function profilePollInterval(
  profile: CompetitorProfile | undefined,
): number | false {
  if (!profile) return false;
  if (isInProgress(profile.researchStatus)) return POLL_MS;

  // Comparison scores may arrive after completion, even if older scores are
  // already displayed. Keep polling for a bounded period after success.
  if (profile.researchStatus === "success" && withinRecentWindow(profile.lastResearchedAt, SCORE_WINDOW_MS)) {
    return 10_000;
  }
  return false;
}

/** Poll active runs, then allow delayed cost ingestion without polling forever. */
export function runsPollInterval(runs: AgentRun[] | undefined): number | false {
  if (runs?.some((r) => isInProgress(r.status))) return POLL_MS;
  if (runs?.some((r) => r.costUsd == null && withinRecentWindow(r.finishedAt, COST_WINDOW_MS))) {
    return 30_000;
  }
  return false;
}
