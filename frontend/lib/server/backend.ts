/**
 * Absolute backend base URL for Next.js server routes.
 * Prefer BACKEND_API_URL; fall back to the Railway production host.
 */
export function getBackendBaseUrl(): string {
  const fromEnv =
    process.env.BACKEND_API_URL?.trim() ||
    process.env.LIVE_API_PROXY_TARGET?.trim();
  if (fromEnv) return fromEnv.replace(/\/$/, "");
  return "https://backend-d3-production.up.railway.app";
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
