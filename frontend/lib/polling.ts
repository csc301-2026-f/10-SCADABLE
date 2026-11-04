
import type { CompetitorProfile, CompetitorSummary, AgentRun } from "@/lib/api/types";

const POLL_MS = 3000;

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

  // The overall comparison generates feature scores after competitor research
  // is marked complete. Update the chart automatically for a few minutes,
  // but never poll older, permanently unscored profiles indefinitely.
  if (profile.researchStatus === "success" && profile.chartSeries.scores.length === 0) {
    const completedAt = profile.lastResearchedAt
      ? new Date(profile.lastResearchedAt).getTime()
      : NaN;
    const elapsed = Date.now() - completedAt;
    if (Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 4 * 60 * 1000) {
      return 10_000;
    }
  }
  return false;
}

/** Poll while any agent run is still pending/running. */
export function runsPollInterval(runs: AgentRun[] | undefined): number | false {
  if (!runs?.some((r) => isInProgress(r.status))) return false;
  return POLL_MS;
}



