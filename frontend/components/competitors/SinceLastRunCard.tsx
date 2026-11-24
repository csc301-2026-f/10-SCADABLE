"use client";

import { useId, useState } from "react";
import type { CompetitorChanges, Finding, ScoreChange, SwotCategory } from "@/lib/api";
import { formatRelativePast } from "@/lib/format";
import styles from "./CompetitorProfile.module.css";

type Props = { changes: CompetitorChanges };

const INITIAL_VISIBLE = 4;
const CATEGORY_LABEL: Record<SwotCategory, string> = {
  strength: "Strength",
  weakness: "Weakness",
  opportunity: "Opportunity",
  threat: "Threat",
};

/** Same colours as the trend label: competitor getting stronger is red, weaker is green. */
function scoreClass(change: ScoreChange): string {
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
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const scoreCount = changes.scoreChanges.length;
  const addedCount = changes.addedFindings.length;
  const removedCount = changes.removedFindings.length;
  const total = scoreCount + addedCount + removedCount;
  const limit = expanded ? total : INITIAL_VISIBLE;
  const visibleScores = changes.scoreChanges.slice(0, limit);
  const visibleAdded = changes.addedFindings.slice(0, Math.max(0, limit - visibleScores.length));
  const visibleRemoved = changes.removedFindings.slice(
    0, Math.max(0, limit - visibleScores.length - visibleAdded.length),
  );

  return (
    <section className={styles.card}>
      <h2>
        Since last run
        {changes.previousRunId ? (
          <span className={styles.cardSubtitle}>
            previous run {formatRelativePast(changes.previousAt).toLowerCase()}
          </span>
        ) : null}
      </h2>

      {!changes.previousRunId ? (
        <p className={styles.empty}>
          {changes.currentRunId
            ? "Run research again to compare findings."
            : "Complete research to start tracking changes."}
        </p>
      ) : !changes.hasChanges ? (
        <p className={styles.empty}>
          {changes.websiteChanged
            ? "No changes to extracted findings or matched feature scores since the last run."
            : "Website unchanged since the last run."}
        </p>
      ) : (
        <>
          <p className={styles.changeSummary}>
            {scoreCount} score {scoreCount === 1 ? "change" : "changes"} · {addedCount} newly extracted · {removedCount} no longer reported
          </p>
          <p className={styles.changeDisclaimer}>
            Research differences do not necessarily mean the competitor changed its product.
          </p>
          <div id={detailsId}>
            {visibleScores.length > 0 ? (
              <>
                <h3 className={styles.changeGroupHeading}>Matched feature score changes</h3>
                {visibleScores.map((change) => (
                  <div key={change.label} className={styles.featureRow}>
                    <span>{change.label}</span>
                    <span className={`${styles.score} ${scoreClass(change)}`}>
                      {change.previous} → {change.current}
                    </span>
                  </div>
                ))}
              </>
            ) : null}
            {visibleAdded.length + visibleRemoved.length > 0 ? (
              <>
                <h3 className={styles.changeGroupHeading}>Extracted SWOT findings</h3>
                {visibleAdded.map((finding) => (
                  <FindingRow key={`added-${finding.id}`} finding={finding} sign="+" />
                ))}
                {visibleRemoved.map((finding) => (
                  <FindingRow key={`removed-${finding.id}`} finding={finding} sign="−" />
                ))}
              </>
            ) : null}
          </div>
          {total > INITIAL_VISIBLE ? (
            <button
              type="button"
              className={styles.changeToggle}
              aria-expanded={expanded}
              aria-controls={detailsId}
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? "Show less" : `Show all changes (${total - INITIAL_VISIBLE} more)`}
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}
