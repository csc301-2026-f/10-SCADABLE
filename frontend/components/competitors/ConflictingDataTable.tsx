import type { ConflictClaimSide, DataConflict } from "@/lib/api";
import styles from "./CompetitorProfile.module.css";

type Props = {
  conflicts: DataConflict[];
  hasCompletedResearch: boolean;
};

function ClaimText({ side }: { side: ConflictClaimSide }) {
  const source = side.sourceUrl ? (
    <a href={side.sourceUrl} target="_blank" rel="noreferrer">
      {side.sourceLabel}
    </a>
  ) : (
    side.sourceLabel
  );

  return (
    <>
      {side.text} <span className={styles.conflictSource}>({source})</span>
    </>
  );
}

function ConflictsWithCell({ sides }: { sides: ConflictClaimSide[] }) {
  if (sides.length === 0) return null;

  if (sides.length === 1) {
    return <ClaimText side={sides[0]} />;
  }

  return (
    <ul className={styles.conflictList}>
      {sides.map((side, index) => (
        <li key={`${side.sourceLabel}-${index}`}>
          <ClaimText side={side} />
        </li>
      ))}
    </ul>
  );
}

/** Show an explicit empty state when research found no conflicting claims. */
export function ConflictingDataTable({ conflicts, hasCompletedResearch }: Props) {
  return (
    <section className={styles.card} aria-labelledby="conflicting-data-heading">
      <h2 id="conflicting-data-heading">Conflicting data</h2>
      {conflicts.length === 0 ? (
        <p className={styles.empty}>
          {hasCompletedResearch
            ? "No conflicting claims identified in the latest research."
            : "Conflicting claims will appear after research completes."}
        </p>
      ) : (
        <div className={styles.conflictTableWrap}>
          <table className={styles.conflictTable}>
            <thead>
              <tr>
                <th scope="col">Claim</th>
                <th scope="col">Conflicts with</th>
              </tr>
            </thead>
            <tbody>
              {conflicts.map((row) => (
                <tr key={row.id}>
                  <td>
                    <ClaimText side={row.claim} />
                  </td>
                  <td>
                    <ConflictsWithCell sides={row.conflictsWith} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
