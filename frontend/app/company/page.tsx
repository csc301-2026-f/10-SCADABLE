"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getGitHubIntegrationStatus,
  syncGitHubFeatures,
} from "@/lib/api/github";
import { getCompanyProfile } from "@/lib/api/company";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import styles from "./company.module.css";

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function CompanyPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["company-profile"],
    queryFn: getCompanyProfile,
  });

  const githubQuery = useQuery({
    queryKey: ["github", "status"],
    queryFn: getGitHubIntegrationStatus,
  });

  const syncMutation = useMutation({
    mutationFn: syncGitHubFeatures,
    onSuccess: (status) => {
      queryClient.setQueryData(["github", "status"], status);
      void queryClient.invalidateQueries({ queryKey: ["company-profile"] });
    },
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
  const githubStatus = githubQuery.data;

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
          <div className={styles.cardHead}>
            <h2 id="github-heading" className={styles.cardTitle}>
              GitHub
            </h2>
            <Pill>{github.statusLabel}</Pill>
          </div>

          {github.connected || github.repository ? (
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
                <dd>{formatDate(github.lastSyncedAt)}</dd>
              </div>
              <div>
                <dt>Recent change</dt>
                <dd>{github.recentChange ?? "None detected"}</dd>
              </div>
            </dl>
          ) : (
            <div className={styles.disconnected}>
              <p className={styles.empty}>
                Connect a GitHub App repository to keep SCADABLE’s product
                features in sync for competitor comparisons.
              </p>
            </div>
          )}

          {githubStatus?.message ? (
            <p className={styles.empty}>{githubStatus.message}</p>
          ) : null}

          {syncMutation.isError ? (
            <p className={styles.syncError} role="alert">
              {syncMutation.error instanceof Error
                ? syncMutation.error.message
                : "GitHub sync failed."}
            </p>
          ) : null}
          {syncMutation.isSuccess ? (
            <p className={styles.syncOk} role="status">
              GitHub sync requested.
            </p>
          ) : null}

          <div className={styles.githubActions}>
            <Button
              type="button"
              disabled={syncMutation.isPending}
              onClick={() => syncMutation.mutate()}
            >
              {syncMutation.isPending ? "Syncing…" : "Sync from GitHub"}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                void refetch();
                void githubQuery.refetch();
              }}
            >
              Refresh status
            </Button>
          </div>
        </section>
      </div>
    </main>
  );
}
