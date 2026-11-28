"use client";

import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { runsApi, type AgentRun, type ResearchStatus } from "@/lib/api";
import { toUserFacingError, toUserFacingRunError } from "@/lib/errors";
import { formatRelativePast } from "@/lib/format";
import { runsPollInterval } from "@/lib/polling";
import {
  computeWeeklyRunStats,
  formatCacheStat,
  formatRunCost,
  formatRunStatus,
  latestRunPerCompetitor,
} from "@/lib/runs/stats";
import styles from "./AgentRunsList.module.css";

function statusClass(status: ResearchStatus): string {
  if (status === "success") return styles.statusSuccess;
  if (status === "failed") return styles.statusFailed;
  if (status === "cancelled") return styles.statusCancelled;
  if (status === "running") return styles.statusRunning;
  return styles.statusPending;
}

function formatRunDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return "Still running";
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return rem ? `${minutes}m ${rem}s` : `${minutes}m`;
}

function runDetailSummary(run: AgentRun): string {
  const failedStep = run.steps.find((step) => step.status === "failed");
  const okSteps = run.steps.filter((step) => step.status === "success").length;
  const duration = formatRunDuration(run.startedAt, run.finishedAt);

  if (run.status === "failed" && failedStep) {
    return `Failed at “${failedStep.name}” · ${okSteps}/${run.steps.length} steps ok · ${duration}`;
  }
  if (run.status === "failed") {
    return `Run failed · ${okSteps}/${run.steps.length} steps ok · ${duration}`;
  }
  if (run.status === "cancelled") {
    return `Cancelled · ${okSteps}/${run.steps.length} steps done · ${duration}`;
  }
  if (run.status === "running" || run.status === "pending") {
    return `In progress · ${okSteps}/${run.steps.length} steps done`;
  }
  return `Completed ${okSteps}/${run.steps.length} steps · ${duration}`;
}

