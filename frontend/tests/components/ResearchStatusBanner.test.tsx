import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ResearchStatusBanner } from "@/components/competitors/ResearchStatusBanner";

describe("ResearchStatusBanner", () => {
  it("shows nothing once research succeeds", () => {
    const { container } = render(
      <ResearchStatusBanner status="success" competitorName="Acme" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows a queued run with a working Cancel button", () => {
    const onCancel = vi.fn();
    render(
      <ResearchStatusBanner status="pending" competitorName="Acme" onCancel={onCancel} />,
    );

    expect(screen.getByText("Agent research queued")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("hides Cancel when no cancel handler is given", () => {
    render(<ResearchStatusBanner status="running" competitorName="Acme" />);
    expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull();
  });

  it("disables Cancel while cancelling and shows a cancel error", () => {
    render(
      <ResearchStatusBanner
        status="running"
        competitorName="Acme"
        onCancel={vi.fn()}
        cancelling
        cancelError="Research run 5 already finished."
      />,
    );

    const button = screen.getByRole("button", { name: "Cancelling…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe("Research run 5 already finished.");
  });

  it("offers a rerun after a failed run", () => {
    const onRerun = vi.fn();
    render(<ResearchStatusBanner status="failed" competitorName="Acme" onRerun={onRerun} />);

    expect(screen.getByText("Agent run failed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Rerun agent" }));
    expect(onRerun).toHaveBeenCalledTimes(1);
  });

  it("offers a rerun after a cancelled run", () => {
    const onRerun = vi.fn();
    render(<ResearchStatusBanner status="cancelled" competitorName="Acme" onRerun={onRerun} />);

    expect(screen.getByText("Agent run cancelled")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Rerun agent" }));
    expect(onRerun).toHaveBeenCalledTimes(1);
  });

  it("disables Rerun while a rerun is being queued", () => {
    render(
      <ResearchStatusBanner status="failed" competitorName="Acme" onRerun={vi.fn()} rerunning />,
    );
    const button = screen.getByRole("button", { name: "Rerunning…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
