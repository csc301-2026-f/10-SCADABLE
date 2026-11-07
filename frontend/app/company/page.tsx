"use client";

import { useQuery } from "@tanstack/react-query";
import { getCompanyProfile } from "@/lib/api/company";
import { Button } from "@/components/ui/Button";
// import { Pill } from "@/components/ui/Pill"; // restore with GitHub block
import styles from "./company.module.css";

/*
function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
*/

export default function CompanyPage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["company-profile"],
    queryFn: getCompanyProfile,
  });

  if (isLoading) {
    return <p className={styles.state}>Loading company profile…</p>;
  }

  if (isError || !data) {
    return (
      <div className={styles.state} role="alert">
        <p>
          Couldn’t load the company profile:{" "}
          {(error as Error)?.message ?? "unknown error"}.
        </p>
        <Button variant="secondary" onClick={() => refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const { offerings, positioningNotes /* , github */ } = data;

  return (
    <main className={styles.page}>
      <header>
        <h1 className={styles.title}>Our company</h1>
        <p className={styles.sub}>
          How SCADABLE is described to the research agents.
        </p>
      </header>

      <div className={styles.grid}>
        <section className={styles.card} aria-labelledby="offerings-heading">
          <h2 id="offerings-heading" className={styles.cardTitle}>
            Offerings
          </h2>
          {offerings.length === 0 ? (
            <p className={styles.empty}>No offerings listed yet.</p>
          ) : (
            <ul className={styles.list}>
              {offerings.map((o) => (
                <li key={o} className={styles.listItem}>
                  {o}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={styles.card} aria-labelledby="positioning-heading">
          <h2 id="positioning-heading" className={styles.cardTitle}>
            Positioning notes
          </h2>
          {positioningNotes.length === 0 ? (
            <p className={styles.empty}>No positioning notes yet.</p>
          ) : (
            <ul className={styles.list}>
              {positioningNotes.map((n) => (
                <li key={n} className={styles.listItem}>
                  {n}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* GitHub sync — uncomment this block (+ Pill import, formatDate, github) when ready
        <section
          className={`${styles.card} ${styles.wide}`}
          aria-labelledby="github-heading"
        >
          <div className={styles.cardHead}>
            <h2 id="github-heading" className={styles.cardTitle}>
              GitHub
            </h2>
            <Pill>
              {github.connected
                ? github.statusLabel
                : "GitHub sync coming in D3"}
            </Pill>
          </div>

          {github.connected ? (
            <dl className={styles.facts}>
              <div>
                <dt>Repository</dt>
                <dd>{github.repository ?? "—"}</dd>
              </div>
              <div>
                <dt>Tracked file</dt>
                <dd>{github.trackedFile ?? "—"}</dd>
              </div>
              <div>
                <dt>Last synced</dt>
                <dd>
                  {github.lastSyncedAt
                    ? formatDate(github.lastSyncedAt)
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Recent change</dt>
                <dd>{github.recentChange ?? "None detected"}</dd>
              </div>
            </dl>
          ) : (
            <div className={styles.disconnected}>
              <p className={styles.empty}>
                Repository sync so SCADABLE’s own changes can be compared
                against competitor activity is coming in D3.
              </p>
              <Button disabled>Connect GitHub</Button>
            </div>
          )}
        </section>
        */}
      </div>
    </main>
  );
}