/** Expanded run details, shared by the desktop table row and the mobile card. */
function RunDetail({ run }: { run: AgentRun }) {
  const failedStepName = run.steps.find((step) => step.status === "failed")?.name;

  return (
    <div className={styles.runDetail}>
      <p className={styles.runSummary}>{runDetailSummary(run)}</p>
      <div className={styles.runSteps}>
        {run.steps.map((step, index) => (
          <div
            key={`${run.id}-step-${index}`}
            className={`${styles.runStep} ${
              step.status === "failed" ? styles.runStepFailed : ""
            }`}
          >
            <span>{step.name}</span>
            <span className={styles.stepResult}>{step.result}</span>
          </div>
        ))}
      </div>
      {run.status === "failed" && (run.errorMessage || failedStepName) ? (
        <div className={styles.failureReason}>
          <span className={styles.failureReasonLabel}>Reason for failure</span>
          <p>
            {failedStepName ? `Stopped on “${failedStepName}”. ` : null}
            {toUserFacingRunError(run.errorMessage)}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
}: {
  run: AgentRun;
  expanded: boolean;
  onToggle: () => void;
}) {
  const cache = formatCacheStat(run.cacheHits, run.cacheMisses);

  return (
    <>
      <tr
        className={`${styles.clickable} ${expanded ? styles.rowExpanded : ""}`}
        tabIndex={0}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className={styles.companyCell}>{run.competitorName}</td>
        <td>
          <span className={`${styles.status} ${statusClass(run.status)}`}>
            {formatRunStatus(run.status)}
          </span>
        </td>
        <td>{formatRunCost(run.costUsd)}</td>
        <td>{run.stepCount}</td>
        <td>
          {cache ? (
            <span className={styles.cacheStat}>
              <b>{cache.hitsLabel}</b> / {cache.missesLabel}
            </span>
          ) : (
            "—"
          )}
        </td>
        <td className={styles.lastRun}>{formatRelativePast(run.startedAt)}</td>
        <td
          className={`${styles.rowArrow} ${expanded ? styles.rowArrowOpen : ""}`}
          aria-hidden="true"
        >
          ›
        </td>
      </tr>
      {expanded ? (
        <tr className={styles.detailRow}>
          <td colSpan={7}>
            <RunDetail run={run} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** Mobile layout: one card per run, tap to expand the same details. */
function RunCard({
  run,
  expanded,
  onToggle,
}: {
  run: AgentRun;
  expanded: boolean;
  onToggle: () => void;
}) {
  const cache = formatCacheStat(run.cacheHits, run.cacheMisses);

  return (
    <li className={`${styles.card} ${expanded ? styles.cardExpanded : ""}`}>
      <button
        type="button"
        className={styles.cardButton}
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <span className={styles.cardTop}>
          <span className={styles.cardName}>{run.competitorName}</span>
          <span className={styles.cardTopRight}>
            <span className={`${styles.status} ${statusClass(run.status)}`}>
              {formatRunStatus(run.status)}
            </span>
            <span
              className={`${styles.cardChevron} ${
                expanded ? styles.rowArrowOpen : ""
              }`}
              aria-hidden="true"
            >
              ›
            </span>
          </span>
        </span>

        <span className={styles.cardStats}>
          <span className={styles.cardStat}>
            <span className={styles.cardStatLabel}>Cost</span>
            <span className={styles.cardStatValue}>
              {formatRunCost(run.costUsd)}
            </span>
          </span>
          <span className={styles.cardStat}>
            <span className={styles.cardStatLabel}>Steps</span>
            <span className={styles.cardStatValue}>{run.stepCount}</span>
          </span>
          <span className={styles.cardStat}>
            <span className={styles.cardStatLabel}>Cache</span>
            <span className={styles.cardStatValue}>
              {cache ? (
                <span className={styles.cacheStat}>
                  <b>{cache.hitsLabel}</b> / {cache.missesLabel}
                </span>
              ) : (
                "—"
              )}
            </span>
          </span>
          <span className={styles.cardStat}>
            <span className={styles.cardStatLabel}>Last run</span>
            <span className={styles.cardStatValue}>
              {formatRelativePast(run.startedAt)}
            </span>
          </span>
        </span>
      </button>

      {expanded ? (
        <div className={styles.cardDetail}>
          <RunDetail run={run} />
        </div>
      ) : null}
    </li>
  );
}

export function AgentRunsList() {
  const searchParams = useSearchParams();
  const runFromUrl = searchParams.get("run");
  const [expandedId, setExpandedId] = useState<string | null>(runFromUrl);

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["runs"],
    queryFn: () => runsApi.listRuns(),
    refetchInterval: (query) => runsPollInterval(query.state.data),
  });

  const runs = useMemo(() => data ?? [], [data]);

  const weeklyStats = useMemo(() => computeWeeklyRunStats(runs), [runs]);
  const latestRows = useMemo(() => latestRunPerCompetitor(runs), [runs]);

  if (isPending) {
    return <p className={styles.statusMessage}>Loading agent runs…</p>;
  }

  if (isError) {
    return (
      <p className={styles.statusMessage} role="alert">
        Could not load agent runs. {toUserFacingError(error)}
      </p>
    );
  }

  const toggle = (id: string) =>
    setExpandedId((current) => (current === id ? null : id));

  return (
    <div className={styles.page}>
      <header className={styles.pageHead}>
        <h1>Agent Runs</h1>
        <p>Internal monitoring for automated competitor research</p>
      </header>

      <div className={styles.summaryGrid}>
        <div className={styles.summaryCard}>
          <span className={styles.summaryLabel}>Runs this week</span>
          <span className={styles.summaryValue}>{weeklyStats.runsThisWeek}</span>
        </div>
        <div className={styles.summaryCard}>
          <span className={styles.summaryLabel}>Successful</span>
          <span className={styles.summaryValue}>{weeklyStats.successful}</span>
        </div>
        <div className={styles.summaryCard}>
          <span className={styles.summaryLabel} title="Sum of available cost estimates for returned runs from the past seven days.">Recorded cost</span>
          <span className={styles.summaryValue}>
            {formatRunCost(weeklyStats.estimatedCostUsd)}
          </span>
        </div>
        <div className={styles.summaryCard}>
          <span className={styles.summaryLabel} title="Percentage of analyzed runs that reused prior analysis because website content was unchanged.">Cache hit rate</span>
          <span className={styles.summaryValue}>
            {weeklyStats.cacheHitRatePct === null ? "—" : `${weeklyStats.cacheHitRatePct}%`}
          </span>
        </div>
      </div>

      {/* Desktop / tablet: table */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Competitor</th>
              <th>Status</th>
              <th>Cost</th>
              <th>Steps</th>
              <th>Cache</th>
              <th>Last run</th>
              <th aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {latestRows.length === 0 ? (
              <tr className={styles.emptyRow}>
                <td colSpan={7}>No agent runs yet.</td>
              </tr>
            ) : (
              latestRows.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  expanded={expandedId === run.id}
                  onToggle={() => toggle(run.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile: card list (table is hidden under 768px) */}
      <ul className={styles.cards}>
        {latestRows.length === 0 ? (
          <li className={styles.cardsEmpty}>No agent runs yet.</li>
        ) : (
          latestRows.map((run) => (
            <RunCard
              key={run.id}
              run={run}
              expanded={expandedId === run.id}
              onToggle={() => toggle(run.id)}
            />
          ))
        )}
      </ul>
    </div>
  );
}
