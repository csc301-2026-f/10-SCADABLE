import type { AnchorHTMLAttributes } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import OverviewPage from "@/app/overview/page";
import { ApiError } from "@/lib/api/client";
import { getSwotOverview } from "@/lib/api/swot";
import type { SwotOverview } from "@/lib/api";
import { makeFinding, renderWithQuery } from "./helpers";

// A plain anchor, so links render without the Next.js router.
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) =>
      createElement("a", props, children),
  };
});
vi.mock("@/lib/api/swot", () => ({ getSwotOverview: vi.fn() }));

const overviewApi = vi.mocked(getSwotOverview);

function overview(overrides: Partial<SwotOverview> = {}): SwotOverview {
  return {
    isStale: false,
    updatedAt: "2026-11-20T12:00:00Z",
    strengths: [makeFinding({ isNew: true })],
    weaknesses: [],
    opportunities: [
      makeFinding({
        id: "f2",
        category: "opportunity",
        competitorId: null,
        competitorName: "",
        text: "Few competitors offer HIPAA templates.",
      }),
    ],
    threats: [],
    ...overrides,
  };
}

function quadrant(title: string) {
  return screen.getByRole("region", { name: title });
}

describe("SWOT Overview page", () => {
  it("shows all four quadrants with their findings and counts", async () => {
    overviewApi.mockResolvedValue(overview());
    renderWithQuery(<OverviewPage />);
    await screen.findByText("SWOT overview");

    for (const title of ["Strengths", "Weaknesses", "Opportunities", "Threats"]) {
      expect(quadrant(title)).toBeTruthy();
    }
    const strengths = quadrant("Strengths");
    expect(within(strengths).getByText("SCADABLE automates SOC 2 evidence collection.")).toBeTruthy();
    expect(within(strengths).getByText("New")).toBeTruthy();
    expect(within(quadrant("Weaknesses")).getByText("No findings yet.")).toBeTruthy();
    expect(within(quadrant("Weaknesses")).getByText("0")).toBeTruthy();
  });

  it("links a finding to its competitor, or labels it SCADABLE when it has none", async () => {
    overviewApi.mockResolvedValue(overview());
    renderWithQuery(<OverviewPage />);
    await screen.findByText("SWOT overview");

    const link = within(quadrant("Strengths")).getByRole("link", { name: "Acme Compliance" });
    expect(link.getAttribute("href")).toBe("/competitors/1");
    expect(within(quadrant("Opportunities")).getByText("SCADABLE")).toBeTruthy();
  });

  it("keeps published findings visible when they are stale", async () => {
    overviewApi.mockResolvedValue(overview({ isStale: true }));
    renderWithQuery(<OverviewPage />);

    expect(await screen.findByText("SWOT findings may be outdated.")).toBeTruthy();
    expect(screen.getByText(/Previously published findings/)).toBeTruthy();
    expect(screen.getByText("SCADABLE automates SOC 2 evidence collection.")).toBeTruthy();
  });

  it("asks for a refresh instead of empty quadrants when nothing was published", async () => {
    overviewApi.mockResolvedValue(
      overview({ isStale: true, strengths: [], opportunities: [], updatedAt: null }),
    );
    renderWithQuery(<OverviewPage />);

    expect(await screen.findByText("Overall SWOT needs refreshing")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Strengths" })).toBeNull();
  });

  it("shows a cleaned up error with a working Try again button", async () => {
    overviewApi.mockRejectedValueOnce(new ApiError(JSON.stringify({ detail: "Not Found" }), 404));
    overviewApi.mockResolvedValueOnce(overview());
    renderWithQuery(<OverviewPage />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn’t load the SWOT overview. Not Found");
    expect(alert.textContent).not.toContain("{");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("SWOT overview")).toBeTruthy());
    expect(overviewApi).toHaveBeenCalledTimes(2);
  });
});
