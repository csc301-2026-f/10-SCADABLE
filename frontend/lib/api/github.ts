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

export type GitHubInstallation = {
  id: number;
  account: string;
  accountType: string;
};

export type GitHubRepository = {
  id: number;
  fullName: string;
  defaultBranch: string;
  private: boolean;
};

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        throw new Error(parsed.detail);
      }
    } catch (err) {
      if (err instanceof Error && err.message !== detail) throw err;
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export async function getGitHubIntegrationStatus(): Promise<GitHubIntegrationStatus> {
  const response = await fetch("/api/github/status", { cache: "no-store" });
  return readJson<GitHubIntegrationStatus>(response);
}

export async function listGitHubInstallations(): Promise<GitHubInstallation[]> {
  const response = await fetch("/api/github/installations", {
    cache: "no-store",
  });
  const data = await readJson<{ installations: GitHubInstallation[] }>(response);
  return data.installations;
}

export async function listGitHubRepositories(
  installationId: number,
): Promise<GitHubRepository[]> {
  const response = await fetch(
    `/api/github/repositories?installationId=${installationId}`,
    { cache: "no-store" },
  );
  const data = await readJson<{ repositories: GitHubRepository[] }>(response);
  return data.repositories;
}

export async function selectGitHubRepository(input: {
  installationId: number;
  repositoryId: number;
  filePath?: string;
}): Promise<GitHubIntegrationStatus> {
  const response = await fetch("/api/github/selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJson<GitHubIntegrationStatus>(response);
}

export async function syncGitHubFeatures(): Promise<GitHubIntegrationStatus> {
  const response = await fetch("/api/github/sync", { method: "POST" });
  return readJson<GitHubIntegrationStatus>(response);
}
