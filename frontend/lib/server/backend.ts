/**
 * Absolute backend base URL for Next.js server routes.
 * Requires BACKEND_API_URL (or LIVE_API_PROXY_TARGET) — no hardcoded host.
 */
export function getBackendBaseUrl(): string {
  const fromEnv =
    process.env.BACKEND_API_URL?.trim() ||
    process.env.LIVE_API_PROXY_TARGET?.trim();
  if (!fromEnv) {
    throw new Error(
      "BACKEND_API_URL is not set. Point it at the current Railway API (never hardcode a past deploy).",
    );
  }
  return fromEnv.replace(/\/$/, "");
}

export async function backendFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const url = `${getBackendBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
  return fetch(url, {
    ...init,
    cache: "no-store",
  });
}
