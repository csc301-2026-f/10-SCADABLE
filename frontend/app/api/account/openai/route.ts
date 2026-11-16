import { auth, clerkClient } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const META_KEY = "openaiApiKey";

function hintFromKey(key: string | undefined | null): string | null {
  if (!key || typeof key !== "string") return null;
  const trimmed = key.trim();
  if (trimmed.length < 8) return "••••";
  return `${trimmed.slice(0, 3)}…${trimmed.slice(-4)}`;
}

async function readKey(userId: string): Promise<string | null> {
  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  const value = user.privateMetadata?.[META_KEY];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }

  const key = await readKey(userId);
  return NextResponse.json({
    configured: Boolean(key),
    hint: hintFromKey(key),
  });
}

export async function PUT(request: Request) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }

  let body: { apiKey?: string };
  try {
    body = (await request.json()) as { apiKey?: string };
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const apiKey = body.apiKey?.trim() ?? "";
  if (!apiKey.startsWith("sk-") || apiKey.length < 20) {
    return NextResponse.json(
      { detail: "Enter a valid OpenAI API key (starts with sk-)." },
      { status: 422 },
    );
  }

  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  await client.users.updateUserMetadata(userId, {
    privateMetadata: {
      ...user.privateMetadata,
      [META_KEY]: apiKey,
    },
  });

  return NextResponse.json({
    configured: true,
    hint: hintFromKey(apiKey),
  });
}

export async function DELETE() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }

  const client = await clerkClient();
  const user = await client.users.getUser(userId);
  const nextMeta = { ...user.privateMetadata };
  delete nextMeta[META_KEY];
  await client.users.updateUserMetadata(userId, {
    privateMetadata: nextMeta,
  });

  return NextResponse.json({ configured: false, hint: null });
}
