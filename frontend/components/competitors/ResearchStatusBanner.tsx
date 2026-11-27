"use client";

import { Button } from "@/components/ui/Button";
import type { ResearchStatus, RunStep } from "@/lib/api";
import styles from "./CompetitorProfile.module.css";

const STAGE_LABELS = new Map([
  ["Crawl competitor website", "Finding and scraping pages"],
  ["Analyze with LLM", "Extracting features and job postings"],
  ["Save SWOT findings", "Saving findings"],
]);

type Props = {
  status: ResearchStatus;
  competitorName: string;
  activeStep?: RunStep;
  rerunning?: boolean;
  onRerun?: () => void;
  /** Shown while a run is queued or running. Omit to hide the Cancel button. */
  onCancel?: () => void;
  cancelling?: boolean;
  cancelError?: string | null;
  /** Keep buttons visible but not runnable (e.g. TA read-only bypass). */
  actionsDisabled?: boolean;
  actionsDisabledReason?: string;
};

export function ResearchStatusBanner({
  status,
  competitorName,
  activeStep,
  rerunning = false,
  onRerun,
  onCancel,
  cancelling = false,
  cancelError = null,
  actionsDisabled = false,
  actionsDisabledReason = "Sign in required",
}: Props) {
  if (status === "success") return null;

  if (status === "cancelled") {
    return (
      <div
        className={`${styles.researchBanner} ${styles.researchBannerCancelled}`}
        role="status"
      >
        <div className={styles.researchBannerCopy}>
          <strong>Agent run cancelled</strong>
          <p>
            The last agent run for {competitorName} was stopped before it
            finished. Results from earlier runs are still shown. You can queue
            another agent run.
          </p>
        </div>
        {onRerun ? (
          <Button
            variant="secondary"
            onClick={onRerun}
            disabled={rerunning || actionsDisabled}
            title={actionsDisabled ? actionsDisabledReason : undefined}
          >
            {rerunning ? "Rerunning…" : "Rerun agent"}
          </Button>
        ) : null}
      </div>
    );
  }

  if (status === "failed") {
    return (
      <div
        className={`${styles.researchBanner} ${styles.researchBannerFailed}`}
        role="status"
      >
        <div className={styles.researchBannerCopy}>
          <strong>Agent run failed</strong>
          <p>
            Research for {competitorName} did not finish successfully. Profile
            data may be incomplete. You can queue another agent run.
          </p>
        </div>
        {onRerun ? (
          <Button
            variant="secondary"
            onClick={onRerun}
            disabled={rerunning || actionsDisabled}
            title={actionsDisabled ? actionsDisabledReason : undefined}
          >
            {rerunning ? "Rerunning…" : "Rerun agent"}
          </Button>
        ) : null}
      </div>
    );
  }

  const inProgress = status === "running" || status === "pending";
  if (!inProgress) return null;

  return (
    <div
      className={`${styles.researchBanner} ${styles.researchBannerProgress}`}
      role="status"
    >
      <div className={styles.researchBannerCopy}>
        <strong className={styles.researchBannerStage}>
          <span className={styles.researchBannerPulse} aria-hidden="true" />
          <span>
            {status === "running"
              ? (activeStep && STAGE_LABELS.get(activeStep.name)) ??
                "Agent run in progress"
              : "Agent research queued"}
          </span>
        </strong>
        <p>
          We&apos;re collecting signals across the competitor&apos;s data
          sources. This status bar will clear once the run succeeds and the
          profile is populated.
        </p>
        {cancelError ? (
          <p className={styles.researchBannerError} role="alert">
            {cancelError}
          </p>
        ) : null}
      </div>
      {onCancel ? (
        <Button
          variant="secondary"
          onClick={onCancel}
          disabled={cancelling || actionsDisabled}
          title={actionsDisabled ? actionsDisabledReason : undefined}
        >
          {cancelling ? "Cancelling…" : "Cancel run"}
        </Button>
      ) : null}
    </div>
  );
}
