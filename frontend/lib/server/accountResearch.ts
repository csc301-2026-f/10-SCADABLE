import { auth, clerkClient } from "@clerk/nextjs/server";

/** Must match the key written by `/api/account/openai`. */
export const OPENAI_PRIVATE_META_KEY = "openaiApiKey";

/**
 * Build headers for POST /competitors and /competitors/:id/rerun.
 * If the signed-in user has a Clerk privateMetadata OpenAI key and
 * ACCOUNT_RESEARCH_BRIDGE_TOKEN is set, forward both bridge headers.
 * Otherwise omit them so Railway falls back to OPENAI_API_KEY.
 * Never log the key or bridge token.
 */
export async function accountResearchHeaders(): Promise<HeadersInit> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };

  const bridge = process.env.ACCOUNT_RESEARCH_BRIDGE_TOKEN?.trim();
  if (!bridge) return headers;

  const { userId } = await auth();
  if (!userId) return headers;

  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  const raw = user.privateMetadata?.[OPENAI_PRIVATE_META_KEY];
  const key = typeof raw === "string" ? raw.trim() : "";
  if (!key.startsWith("sk-") || key.length < 20) return headers;

  headers["X-Scadable-Account-OpenAI-Key"] = key;
  headers["X-Scadable-Research-Bridge-Token"] = bridge;
  return headers;
}
