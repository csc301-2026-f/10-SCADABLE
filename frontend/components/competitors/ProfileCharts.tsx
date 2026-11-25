"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartPoint, ScoreSeries } from "@/lib/api";
import { matchedScoreRows } from "@/lib/scores";
import styles from "./CompetitorProfile.module.css";

const COMPETITOR_FILL = "#55466f";
const SCADABLE_FILL = "#4d64b9";
const ACTIVITY_STROKE = "#a64f45";

/** Short axis labels for long research sentences; full text stays in tooltips. */
export function shortenScoreLabel(label: string, maxChars = 26): string {
  let text = label.trim().replace(/\s+/g, " ");
  // Drop leading brand / product preamble before a common verb.
  text = text.replace(
    /^(?:\S+\s+){1,3}(?=offers|provides|supports|includes|enables|allows|is|has)\b/i,
    "",
  );
  if (text.length <= maxChars) return text;
  const truncated = text.slice(0, maxChars - 1);
  const space = truncated.lastIndexOf(" ");
  return `${(space > 8 ? truncated.slice(0, space) : truncated).trimEnd()}…`;
}

/** Comparison chart: only features with both competitor and SCADABLE scores. */
export function ScoresChart({ data }: { data: ScoreSeries[] }) {
  if (!data.length) {
    return <p className={styles.empty}>No feature scores available yet.</p>;
  }

  const matched = matchedScoreRows(data);
  if (!matched.length) {
    return (
      <p className={styles.empty} role="status">
        No matched features found. SCADABLE scores are not available for
        comparison yet.
      </p>
    );
  }

  const omitted = data.length - matched.length;
  const chartHeight = Math.max(240, matched.length * 51 + 60);

  return (
    <>
      {omitted > 0 ? (
        <p className={styles.scoreNotice} role="status">
          Showing {matched.length} matched{" "}
          {matched.length === 1 ? "feature" : "features"} (both sides scored).{" "}
          {omitted} competitor-only {omitted === 1 ? "feature" : "features"}{" "}
          listed under Feature &amp; automation score.
        </p>
      ) : null}
      <div className={styles.chartWrap} style={{ height: chartHeight }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={matched}
            layout="vertical"
            margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
            barCategoryGap="26%"
            accessibilityLayer
          >
            <CartesianGrid stroke="var(--line)" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, 10]}
              ticks={[0, 2, 4, 6, 8, 10]}
              tick={{ fill: "var(--sub)", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "var(--line)" }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={142}
              interval={0}
              tick={{ fill: "var(--sub)", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value: string) => shortenScoreLabel(value)}
            />
            <Tooltip
              labelFormatter={(label) => String(label)}
              contentStyle={{
                fontSize: 11,
                borderRadius: 6,
                border: "1px solid var(--line)",
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar
              dataKey="competitor"
              name="Competitor"
              fill={COMPETITOR_FILL}
              radius={[0, 3, 3, 0]}
            />
            <Bar
              dataKey="scadable"
              name="SCADABLE"
              fill={SCADABLE_FILL}
              radius={[0, 3, 3, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className={styles.screenReaderTable}>
        <caption>
          Matched feature scores out of 10 for this competitor and SCADABLE
        </caption>
        <thead>
          <tr>
            <th scope="col">Feature</th>
            <th scope="col">Competitor</th>
            <th scope="col">SCADABLE</th>
          </tr>
        </thead>
        <tbody>
          {matched.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{row.competitor}/10</td>
              <td>{row.scadable}/10</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function ActivityChart({ data }: { data: ChartPoint[] }) {
  if (!data.length) {
    return <p className={styles.empty}>No activity series yet.</p>;
  }

  return (
    <div className={styles.chartWrap}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "var(--sub)", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "var(--line)" }}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: "var(--sub)", fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={28}
          />
          <Tooltip
            contentStyle={{
              fontSize: 11,
              borderRadius: 6,
              border: "1px solid var(--line)",
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            name="Signals"
            stroke={ACTIVITY_STROKE}
            strokeWidth={2}
            dot={{ r: 3, fill: ACTIVITY_STROKE }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
