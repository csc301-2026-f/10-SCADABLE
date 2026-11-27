import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { AgentRun, CompetitorSummary, Finding } from "@/lib/api";

/** Render inside a fresh React Query client, with no retries so errors show at once. */
export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const now = () => new Date().toISOString();

/** A server error that leaks internals. The UI must never show this text. */
export const LEAKY_SERVER_ERROR =
  "sqlalchemy.exc.OperationalError: connection refused at db.internal:5432";

export function makeCompetitor(
  overrides: Partial<CompetitorSummary> = {},
): CompetitorSummary {
  return {
    id: "1",
    name: "Acme Compliance",
    website: "https://acme.example",
    trend: "up",
    researchStatus: "success",
    lastResearchedAt: now(),
    recentChangeSummary: null,
    ...overrides,
  };
}

export function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: "1",
    competitorId: "1",
    competitorName: "Acme Compliance",
    status: "success",
    costUsd: 0.05,
    stepCount: 3,
    cacheHits: 2,
    cacheMisses: 1,
    startedAt: now(),
    finishedAt: now(),
    steps: [
      { name: "Crawl competitor website", result: "Success", status: "success" },
      { name: "Analyze with LLM", result: "Success", status: "success" },
      { name: "Save SWOT findings", result: "Success", status: "success" },
    ],
    pullRequestUrl: null,
    errorMessage: null,
    ...overrides,
  };
}

export function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: "f1",
    competitorId: "1",
    competitorName: "Acme Compliance",
    category: "strength",
    text: "SCADABLE automates SOC 2 evidence collection.",
    sourceUrl: "https://acme.example/features",
    sourceLabel: "acme.example/features",
    detectedAt: now(),
    ...overrides,
  };
}
