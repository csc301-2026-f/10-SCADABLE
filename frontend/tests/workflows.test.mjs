import assert from "node:assert/strict";
import test from "node:test";
import client from "../.test-build/lib/api/client.js";
import competitors from "../.test-build/lib/api/competitors.js";
import swot from "../.test-build/lib/api/swot.js";
import runsApi from "../.test-build/lib/api/runs.js";
import validation from "../.test-build/lib/validation/competitor.js";
import errors from "../.test-build/lib/errors.js";
import polling from "../.test-build/lib/polling.js";
import stats from "../.test-build/lib/runs/stats.js";
import scores from "../.test-build/lib/scores.js";
import format from "../.test-build/lib/format.js";

process.env.NEXT_PUBLIC_USE_MOCKS = "false";

const now = Date.now();
const ago = (minutes) => new Date(now - minutes * 60_000).toISOString();
const run = (overrides = {}) => ({
  id: "1", competitorId: "2", competitorName: "Example", status: "success",
  costUsd: null, cacheHits: null, cacheMisses: null, startedAt: ago(2),
  finishedAt: ago(1), stepCount: 3, steps: [], ...overrides,
});

test("competitor form validates and normalizes real HTTP(S) websites", () => {
  assert.deepEqual(validation.validateCompetitorForm("Vanta", "vanta.com"), {});
  assert.equal(validation.normalizeWebsiteInput(" vanta.com "), "https://vanta.com/");
  for (const bad of ["", "javascript:alert(1)", "ftp://example.com", "https://user:pass@example.com", "localhost"]) {
    assert.equal(validation.normalizeWebsiteInput(bad), null);
  }
  assert.ok(validation.validateCompetitorForm("", "").name);
  assert.ok(validation.validateCompetitorForm("x".repeat(256), "example.com").name);
});

test("real API is the default; fixtures require explicit opt-in", (t) => {
  const old = process.env.NEXT_PUBLIC_USE_MOCKS;
  t.after(() => old === undefined ? delete process.env.NEXT_PUBLIC_USE_MOCKS : process.env.NEXT_PUBLIC_USE_MOCKS = old);
  delete process.env.NEXT_PUBLIC_USE_MOCKS;
  assert.equal(client.shouldUseMocks(), false);
  process.env.NEXT_PUBLIC_USE_MOCKS = "false";
  assert.equal(client.shouldUseMocks(), false);
  process.env.NEXT_PUBLIC_USE_MOCKS = "true";
  assert.equal(client.shouldUseMocks(), true);
});

test("real profile returns saved conflict claims and preserves missing comparison scores", async (t) => {
  const old = process.env.NEXT_PUBLIC_USE_MOCKS;
  process.env.NEXT_PUBLIC_USE_MOCKS = "false";
  t.after(() => old === undefined ? delete process.env.NEXT_PUBLIC_USE_MOCKS : process.env.NEXT_PUBLIC_USE_MOCKS = old);
  const profile = { id: "2", conflictingData: [{ id: "c1", claim: { text: "100 customers", sourceUrl: "https://example.com/about" }, conflictsWith: [{ text: "200 customers", sourceUrl: "https://example.com/pricing" }] }], chartSeries: { scores: [{ label: "Evidence", competitor: 8, scadable: null }] } };
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.ok(String(url).endsWith("/competitors/2"));
    return Response.json(profile);
  });
  assert.deepEqual(await competitors.getCompetitor("2"), profile);
});

test("overall SWOT retains published findings when API reports stale", async (t) => {
  const snapshot = { isStale: true, updatedAt: ago(60), strengths: [{ id: "s1" }], weaknesses: [], opportunities: [], threats: [] };
  t.mock.method(globalThis, "fetch", async () => Response.json(snapshot));
  assert.deepEqual(await swot.getSwotOverview(), snapshot);
});

test("Agent Runs returns pending cost without manufacturing a zero", async (t) => {
  const list = [run()];
  t.mock.method(globalThis, "fetch", async () => Response.json(list));
  assert.deepEqual(await runsApi.listRuns(), list);
  assert.equal(stats.formatRunCost(null), "—");
  assert.equal(stats.formatRunCost(0), "$0.00");
  assert.equal(stats.formatRunCost(0.0087), "$0.0087");
});

