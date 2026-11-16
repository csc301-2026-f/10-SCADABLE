"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import {
  getGitHubIntegrationStatus,
  listGitHubInstallations,
  listGitHubRepositories,
  selectGitHubRepository,
  syncGitHubFeatures,
  type GitHubIntegrationStatus,
} from "@/lib/api/github";
import styles from "./GitHubRepoPicker.module.css";

const DEFAULT_FILE_PATH = "docs/scadable-features.json";

type Props = {
  companyStatusLabel: string;
  companyConnected: boolean;
  companyRepository: string | null;
  companyTrackedFile: string | null;
  companyLastSyncedAt: string | null;
  companyRecentChange: string | null;
  onCompanyRefresh: () => void;
};

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function GitHubRepoPicker({
  companyStatusLabel,
  companyConnected,
  companyRepository,
  companyTrackedFile,
  companyLastSyncedAt,
  companyRecentChange,
  onCompanyRefresh,
}: Props) {
  const queryClient = useQueryClient();
  const [installationId, setInstallationId] = useState<number | "">("");
  const [repositoryId, setRepositoryId] = useState<number | "">("");
  const [filePath, setFilePath] = useState(DEFAULT_FILE_PATH);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const statusQuery = useQuery({
    queryKey: ["github", "status"],
    queryFn: getGitHubIntegrationStatus,
  });

  const installationsQuery = useQuery({
    queryKey: ["github", "installations"],
    queryFn: listGitHubInstallations,
    retry: false,
  });

  const reposQuery = useQuery({
    queryKey: ["github", "repositories", installationId],
    queryFn: () => listGitHubRepositories(Number(installationId)),
    enabled: typeof installationId === "number" && installationId > 0,
    retry: false,
  });

  const selectMutation = useMutation({
    mutationFn: selectGitHubRepository,
    onSuccess: (status: GitHubIntegrationStatus) => {
      queryClient.setQueryData(["github", "status"], status);
      void queryClient.invalidateQueries({ queryKey: ["company-profile"] });
      setError(null);
      setMessage(
        `Selected ${status.fullName ?? "repository"}${
          status.filePath ? ` (${status.filePath})` : ""
        }.`,
      );
      onCompanyRefresh();
    },
    onError: (err) => {
      setMessage(null);
      setError(err instanceof Error ? err.message : "Could not save selection.");
    },
  });

  const syncMutation = useMutation({
    mutationFn: syncGitHubFeatures,
    onSuccess: (status) => {
      queryClient.setQueryData(["github", "status"], status);
      void queryClient.invalidateQueries({ queryKey: ["company-profile"] });
      setError(null);
      setMessage("GitHub sync requested.");
      onCompanyRefresh();
    },
    onError: (err) => {
      setMessage(null);
      setError(err instanceof Error ? err.message : "GitHub sync failed.");
    },
  });

  const status = statusQuery.data;
  const canSave =
    typeof installationId === "number" &&
    typeof repositoryId === "number" &&
    filePath.trim().length > 0;

  return (
    <div className={styles.root}>
      <div className={styles.head}>
        <h2 id="github-heading" className={styles.title}>
          GitHub
        </h2>
        <Pill>{companyStatusLabel}</Pill>
      </div>

      {companyConnected || companyRepository ? (
        <dl className={styles.facts}>
          <div>
            <dt>Repository</dt>
            <dd>{companyRepository ?? "—"}</dd>
          </div>
          <div>
            <dt>Tracked file</dt>
            <dd>{companyTrackedFile ?? "—"}</dd>
          </div>
          <div>
            <dt>Last synced</dt>
            <dd>{formatDate(companyLastSyncedAt)}</dd>
          </div>
          <div>
            <dt>Recent change</dt>
            <dd>{companyRecentChange ?? "None detected"}</dd>
          </div>
        </dl>
      ) : (
        <p className={styles.hint}>
          Connect a GitHub App repository to keep SCADABLE’s product features in
          sync for competitor comparisons.
        </p>
      )}

      {status?.message && !status.configured ? (
        <p className={styles.hint}>{status.message}</p>
      ) : null}

      <div className={styles.picker}>
        <h3 className={styles.pickerTitle}>Choose repository</h3>
        <p className={styles.hint}>
          Pick a GitHub App installation and repository, then optionally set the
          feature JSON path (default <code>{DEFAULT_FILE_PATH}</code>).
          Dropdowns fill once the GitHub admin APIs are on Railway and{" "}
          <code>GITHUB_INTEGRATION_ADMIN_TOKEN</code> is set on this Next
          server (same value as backend).
        </p>

        {installationsQuery.isError ? (
          <p className={styles.error} role="alert">
            {installationsQuery.error instanceof Error
              ? installationsQuery.error.message
              : "Could not load installations."}
          </p>
        ) : null}

        <label className={styles.label} htmlFor="github-installation">
          Installation
        </label>
        <select
          id="github-installation"
          className={styles.select}
          value={installationId === "" ? "" : String(installationId)}
          disabled={installationsQuery.isPending || installationsQuery.isError}
          onChange={(e) => {
            const next = e.target.value ? Number(e.target.value) : "";
            setInstallationId(next);
            setRepositoryId("");
            setMessage(null);
            setError(null);
          }}
        >
          <option value="">
            {installationsQuery.isPending
              ? "Loading installations…"
              : "Select installation…"}
          </option>
          {(installationsQuery.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.account}
              {item.accountType ? ` (${item.accountType})` : ""} — #{item.id}
            </option>
          ))}
        </select>

        <label className={styles.label} htmlFor="github-repository">
          Repository
        </label>
        <select
          id="github-repository"
          className={styles.select}
          value={repositoryId === "" ? "" : String(repositoryId)}
          disabled={
            typeof installationId !== "number" ||
            reposQuery.isPending ||
            Boolean(reposQuery.isError)
          }
          onChange={(e) => {
            setRepositoryId(e.target.value ? Number(e.target.value) : "");
            setMessage(null);
            setError(null);
          }}
        >
          <option value="">
            {typeof installationId !== "number"
              ? "Select an installation first…"
              : reposQuery.isPending
                ? "Loading repositories…"
                : "Select repository…"}
          </option>
          {(reposQuery.data ?? []).map((repo) => (
            <option key={repo.id} value={repo.id}>
              {repo.fullName}
              {repo.private ? " (private)" : ""} — {repo.defaultBranch}
            </option>
          ))}
        </select>

        {reposQuery.isError ? (
          <p className={styles.error} role="alert">
            {reposQuery.error instanceof Error
              ? reposQuery.error.message
              : "Could not load repositories."}
          </p>
        ) : null}

        <label className={styles.label} htmlFor="github-file-path">
          Feature file path
        </label>
        <input
          id="github-file-path"
          className={styles.input}
          type="text"
          value={filePath}
          onChange={(e) => setFilePath(e.target.value)}
          placeholder={DEFAULT_FILE_PATH}
          autoComplete="off"
        />

        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        {message ? (
          <p className={styles.ok} role="status">
            {message}
          </p>
        ) : null}

        <div className={styles.actions}>
          <Button
            type="button"
            disabled={!canSave || selectMutation.isPending}
            onClick={() =>
              selectMutation.mutate({
                installationId: Number(installationId),
                repositoryId: Number(repositoryId),
                filePath: filePath.trim(),
              })
            }
          >
            {selectMutation.isPending ? "Saving…" : "Save selection"}
          </Button>
          <Button
            type="button"
            disabled={syncMutation.isPending || !status?.configured}
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending ? "Syncing…" : "Sync from GitHub"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              void statusQuery.refetch();
              void installationsQuery.refetch();
              if (typeof installationId === "number") {
                void reposQuery.refetch();
              }
              onCompanyRefresh();
            }}
          >
            Refresh status
          </Button>
        </div>
      </div>
    </div>
  );
}
