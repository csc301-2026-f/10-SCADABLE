export type ResearchStatus = "pending" | "running" | "success" | "failed";

export type Trend = "up" | "flat" | "down";

export type SwotCategory = "strength" | "weakness" | "opportunity" | "threat";

export type SignalSource = "website" | "jobs" | "reviews" | "press";

export type Finding = {
  id: string;
  /** Null for overall SCADABLE SWOT findings that are not tied to one competitor. */
  competitorId: string | null;
  competitorName: string;
  category: SwotCategory;
  text: string;
  sourceUrl: string;
  sourceLabel: string;
  detectedAt: string;
  isNew?: boolean;
  isChanged?: boolean;
};

export type ChartPoint = {
  label: string;
  value: number;
};

export type ScoreSeries = {
  label: string;
  competitor: number;
  scadable: number | null;
};

export type ActivityItem = {
  id: string;
  text: string;
  detectedAt: string;
};

/** One side of a conflicting-data pair (claim text + page/source). */
export type ConflictClaimSide = {
  text: string;
  sourceLabel: string;
  sourceUrl?: string;
};

/**
 * A claim and one or more disagreeing statements from other pages/sources.
 * Shown on competitor profiles only when the list is non-empty.
 */
export type DataConflict = {
  id: string;
  claim: ConflictClaimSide;
  /** One or more conflicting statements; UI bullets when length > 1. */
  conflictsWith: ConflictClaimSide[];
};

export type CompetitorSummary = {
  id: string;
  name: string;
  website: string;
  trend: Trend;
  researchStatus: ResearchStatus;
  lastResearchedAt: string | null;
  recentChangeSummary: string | null;
};

export type CompetitorProfile = CompetitorSummary & {
  summary: string;
  findings: Finding[];
  recentActivity: ActivityItem[];
  openRoles: number | null;
  nextRefreshAt: string | null;
  /** Optional; omit or [] when research found no conflicts. */
  conflictingData?: DataConflict[];
  chartSeries: {
    scores: ScoreSeries[];
    activityOverTime: ChartPoint[];
    signalsBySource: ChartPoint[];
    hiringTrend: ChartPoint[];
  };
};

export type SwotOverview = {
  isStale?: boolean;
  updatedAt: string | null;
  strengths: Finding[];
  weaknesses: Finding[];
  opportunities: Finding[];
  threats: Finding[];
};

export type CompanyProfile = {
  offerings: string[];
  positioningNotes: string[];
  github: {
    connected: boolean;
    repository: string | null;
    trackedFile: string | null;
    statusLabel: string;
    lastSyncedAt: string | null;
    recentChange: string | null;
  };
};

export type RunStep = {
  name: string;
  result: string;
  status: ResearchStatus;
};

export type AgentRun = {
  id: string;
  competitorId: string | null;
  competitorName: string;
  status: ResearchStatus;
  costUsd: number | null;
  stepCount: number;
  cacheHits: number | null;
  cacheMisses: number | null;
  startedAt: string;
  finishedAt: string | null;
  steps: RunStep[];
  pullRequestUrl: string | null;
  /** Present when status is failed; null/omitted otherwise. */
  errorMessage: string | null;
};

export type CreateCompetitorInput = {
  name: string;
  /** Website URL(s). UI sends a single link; backend stores the first as website. */
  dataSources: string[];
};

export type CreateCompetitorResult = {
  competitor: CompetitorSummary;
  queuedRunId: string;
};
