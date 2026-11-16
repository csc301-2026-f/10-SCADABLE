import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server/backend";

export const dynamic = "force-dynamic";

type ScrapingDepth = "low" | "medium" | "high";

function mapBody(data: { depth: ScrapingDepth; max_pages?: number; maxPages?: number }) {
  return {
    depth: data.depth,
    maxPages: data.maxPages ?? data.max_pages ?? 0,
  };
}

export async function GET() {
  const response = await backendFetch("/settings/scraping");
  const text = await response.text();
  if (!response.ok) {
    return new NextResponse(text || "Failed to load scraping settings", {
      status: response.status,
    });
  }
  const data = JSON.parse(text) as {
    depth: ScrapingDepth;
    max_pages?: number;
    maxPages?: number;
  };
  return NextResponse.json(mapBody(data));
}

export async function PUT(request: Request) {
  const token = process.env.SCRAPING_SETTINGS_ADMIN_TOKEN?.trim();
  if (!token) {
    return NextResponse.json(
      {
        detail:
          "Scraping settings updates are not configured. Set SCRAPING_SETTINGS_ADMIN_TOKEN on the Next.js server.",
      },
      { status: 503 },
    );
  }

  let body: { depth?: ScrapingDepth };
  try {
    body = (await request.json()) as { depth?: ScrapingDepth };
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  if (body.depth !== "low" && body.depth !== "medium" && body.depth !== "high") {
    return NextResponse.json(
      { detail: "depth must be low, medium, or high." },
      { status: 422 },
    );
  }

  const response = await backendFetch("/settings/scraping", {
    method: "PUT",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ depth: body.depth }),
  });

  const text = await response.text();
  if (!response.ok) {
    return new NextResponse(text || "Failed to update scraping settings", {
      status: response.status,
    });
  }

  const data = JSON.parse(text) as {
    depth: ScrapingDepth;
    max_pages?: number;
    maxPages?: number;
  };
  return NextResponse.json(mapBody(data));
}
