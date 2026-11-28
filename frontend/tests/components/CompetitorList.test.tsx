import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { CompetitorList } from "@/components/competitors/CompetitorList";
import { ApiError } from "@/lib/api/client";
import { listCompetitors } from "@/lib/api/competitors";
import { LEAKY_SERVER_ERROR, makeCompetitor, renderWithQuery } from "./helpers";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api/competitors", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/competitors")>()),
  listCompetitors: vi.fn(),
}));

const list = vi.mocked(listCompetitors);

const competitors = [
  makeCompetitor({ id: "1", name: "Acme Compliance", website: "https://acme.example", trend: "up" }),
  makeCompetitor({
    id: "2",
    name: "Vanta Like",
    website: "https://vantalike.example",
    researchStatus: "running",
  }),
];

/** Rows in the desktop table, without the header row. */
function tableRows() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

describe("CompetitorList", () => {
  it("hides server internals when loading fails", async () => {
    list.mockRejectedValue(new ApiError(LEAKY_SERVER_ERROR, 500));
    renderWithQuery(<CompetitorList />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Could not load competitors. Something went wrong. Please try again.",
    );
    expect(document.body.textContent).not.toContain("sqlalchemy");
  });

  it("shows an empty state when there are no competitors", async () => {
    list.mockResolvedValue([]);
    renderWithQuery(<CompetitorList />);
    expect((await screen.findAllByText("No competitors match this view.")).length).toBeGreaterThan(0);
    expect(screen.getByText("0 of 0")).toBeTruthy();
  });

  it("lists competitors with their activity, marking running research as pending", async () => {
    list.mockResolvedValue(competitors);
    renderWithQuery(<CompetitorList />);
    await screen.findByText("2 of 2");

    const [acme, vanta] = tableRows();
    expect(within(acme).getByText("Acme Compliance")).toBeTruthy();
    expect(within(acme).getByText("acme.example")).toBeTruthy();
    expect(within(acme).getByText("↑ More active")).toBeTruthy();
    expect(within(vanta).getByText("— Pending")).toBeTruthy();
  });

  it("filters by research status and by search text", async () => {
    list.mockResolvedValue(competitors);
    renderWithQuery(<CompetitorList />);
    await screen.findByText("2 of 2");

    fireEvent.click(screen.getByRole("button", { name: "Pending" }));
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(within(tableRows()[0]).getByText("Vanta Like")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByLabelText("Search competitors"), { target: { value: "acme" } });
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(within(tableRows()[0]).getByText("Acme Compliance")).toBeTruthy();
  });

  it("opens a competitor's profile when its row is clicked", async () => {
    list.mockResolvedValue(competitors);
    renderWithQuery(<CompetitorList />);
    await screen.findByText("2 of 2");

    fireEvent.click(tableRows()[0]);
    expect(push).toHaveBeenCalledWith("/competitors/1");
  });

  it("opens the add competitor form", async () => {
    list.mockResolvedValue([]);
    renderWithQuery(<CompetitorList />);
    fireEvent.click(await screen.findByRole("button", { name: "Add competitor" }));
    expect(screen.getByRole("dialog", { name: "Add a competitor" })).toBeTruthy();
  });
});
