
"use client";

import { useId, useRef, useState, type KeyboardEvent } from "react";
import type { CompetitorProfile } from "@/lib/api";
import { ActivityChart, ScoresChart } from "./ProfileCharts";
import styles from "./CompetitorProfile.module.css";

type VisualId = "scores" | "activity";

const VISUALS: { id: VisualId; title: string }[] = [
  { id: "scores", title: "Scores vs SCADABLE" },
  { id: "activity", title: "Activity over time" },
];

export function ProfileVisuals({
  chartSeries,
}: {
  chartSeries: CompetitorProfile["chartSeries"];
}) {
  const baseId = useId();
  const [active, setActive] = useState<VisualId>("scores");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function selectTab(id: VisualId, focus = false) {
    setActive(id);
    if (!focus) return;
    const index = VISUALS.findIndex((tab) => tab.id === id);
    tabRefs.current[index]?.focus();
  }

  function onTabKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") {
      return;
    }
    e.preventDefault();
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % VISUALS.length;
    if (e.key === "ArrowLeft") next = (index - 1 + VISUALS.length) % VISUALS.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = VISUALS.length - 1;
    selectTab(VISUALS[next].id, true);
  }

  const activeMeta = VISUALS.find((v) => v.id === active) ?? VISUALS[0];
  const panelId = `baseId-panel-{activeMeta.id}`;

  return (
    <section className={styles.card}>
      <h2>Visuals</h2>

      <div className={styles.visualTabs} role="tablist" aria-label="Profile charts">
        {VISUALS.map((tab, index) => {
          const selected = active === tab.id;
          const tabId = `baseId-tab-{tab.id}`;
          return (
            <button
              key={tab.id}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              id={tabId}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`baseId-panel-{tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? styles.visualTabActive : undefined}
              onClick={() => selectTab(tab.id)}
              onKeyDown={(e) => onTabKeyDown(e, index)}
            >
              {tab.title}
            </button>
          );
        })}
      </div>

      <div
        id={panelId}
        role="tabpanel"
        aria-labelledby={`baseId-tab-{activeMeta.id}`}
      >
        {active === "scores" ? (
          <ScoresChart data={chartSeries.scores} />
        ) : (
          <ActivityChart data={chartSeries.activityOverTime} />
        )}
      </div>
    </section>
  );
}


