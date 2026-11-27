import type { ScoreSeries } from "@/lib/api/types";

/** Only plot scores when both sides have a valid rating; zero is a valid score. */
export function matchedScoreRows(rows: ScoreSeries[]): ScoreSeries[] {
  const valid = (score: number | null): boolean =>
    typeof score === "number" && Number.isFinite(score) && score >= 0 && score <= 10;
  return rows.filter((row) => valid(row.competitor) && valid(row.scadable));
}
