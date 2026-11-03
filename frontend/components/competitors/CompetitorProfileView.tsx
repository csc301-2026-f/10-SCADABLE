"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import {
  competitorsApi,
  runsApi,
  type CompetitorSummary,
  type Finding,
  type ResearchStatus,
  type Trend,
} from "@/lib/api";
import { useFocusTrap } from "@/lib/a11y/useFocusTrap";
import { toUserFacingError } from "@/lib/errors";
import { displayWebsite, formatRelativePast } from "@/lib/format";
import { profilePollInterval, runsPollInterval } from "@/lib/polling";
import { latestSuccessfulRunForCompetitor } from "@/lib/runs/stats";
import { ConflictingDataTable } from "./ConflictingDataTable";
import { ProfileVisuals } from "./ProfileVisuals";
import { ResearchStatusBanner } from "./ResearchStatusBanner";
import styles from "./CompetitorProfile.module.css";

const TREND_LABEL: Record<Trend, string> = {
  up: "↑ More active",
  down: "↓ Less active",
  flat: "— Stable",
};

/** Longer labels for the score list; chart keeps shorter series labels. */
const SCORE_LIST_LABELS: Record<string, string> = {
  "Data collection": "Automated data collection",
  "Workflow execution": "Automated workflow execution",
  Analytics: "Analytics & reporting",
  "AI recommendations": "AI-generated recommendations",
};

function isResearchPending(status: ResearchStatus): boolean {
  return status === "pending" || status === "running";
}

function glanceTrendLabel(trend: Trend, status: ResearchStatus): string {
  if (isResearchPending(status)) return "— Pending";
  return TREND_LABEL[trend];
}

function trendClass(trend: Trend, status: ResearchStatus): string {
  if (isResearchPending(status)) return styles.trendPending;
  if (trend === "up") return styles.trendUp;
  if (trend === "down") return styles.trendDown;
  return styles.trendFlat;
}

function uniqueSources(findings: Finding[]) {
  const seen = new Set<string>();
  const sources: { label: string; url: string }[] = [];

  for (const finding of findings) {
    const key = finding.sourceUrl;
    if (seen.has(key)) continue;
    seen.add(key);
    sources.push({ label: finding.sourceLabel, url: finding.sourceUrl });
  }

  return sources;
}

