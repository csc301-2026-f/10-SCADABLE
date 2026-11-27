import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { AddCompetitorModal } from "@/components/competitors/AddCompetitorModal";
import { ApiError } from "@/lib/api/client";
import { createCompetitor } from "@/lib/api/competitors";
import { LEAKY_SERVER_ERROR, makeCompetitor, renderWithQuery } from "./helpers";

vi.mock("@/lib/api/competitors", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/competitors")>()),
  createCompetitor: vi.fn(),
}));

const create = vi.mocked(createCompetitor);

function fillAndSubmit(name: string, website: string) {
  fireEvent.change(screen.getByLabelText("Company name"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Website"), { target: { value: website } });
  fireEvent.click(screen.getByRole("button", { name: "Add & queue research" }));
}

describe("AddCompetitorModal", () => {
  it("flags missing fields without calling the API", () => {
    renderWithQuery(<AddCompetitorModal open onClose={vi.fn()} />);
    fillAndSubmit("", "");

    expect(screen.getByText("Company name is required.")).toBeTruthy();
    expect(screen.getByText("Website link is required.")).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("rejects a website that is not a real http(s) address", () => {
    renderWithQuery(<AddCompetitorModal open onClose={vi.fn()} />);
    fillAndSubmit("Acme", "localhost");

    expect(screen.getByText(/Enter a valid http\(s\) website/)).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it("sends a normalized website, closes and confirms the competitor was queued", async () => {
    create.mockResolvedValue({
      competitor: makeCompetitor({ name: "Acme", researchStatus: "pending" }),
      queuedRunId: "7",
    });
    const onClose = vi.fn();
    renderWithQuery(<AddCompetitorModal open onClose={onClose} />);
    fillAndSubmit("  Acme ", "acme.example");

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toEqual({
      name: "Acme",
      dataSources: ["https://acme.example/"],
    });
    expect(await screen.findByText(/Acme added/)).toBeTruthy();
  });

  it("shows the backend's reason when a competitor is rejected", async () => {
    create.mockRejectedValue(
      new ApiError(JSON.stringify({ detail: "A competitor with this website already exists." }), 409),
    );
    const onClose = vi.fn();
    renderWithQuery(<AddCompetitorModal open onClose={onClose} />);
    fillAndSubmit("Acme", "acme.example");

    expect(await screen.findByText("A competitor with this website already exists.")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("hides server internals behind a friendly message", async () => {
    create.mockRejectedValue(new ApiError(LEAKY_SERVER_ERROR, 500));
    renderWithQuery(<AddCompetitorModal open onClose={vi.fn()} />);
    fillAndSubmit("Acme", "acme.example");

    expect(await screen.findByText("Could not add competitor. Please try again.")).toBeTruthy();
    expect(document.body.textContent).not.toContain("sqlalchemy");
  });
});
