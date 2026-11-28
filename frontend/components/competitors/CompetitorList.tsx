"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { useSignInNote } from "@/components/ui/SignInNote";
import {
  competitorsApi,
  type CompetitorSummary,
  type ResearchStatus,
  type Trend,
} from "@/lib/api";
import { useDevDashboardBypass } from "@/lib/auth/useDevDashboardBypass";
import { toUserFacingError } from "@/lib/errors";
import { displayWebsite, formatRelativePast } from "@/lib/format";
import { competitorsPollInterval } from "@/lib/polling";
import { AddCompetitorModal } from "./AddCompetitorModal";
import styles from "./CompetitorList.module.css";

type ActivityFilter = "all" | "pending" | Trend;

const FILTERS: { id: ActivityFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "pending", label: "Pending" },
  { id: "up", label: "Active" },
  { id: "flat", label: "Stable" },
  { id: "down", label: "Quiet" },
];

const TREND_LABEL: Record<Trend, string> = {
  up: "↑ More active",
  down: "↓ Less active",
  flat: "— Stable",
};

function isResearchPending(status: ResearchStatus): boolean {
  return status === "pending" || status === "running";
}

function activityLabel(competitor: CompetitorSummary): string {
  if (isResearchPending(competitor.researchStatus)) return "— Pending";
  return TREND_LABEL[competitor.trend];
}

function activityClass(competitor: CompetitorSummary): string {
  if (isResearchPending(competitor.researchStatus)) return styles.trendPending;
  if (competitor.trend === "up") return styles.trendUp;
  if (competitor.trend === "down") return styles.trendDown;
  return styles.trendFlat;
}

function matchesFilter(
  competitor: CompetitorSummary,
  filter: ActivityFilter,
): boolean {
  if (filter === "all") return true;
  if (filter === "pending") return isResearchPending(competitor.researchStatus);
  // Active / Stable / Quiet only apply after research finishes
  if (isResearchPending(competitor.researchStatus)) return false;
  return competitor.trend === filter;
}

function matchesSearch(competitor: CompetitorSummary, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    competitor.name.toLowerCase().includes(q) ||
    displayWebsite(competitor.website).toLowerCase().includes(q)
  );
}

export function CompetitorList() {
  const router = useRouter();
  const readOnly = useDevDashboardBypass();
  const { showSignInNote, signInNote } = useSignInNote();
  const [filter, setFilter] = useState<ActivityFilter>("all");
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["competitors"],
    queryFn: () => competitorsApi.listCompetitors(),
    refetchInterval: (query) => competitorsPollInterval(query.state.data),
  });

  const competitors = useMemo(() => data ?? [], [data]);

  const visible = useMemo(() => {
    const query = search.trim();
    return competitors.filter(
      (c) => matchesFilter(c, filter) && matchesSearch(c, query),
    );
  }, [competitors, filter, search]);

  if (isPending) {
    return <p className={styles.status}>Loading competitors…</p>;
  }

  if (isError) {
    return (
      <p className={styles.status} role="alert">
        Could not load competitors. {toUserFacingError(error)}
      </p>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.pageHead}>
        <div>
          <h1>Competitors</h1>
          <p>
            Everyone you compete with, and how their activity is changing
          </p>
        </div>
        <div className={styles.headActions}>
          <Button
            onClick={() => {
              if (readOnly) {
                showSignInNote("Sign in required to add competitors.");
                return;
              }
              setAddOpen(true);
            }}
            aria-disabled={readOnly || undefined}
            title={readOnly ? "Sign in required to add competitors" : undefined}
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" aria-hidden="true">
              <path d="M12 5v14M5 12h14" strokeLinecap="round" />
            </svg>
            Add competitor
          </Button>
        </div>
      </header>

      {signInNote}
      <AddCompetitorModal open={addOpen} onClose={() => setAddOpen(false)} />

      <div className={styles.listControls}>
        <div className={styles.filterTabs} role="group" aria-label="Filter competitors">
          {FILTERS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              aria-pressed={filter === tab.id}
              onClick={() => setFilter(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className={styles.searchGroup}>
          <label className={styles.searchWrap}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
              <circle cx="11" cy="11" r="6" />
              <path d="m16 16 4 4" strokeLinecap="round" />
            </svg>
            <input
              type="search"
              placeholder="Search by name..."
              aria-label="Search competitors"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <span className={styles.resultCount}>
            {visible.length} of {competitors.length}
          </span>
        </div>
      </div>

      {/* Desktop / tablet: table */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.colName}>Name</th>
              <th className={styles.colWebsite}>Website</th>
              <th className={styles.colRelationship}>Relationship</th>
              <th className={styles.colActivity}>Activity</th>
              <th className={styles.colChecked}>Last checked</th>
              <th className={styles.colArrow} aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr className={styles.emptyRow}>
                <td colSpan={6}>No competitors match this view.</td>
              </tr>
            ) : (
              visible.map((c) => (
                <tr
                  key={c.id}
                  className={styles.clickable}
                  tabIndex={0}
                  onClick={() => router.push(`/competitors/${c.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(`/competitors/${c.id}`);
                    }
                  }}
                >
                  <td className={styles.companyCell}>{c.name}</td>
                  <td className={styles.websiteCell}>{displayWebsite(c.website)}</td>
                  <td>
                    <span className={styles.relationship}>Competitor</span>
                  </td>
                  <td>
                    <span className={`${styles.trend} ${activityClass(c)}`}>
                      {activityLabel(c)}
                    </span>
                  </td>
                  <td className={styles.lastChecked}>
                    {formatRelativePast(c.lastResearchedAt)}
                  </td>
                  <td className={styles.rowArrow} aria-hidden="true">
                    <svg
                      viewBox="0 0 24 24"
                      width="13"
                      height="13"
                      fill="none"
                      stroke="currentColor"
                    >
                      <path
                        d="m9 5 7 7-7 7"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile: card list (table is hidden under 768px) */}
      <ul className={styles.cards}>
        {visible.length === 0 ? (
          <li className={styles.cardsEmpty}>No competitors match this view.</li>
        ) : (
          visible.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={styles.card}
                onClick={() => router.push(`/competitors/${c.id}`)}
              >
                <span className={styles.cardTop}>
                  <span className={styles.cardName}>{c.name}</span>
                  <span className={`${styles.trend} ${activityClass(c)}`}>
                    {activityLabel(c)}
                  </span>
                </span>
                <span className={styles.cardSite}>
                  {displayWebsite(c.website)}
                </span>
                <span className={styles.cardMeta}>
                  Checked {formatRelativePast(c.lastResearchedAt)}
                </span>
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
