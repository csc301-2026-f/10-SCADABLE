"use client";

import { useQuery } from "@tanstack/react-query";
import { GitHubRepoPicker } from "@/components/company/GitHubRepoPicker";
import { getCompanyProfile } from "@/lib/api/company";
import { Button } from "@/components/ui/Button";
import styles from "./company.module.css";

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

  const { offerings, positioningNotes, github } = data;

  return (
    <main className={styles.page}>
      <header>
        <h1 className={styles.title}>Our company</h1>
        <p className={styles.sub}>
          How SCADABLE is described to the research agents, and the GitHub
          source used for product features.
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

        <section
          className={`${styles.card} ${styles.wide}`}
          aria-labelledby="github-heading"
        >
          <GitHubRepoPicker
            companyStatusLabel={github.statusLabel}
            companyConnected={github.connected}
            companyRepository={github.repository}
            companyTrackedFile={github.trackedFile}
            companyLastSyncedAt={github.lastSyncedAt}
            companyRecentChange={github.recentChange}
            onCompanyRefresh={() => {
              void refetch();
            }}
          />
        </section>
      </div>
    </main>
  );
}