test("API wrapper hides server internals and supports empty DELETE responses", async (t) => {
  const fetchMock = t.mock.method(globalThis, "fetch", async () => new Response("password=secret", { status: 500 }));
  await assert.rejects(client.apiFetch("/runs"), (err) => err instanceof client.ApiError && err.status === 500 && !err.message.includes("secret"));
  fetchMock.mock.mockImplementation(async () => new Response(null, { status: 204 }));
  assert.equal(await client.apiFetch("/competitors/2", { method: "DELETE" }), undefined);
  assert.equal(errors.toUserFacingError(new client.ApiError('{"detail":"Already exists."}', 409)), "Already exists.");
  assert.equal(errors.toUserFacingRunError("Traceback: password=secret"), "Research run failed.");
});

test("run polling covers active research and delayed costs, then stops", () => {
  assert.equal(polling.runsPollInterval([run({ status: "running", finishedAt: null })]), 3000);
  assert.equal(polling.runsPollInterval([run()]), 30_000);
  assert.equal(polling.runsPollInterval([run({ costUsd: 0 })]), false);
  assert.equal(polling.runsPollInterval([run({ finishedAt: ago(21) })]), false);
  assert.equal(polling.runsPollInterval([]), false);
});

test("profile polling permits delayed comparisons even when old scores exist", () => {
  const profile = { researchStatus: "success", lastResearchedAt: ago(1), chartSeries: { scores: [{ label: "Old score" }] } };
  assert.equal(polling.profilePollInterval(profile), 10_000);
  assert.equal(polling.profilePollInterval({ ...profile, lastResearchedAt: ago(11) }), false);
  assert.equal(polling.profilePollInterval({ ...profile, researchStatus: "running" }), 3000);
  assert.equal(polling.competitorsPollInterval([{ researchStatus: "pending" }]), 3000);
  assert.equal(polling.competitorsPollInterval([{ researchStatus: "success" }]), false);
});

test("weekly totals distinguish unknown cache rate from measured 0%", () => {
  assert.equal(stats.computeWeeklyRunStats([run()]).cacheHitRatePct, null);
  assert.equal(stats.computeWeeklyRunStats([run({ cacheHits: 0, cacheMisses: 1 })]).cacheHitRatePct, 0);
  const totals = stats.computeWeeklyRunStats([run({ costUsd: 0.01, cacheHits: 1, cacheMisses: 0 }), run({ id: "2", cacheHits: 0, cacheMisses: 1 }), run({ startedAt: ago(8 * 24 * 60), costUsd: 100 })]);
  assert.equal(totals.cacheHitRatePct, 50);
  assert.equal(totals.estimatedCostUsd, 0.01);
  assert.equal(totals.runsThisWeek, 2);
});

test("latest run selection ignores pending and retains prior success after cancellation", () => {
  const success = run({ startedAt: ago(3) });
  const cancelled = run({ id: "2", status: "cancelled", startedAt: ago(2) });
  const pending = run({ id: "3", status: "pending", startedAt: ago(1) });
  assert.equal(stats.latestRunPerCompetitor([pending, success, cancelled])[0].id, "2");
  assert.equal(stats.latestSuccessfulRunForCompetitor([cancelled, pending, success], "2").id, "1");
});

test("invalid dates do not appear as NaN in the dashboard", () => {
  assert.equal(format.formatRelativePast("invalid"), "Not checked");
  assert.equal(format.formatRelativeFuture("invalid"), "Not scheduled");
});


test("comparison chart includes only valid matched ratings, including real zero", () => {
  const rows = [
    { label: "Matched", competitor: 7, scadable: 8 },
    { label: "Unmatched", competitor: 10, scadable: null },
    { label: "Zero", competitor: 6, scadable: 0 },
    { label: "Invalid", competitor: 7, scadable: 11 },
  ];
  assert.deepEqual(scores.matchedScoreRows(rows), [rows[0], rows[2]]);
});
