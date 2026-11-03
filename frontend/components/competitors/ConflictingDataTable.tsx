import type { ConflictClaimSide, DataConflict } from "@/lib/api";
import styles from "./CompetitorProfile.module.css";

type Props = {
  conflicts: DataConflict[];
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

/** Renders only when the parent passes a non-empty conflicts list. */
export function ConflictingDataTable({ conflicts }: Props) {
  if (conflicts.length === 0) return null;

  return (
    <section className={styles.card} aria-labelledby="conflicting-data-heading">
      <h2 id="conflicting-data-heading">Conflicting data</h2>
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
    </section>
  );
}
