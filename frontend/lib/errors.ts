import { ApiError } from "./api/client";

const GENERIC = "Something went wrong. Please try again.";

/** Patterns that look like internals / stack traces, not user copy. */
const UNSAFE_DETAIL =
  /traceback|stack trace|sqlalchemy|psycopg|operationalerror|permission denied|connection refused|econnrefused|at\s+\S+\.(py|ts|js):\d+|exception:|file "\/|secret|api[_-]?key|password|databaseurl/i;

/**
 * Map thrown errors to short UI copy. Never surface raw 5xx bodies or
 * stack-like detail from the API.
 */
export function toUserFacingError(
  err: unknown,
  fallback = GENERIC,
): string {
  if (err instanceof ApiError) {
    if (err.status >= 500) return fallback;
    const msg = err.message.trim();
    if (!msg || UNSAFE_DETAIL.test(msg) || msg.length > 280) return fallback;
    // Prefer FastAPI `{ "detail": "..." }` when present.
    try {
      const parsed = JSON.parse(msg) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        const detail = parsed.detail.trim();
        if (UNSAFE_DETAIL.test(detail) || detail.length > 280) return fallback;
        return detail;
      }
    } catch {
      /* plain text body */
    }
    return msg;
  }
  if (err instanceof Error && err.message.trim()) {
    const msg = err.message.trim();
    if (UNSAFE_DETAIL.test(msg) || msg.length > 280) return fallback;
    return msg;
  }
  return fallback;
}

/**
 * Safe display for agent-run failure reasons from the API.
 * Keeps short human messages; replaces internals with a generic line.
 */
export function toUserFacingRunError(
  message: string | null | undefined,
): string {
  const msg = message?.trim();
  if (!msg) return "Research run failed.";
  if (UNSAFE_DETAIL.test(msg) || msg.length > 280) {
    return "Research run failed.";
  }
  return msg;
}
