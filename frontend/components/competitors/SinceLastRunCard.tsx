import type { CompetitorChanges, Finding, ScoreChange, SwotCategory } from "@/lib/api";
import { formatRelativePast } from "@/lib/format";
import styles from "./CompetitorProfile.module.css";

type Props = {
  changes: CompetitorChanges;
};

const CATEGORY_LABEL: Record<SwotCategory, string> = {
  strength: "Strength",
  weakness: "Weakness",
  opportunity: "Opportunity",
  threat: "Threat",
};

function formatScore(score: number | null): string {
  return score === null ? "—" : `${score}`;
}

/** Same colours as the trend label: competitor getting stronger is red, weaker is green. */
function scoreClass(change: ScoreChange): string {
  if (change.previous === null || change.current === null) return styles.trendFlat;
  return change.current > change.previous ? styles.trendUp : styles.trendDown;
}

function FindingRow({ finding, sign }: { finding: Finding; sign: "+" | "−" }) {
  return (
    <div className={styles.newsItem}>
      <span className={sign === "+" ? styles.trendUp : styles.trendDown}>{sign}</span>{" "}
      {finding.text}
      <span className={styles.newsDate}>
        {CATEGORY_LABEL[finding.category]}
        {finding.sourceUrl ? (
          <>
            {" · "}
            <a href={finding.sourceUrl} target="_blank" rel="noreferrer">
              {finding.sourceLabel}
            </a>
          </>
        ) : null}
      </span>
    </div>
  );
}

export function SinceLastRunCard({ changes }: Props) {
  return (
    <section className={styles.card}>
      <h2>
        Since last run
        <span className={styles.cardSubtitle}>
          previous run {formatRelativePast(changes.previousAt).toLowerCase()}
        </span>
      </h2>

      {!changes.hasChanges ? (
        <p className={styles.empty}>
          {changes.websiteChanged
            ? "No changes to findings or scores since the last run."
            : "Website unchanged since the last run."}
        </p>
      ) : (
        <>
          {changes.scoreChanges.map((change) => (
            <div key={change.label} className={styles.featureRow}>
              <span>{change.label}</span>
              <span className={`${styles.score} ${scoreClass(change)}`}>
                {formatScore(change.previous)} → {formatScore(change.current)}
              </span>
            </div>
          ))}
          {changes.addedFindings.map((finding) => (
            <FindingRow key={`added-${finding.id}`} finding={finding} sign="+" />
          ))}
          {changes.removedFindings.map((finding) => (
            <FindingRow key={`removed-${finding.id}`} finding={finding} sign="−" />
          ))}
        </>
      )}
    </section>
  );
}
