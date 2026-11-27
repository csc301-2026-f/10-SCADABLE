import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { AgentRunsList } from "@/components/runs/AgentRunsList";
import { ApiError } from "@/lib/api/client";
import { listRuns } from "@/lib/api/runs";
import { LEAKY_SERVER_ERROR, makeRun, renderWithQuery } from "./helpers";

const nav = vi.hoisted(() => ({ search: "" }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock("@/lib/api/runs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/runs")>()),
  listRuns: vi.fn(),
}));

const runsApi = vi.mocked(listRuns);
const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const runs = [
  // Older failed run for Acme is replaced in the table by the newer success.
  makeRun({ id: "1", status: "failed", startedAt: minutesAgo(60), finishedAt: minutesAgo(59), costUsd: 0.02 }),
  makeRun({ id: "2", status: "success", startedAt: minutesAgo(5), finishedAt: minutesAgo(4), costUsd: 0.05 }),
  makeRun({
    id: "3",
    competitorId: "2",
    competitorName: "Vanta Like",
    status: "failed",
    startedAt: minutesAgo(30),
    finishedAt: minutesAgo(29),
    costUsd: null,
    errorMessage: "Could not crawl the website.",
    steps: [
      { name: "Crawl competitor website", result: "Failed", status: "failed" },
      { name: "Analyze with LLM", result: "Skipped", status: "pending" },
    ],
  }),
  // Still running, so it stays out of the table but counts toward this week.
  makeRun({
    id: "4",
    competitorId: "3",
    competitorName: "Drata Like",
    status: "running",
    startedAt: minutesAgo(1),
    finishedAt: null,
  }),
];

function tableRows() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

function summaryValue(label: string) {
  return screen.getByText(label).nextElementSibling?.textContent;
}

describe("AgentRunsList", () => {
  it("shows one finished run per competitor, newest first", async () => {
    nav.search = "";
    runsApi.mockResolvedValue(runs);
    renderWithQuery(<AgentRunsList />);
    await screen.findByText("Agent Runs");

    const rows = tableRows();
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Acme Compliance")).toBeTruthy();
    expect(within(rows[0]).getByText("Success")).toBeTruthy();
    expect(within(rows[0]).getByText("$0.05")).toBeTruthy();
    expect(within(rows[1]).getByText("Vanta Like")).toBeTruthy();
    expect(screen.queryByText("Drata Like")).toBeNull();
  });

  it("totals this week's runs, successes and known costs", async () => {
    nav.search = "";
    runsApi.mockResolvedValue(runs);
    renderWithQuery(<AgentRunsList />);
    await screen.findByText("Agent Runs");

    expect(summaryValue("Runs this week")).toBe("4");
    expect(summaryValue("Successful")).toBe("1");
    expect(summaryValue("Recorded cost")).toBe("$0.12");
  });

  it("shows why a run failed when its row is expanded", async () => {
    nav.search = "";
    runsApi.mockResolvedValue(runs);
    renderWithQuery(<AgentRunsList />);
    await screen.findByText("Agent Runs");

    expect(screen.queryByText("Reason for failure")).toBeNull();
    fireEvent.click(tableRows()[1]);
    expect(screen.getAllByText("Reason for failure").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Stopped on “Crawl competitor website”\. Could not crawl the website\./).length,
    ).toBeGreaterThan(0);
  });

  it("opens the run named in the link", async () => {
    nav.search = "run=3";
    runsApi.mockResolvedValue(runs);
    renderWithQuery(<AgentRunsList />);
    expect((await screen.findAllByText("Reason for failure")).length).toBeGreaterThan(0);
  });

  it("shows an empty state when nothing has run", async () => {
    nav.search = "";
    runsApi.mockResolvedValue([]);
    renderWithQuery(<AgentRunsList />);
    expect((await screen.findAllByText("No agent runs yet.")).length).toBeGreaterThan(0);
    expect(summaryValue("Recorded cost")).toBe("—");
  });

  it("hides server internals when runs cannot load", async () => {
    nav.search = "";
    runsApi.mockRejectedValue(new ApiError(LEAKY_SERVER_ERROR, 500));
    renderWithQuery(<AgentRunsList />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Could not load agent runs. Something went wrong. Please try again.",
    );
    expect(document.body.textContent).not.toContain("sqlalchemy");
  });
});
