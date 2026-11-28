const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function shouldUseMocks(): boolean {
  return process.env.NEXT_PUBLIC_USE_MOCKS === "true";
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Shared fetch wrapper. Auth headers (Clerk session / API key) can be
 * plugged in here later without touching page components.
 */
export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: HeadersInit = {
    Accept: "application/json",
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  // Placeholder for future auth:
  // const token = await getToken();
  // if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
    cache: "no-store",
  });

  if (!response.ok) {
    // Keep 5xx bodies out of Error.message so UI helpers never leak internals.
    if (response.status >= 500) {
      throw new ApiError("Something went wrong. Please try again.", response.status);
    }
    const detail = await response.text().catch(() => "");
    throw new ApiError(
      detail || `Request failed: ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
