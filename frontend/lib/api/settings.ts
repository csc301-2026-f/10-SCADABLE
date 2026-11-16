export type ScrapingDepth = "low" | "medium" | "high";

export type ScrapingSettings = {
  depth: ScrapingDepth;
  maxPages: number;
};

export type OpenAiAccountSettings = {
  configured: boolean;
  hint: string | null;
};

const DEPTH_PAGES: Record<ScrapingDepth, number> = {
  low: 5,
  medium: 10,
  high: 15,
};

export function pagesForDepth(depth: ScrapingDepth): number {
  return DEPTH_PAGES[depth];
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

/** Public GET — no admin token required. */
export async function getScrapingSettings(): Promise<ScrapingSettings> {
  const response = await fetch("/api/settings/scraping", { cache: "no-store" });
  return readJson<ScrapingSettings>(response);
}

/** PUT via server gateway (admin token stays on the server). */
export async function updateScrapingSettings(
  depth: ScrapingDepth,
): Promise<ScrapingSettings> {
  const response = await fetch("/api/settings/scraping", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ depth }),
  });
  return readJson<ScrapingSettings>(response);
}

export async function getOpenAiAccountSettings(): Promise<OpenAiAccountSettings> {
  const response = await fetch("/api/account/openai", { cache: "no-store" });
  return readJson<OpenAiAccountSettings>(response);
}

export async function saveOpenAiAccountKey(
  apiKey: string,
): Promise<OpenAiAccountSettings> {
  const response = await fetch("/api/account/openai", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
  return readJson<OpenAiAccountSettings>(response);
}

export async function clearOpenAiAccountKey(): Promise<OpenAiAccountSettings> {
  const response = await fetch("/api/account/openai", { method: "DELETE" });
  return readJson<OpenAiAccountSettings>(response);
}
