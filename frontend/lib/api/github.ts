export type GitHubIntegrationStatus = {
  installationId: number | null;
  repositoryId: number | null;
  fullName: string | null;
  defaultBranch: string | null;
  filePath: string | null;
  syncStatus: string | null;
  lastSyncedAt: string | null;
  lastError: string | null;
  revision: number | null;
  configured: boolean;
  message?: string;
};

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export async function getGitHubIntegrationStatus(): Promise<GitHubIntegrationStatus> {
  const response = await fetch("/api/github/status", { cache: "no-store" });
  return readJson<GitHubIntegrationStatus>(response);
}

export async function syncGitHubFeatures(): Promise<GitHubIntegrationStatus> {
  const response = await fetch("/api/github/sync", { method: "POST" });
  return readJson<GitHubIntegrationStatus>(response);
}