export function CompetitorProfileView({ id }: { id: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const confirmTitleId = useId();
  const deleteErrorId = useId();
  const confirmDialogRef = useRef<HTMLDivElement>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["competitor", id],
    queryFn: () => competitorsApi.getCompetitor(id),
    refetchInterval: (query) => profilePollInterval(query.state.data),
  });

  const { data: runs } = useQuery({
    queryKey: ["runs"],
    queryFn: () => runsApi.listRuns(),
    refetchInterval: (query) => runsPollInterval(query.state.data),
  });

  const sources = useMemo(
    () => (data ? uniqueSources(data.findings) : []),
    [data],
  );

  /** Only after a successful completed run — hidden while pending / if latest failed. */
  const latestSuccessfulRun = useMemo(
    () => latestSuccessfulRunForCompetitor(runs ?? [], id),
    [runs, id],
  );

  const deleteMutation = useMutation({
    mutationFn: () => competitorsApi.deleteCompetitor(id),
    onSuccess: () => {
      queryClient.setQueryData<CompetitorSummary[]>(["competitors"], (prev) =>
        (prev ?? []).filter((c) => c.id !== id),
      );
      void queryClient.invalidateQueries({ queryKey: ["competitors"] });
      queryClient.removeQueries({ queryKey: ["competitor", id] });
      router.push("/competitors");
    },
    onError: (err) => {
      setDeleteError(
        toUserFacingError(err, "Could not delete profile. Please try again."),
      );
    },
  });

  useFocusTrap(confirmOpen, confirmDialogRef, () => {
    if (!deleteMutation.isPending) {
      setConfirmOpen(false);
      setDeleteError(null);
    }
  });

  const rerunMutation = useMutation({
    mutationFn: () => competitorsApi.rerunCompetitorResearch(id),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ["competitor", id] });
      const previous = queryClient.getQueryData(["competitor", id]);
      queryClient.setQueryData(["competitor", id], (current: typeof data) =>
        current ? { ...current, researchStatus: "running" as const } : current,
      );
      queryClient.setQueryData<CompetitorSummary[]>(["competitors"], (prev) =>
        (prev ?? []).map((c) =>
          c.id === id ? { ...c, researchStatus: "running" } : c,
        ),
      );
      return { previous };
    },
    onSuccess: (profile) => {
      queryClient.setQueryData(["competitor", id], profile);
      queryClient.setQueryData<CompetitorSummary[]>(["competitors"], (prev) =>
        (prev ?? []).map((c) =>
          c.id === id
            ? {
                ...c,
                researchStatus: profile.researchStatus,
                lastResearchedAt: profile.lastResearchedAt,
                recentChangeSummary: profile.recentChangeSummary,
                trend: profile.trend,
              }
            : c,
        ),
      );
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["competitors"] });
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["competitor", id], context.previous);
      }
      void queryClient.invalidateQueries({ queryKey: ["competitor", id] });
      void queryClient.invalidateQueries({ queryKey: ["competitors"] });
    },
  });

  // Keep list/runs caches in sync when polling finishes research on this profile.
  useEffect(() => {
    if (!data) return;
    if (data.researchStatus !== "success" && data.researchStatus !== "failed") {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["competitors"] });
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
  }, [data?.researchStatus, data?.lastResearchedAt, queryClient, data]);

  if (isPending) {
    return <p className={styles.status}>Loading profile…</p>;
  }

  if (isError || !data) {
    return (
      <p className={styles.status} role="alert">
        Could not load profile. {toUserFacingError(error)}
      </p>
    );
  }

  return (
    <div className={styles.page}>
      <Link href="/competitors" className={styles.back}>
        ← Back to competitors
      </Link>

      <header className={styles.hero}>
        <div>
          <h1>{data.name}</h1>
          <p>{displayWebsite(data.website)}</p>
        </div>
        <div className={styles.heroActions}>
          <span className={styles.pill}>Competitor</span>
          {latestSuccessfulRun ? (
            <Button
              variant="secondary"
              onClick={() =>
                router.push(`/runs?run=${encodeURIComponent(latestSuccessfulRun.id)}`)
              }
            >
              View latest agent run
            </Button>
          ) : null}
        </div>
      </header>

      <ResearchStatusBanner
        status={data.researchStatus}
        competitorName={data.name}
        rerunning={rerunMutation.isPending}
        onRerun={
          data.researchStatus === "failed"
            ? () => rerunMutation.mutate()
            : undefined
        }
      />

      <div className={styles.grid}>
        <div>
          <section className={styles.card}>
            <h2>Feature & automation score</h2>
            {data.chartSeries.scores.length === 0 ? (
              <p className={styles.empty}>Scores not researched yet.</p>
            ) : (
              data.chartSeries.scores.map((row) => (
                <div key={row.label} className={styles.featureRow}>
                  <span>{SCORE_LIST_LABELS[row.label] ?? row.label}</span>
                  <span className={styles.score}>{row.competitor}/10</span>
                </div>
              ))
            )}
          </section>

          <ProfileVisuals chartSeries={data.chartSeries} />

          <section className={styles.card}>
            <h2>Recent activity</h2>
            {data.recentActivity.length === 0 ? (
              <p className={styles.empty}>No recent activity yet.</p>
            ) : (
              data.recentActivity.map((item) => (
                <div key={item.id} className={styles.newsItem}>
                  {item.text}
                  <span className={styles.newsDate}>
                    {formatRelativePast(item.detectedAt)}
                  </span>
                </div>
              ))
            )}
          </section>
        </div>

        <div>
          <section className={styles.card}>
            <h2>At a glance</h2>
            <div className={styles.statRow}>
              <span>Trend</span>
              <b>
                <span
                  className={`${styles.trend} ${trendClass(data.trend, data.researchStatus)}`}
                >
                  {glanceTrendLabel(data.trend, data.researchStatus)}
                </span>
              </b>
            </div>
            <div className={styles.statRow}>
              <span>Open roles</span>
              <b>Coming in D3</b>
            </div>
            <div className={styles.statRow}>
              <span>Last checked</span>
              <b>{formatRelativePast(data.lastResearchedAt)}</b>
            </div>
            <div className={styles.statRow}>
              <span>Next refresh</span>
              <b>Coming in D3</b>
            </div>
          </section>

          <section className={styles.card}>
            <h2>Why it matters</h2>
            <p className={styles.summary}>{data.summary}</p>
          </section>

          <section className={styles.card}>
            <h2>Sources</h2>
            {sources.length === 0 ? (
              <p className={styles.empty}>No sources linked yet.</p>
            ) : (
              <ul className={styles.sourceList}>
                {sources.map((source) => (
                  <li key={source.url}>
                    <a href={source.url} target="_blank" rel="noreferrer">
                      <span>{source.label}</span>
                      <span className={styles.sourceHost}>
                        {displayWebsite(source.url)}
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {(data.conflictingData?.length ?? 0) > 0 ? (
        <ConflictingDataTable conflicts={data.conflictingData ?? []} />
      ) : null}

      <div className={styles.deleteBar}>
        <Button
          variant="danger"
          onClick={() => {
            setDeleteError(null);
            setConfirmOpen(true);
          }}
        >
          Delete profile
        </Button>
      </div>

      {confirmOpen ? (
        <div
          className={styles.overlay}
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget && !deleteMutation.isPending) {
              setConfirmOpen(false);
            }
          }}
        >
          <div
            ref={confirmDialogRef}
            className={styles.confirmModal}
            role="dialog"
            aria-modal="true"
            aria-labelledby={confirmTitleId}
            aria-describedby={deleteError ? deleteErrorId : undefined}
          >
            <h2 id={confirmTitleId}>Delete this profile?</h2>
            <p>
              Deleting <strong>{data.name}</strong> will permanently remove this
              competitor profile, research history, activity, findings, sources,
              and queued agent runs for this company.
            </p>
            <p>This cannot be undone.</p>
            {deleteError ? (
              <p
                id={deleteErrorId}
                className={styles.confirmError}
                role="alert"
              >
                {deleteError}
              </p>
            ) : null}
            <div className={styles.confirmActions}>
              <Button
                variant="secondary"
                type="button"
                data-autofocus
                onClick={() => {
                  setConfirmOpen(false);
                  setDeleteError(null);
                }}
                disabled={deleteMutation.isPending}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                type="button"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? "Deleting…" : "Delete profile"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
