# Live SWOT - Individual Contributions (D3)

> For each team member: name, role, and specific contributions. Be precise about what you did, not how much you wrote.

### Jaynik Sheth

**Role:** Scraper optimization, Decisions API tracking, and research pipeline integration.

**Contributions:**

- Implemented optional Langfuse tracking for scraper Decisions API attempts, recording token usage, outcomes, and crawl references without exposing page content or allowing tracking failures to interrupt scraping (`decisions-api-cost-tracking` branch; [PR #57](https://github.com/csc301-2026-f/10-SCADABLE/pull/57)).
- Aligned the scraper's research objective and usefulness rubrics with downstream extraction, and added approximate coverage hints that update candidate scoring based on useful pages already collected (`d3_scraper_optimizations` branch; [PR #58](https://github.com/csc301-2026-f/10-SCADABLE/pull/58)).
- Added scraper tests, lint, and formatting checks (CI) to GitHub Actions
- Answered Q2 in `product-evolution.md` doc

### Essam Elmancy

**Role:** Backend API development and data reliability.

**Contributions:**
- Connected the LLM feature scores and research run history to the competitor profile API so the profile charts show real data (`essam_d2_backend_2` branch).
- Made deleting a competitor refresh the overall SWOT so the overview no longer shows removed competitors (`essam_d2_backend_2` branch).
- Kept existing feature scores when an LLM comparison fails or returns nothing, and filtered out invalid scores ([PR #50](https://github.com/csc301-2026-f/10-SCADABLE/pull/50)).
- Made failed website crawls mark the research run as failed instead of silently using mock data ([PR #62](https://github.com/csc301-2026-f/10-SCADABLE/pull/62)).
- Wrote API, crawler, and orchestrator tests for each change.

**[Yash Khanchandani]**, [AI analysis]
- Optimized prompts for company data extraction and SWOT analysis.
- Added OpenAI API call cost tracking using Langfuse (https://github.com/csc301-2026-f/10-SCADABLE/issues/53)
- Added "conflicting data" flagging to LLM outputs to let the user decide which claim to believe in (https://github.com/csc301-2026-f/10-SCADABLE/issues/67).

### Ibraheem Hussain

**Role:** Frontend development (accessibility, competitor profiles, agent runs).

**Contributions:**
- Improved frontend accessibility and client-side security for D3 NFRs: focus traps on dialogs, accessible Add Competitor validation/errors, keyboard chart tabs, sanitized UI/API error messages, and documented Lighthouse a11y evidence in the root README ([PR #47](https://github.com/csc301-2026-f/10-SCADABLE/pull/47)).
- Built the optional Conflicting data table on competitor profiles (Claim vs Conflicts with, bullets for multiple conflicts); shown only when `conflictingData` is present ([PR #55](https://github.com/csc301-2026-f/10-SCADABLE/pull/55)).
- Hid the unfinished GitHub sync block on Our Company while keeping the code commented for later ([PR #66](https://github.com/csc301-2026-f/10-SCADABLE/pull/66)).
- Surfaced backend/Langfuse `costUsd` on the Agent Runs page (per-run Cost column and weekly Estimated cost) ([PR #70](https://github.com/csc301-2026-f/10-SCADABLE/pull/70)).
- Reviewed the D3 product evolution report draft ([PR #65](https://github.com/csc301-2026-f/10-SCADABLE/pull/65)).

## Sukhmandeep Gill

**Role:** Backend development, CI/CD infrastructure, reliability improvements, and D3 documentation.

**Contributions:**

- Implemented the GitHub Actions CI pipeline to automatically run backend tests and frontend ESLint checks on pull requests, with five-minute job timeouts. Fixed frontend lint errors, sanitized internal research-run errors exposed through API responses, and added regression tests and testing documentation ([PR #49](https://github.com/csc301-2026-f/10-SCADABLE/pull/49)).

- Improved overall SWOT reliability by tracking stale results after failed comparisons, updating the backend API and frontend to handle outdated findings, and adding tests for comparison failures and successful retries. Addressed teammate review feedback concerning score preservation, concurrent refreshes, database errors, and freshness tracking ([PR #54](https://github.com/csc301-2026-f/10-SCADABLE/pull/54)).

- Extended the backend API to support SCADABLE feature scores alongside competitor scores. Implemented competitor refresh scheduling with configurable intervals, duplicate-run prevention, retry handling, and restart recovery. Added Open Roles storage and API integration, along with scheduling and API tests. Automatic refresh execution remains disabled for D3 ([PR #63](https://github.com/csc301-2026-f/10-SCADABLE/pull/63)).

- Improved overall SWOT comparison handling by introducing configurable LLM comparison timeouts, adding frontend polling for stale SWOT results, and extending feature-score polling to accommodate longer comparison runs ([PR #68](https://github.com/csc301-2026-f/10-SCADABLE/pull/68)).

- Completed Question 1 of the D3 Product Evolution Report using the provided template, documenting the key reliability and consistency gaps identified after D2 and the feedback that motivated improvements ([PR #65](https://github.com/csc301-2026-f/10-SCADABLE/pull/65)).

- Updated the D3 README with application workflows, deployment links, local setup and testing instructions, known limitations, and screenshots demonstrating the application, GitHub Actions CI, Railway/Vercel automatic deployments, and accessibility results (`docs/d3-readme-onboarding` branch).

- Reviewed and merged four teammates' pull requests: [PR #51](https://github.com/csc301-2026-f/10-SCADABLE/pull/51), [PR #52](https://github.com/csc301-2026-f/10-SCADABLE/pull/52), [PR #62](https://github.com/csc301-2026-f/10-SCADABLE/pull/62), and [PR #64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64), contributing to code review, team integration, and maintaining the shared `main` branch.

### Houssam Yaacoub

**Role:** Backend research pipeline, LLM cost tracking, and CI test coverage.

**Contributions:**

- Made the overall comparison score every competitor that has features, even when the LLM returned no strengths or weaknesses for it, by using each competitor's latest complete run instead of only runs with SWOT entries ([PR #52](https://github.com/csc301-2026-f/10-SCADABLE/pull/52)).
- Built cost tracking for each research run: named every LLM call by run and company in Langfuse (for example `run-42:vanta:extraction`), looked up the cost of each run a few minutes after it finished, saved it with the run, and returned it in the runs API as `costUsd`. Fixed a bug where only the first run's cost was recorded ([PR #59](https://github.com/csc301-2026-f/10-SCADABLE/pull/59)).
- Updated the backend LLM adapter for the new analyzer schema, and made CI install the local `llm` package so the adapter and cost tests run on every pull request without spending OpenAI tokens ([PR #59](https://github.com/csc301-2026-f/10-SCADABLE/pull/59)).
- Fixed failed runs showing "Analyze with LLM: Success" after their cost was saved, and added a regression test for a failed crawl ([PR #69](https://github.com/csc301-2026-f/10-SCADABLE/pull/69)).
- Answered Question 3 (Before vs. After) in `product-evolution.md`.

## Saad Qureshi

**Role:** Frontend development (competitor scoring, refresh scheduling, and open roles UI), accessibility, and backend PR review, Team coordinator.

**Contributions:**

- Built the Scores vs SCADABLE chart (`ScoresChart`) on competitor profiles: a horizontal bar chart comparing each feature's competitor and SCADABLE scores out of 10, with truncated long labels and a height that adjusts to the number of features. It replaces the "coming in D3" placeholder, and Scores is now the default tab ([PR #64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64)).
- Added graceful handling when SCADABLE scores are unavailable: the chart shows a status notice and still displays competitor scores.
- Made the chart accessible by adding a screen-reader-only data table and ARIA tab/panel labelling, so chart data is available without a duplicate visible table.
- Updated profile polling so the Scores chart appears automatically after a research run completes. The page polls for a short window after completion and stops for older, permanently unscored profiles.
- Integrated the backend's Open Roles data into competitor profiles, replacing the placeholder with the role count and an "Not available" fallback ([PR #64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64)).
- Integrated scheduled refreshes into competitor profiles. "Next refresh" now shows a readable date from the API (new `formatRefreshDate` helper) and "Not scheduled" when none is set ([PR #64](https://github.com/csc301-2026-f/10-SCADABLE/pull/64)).
- Reviewed and approved the backend changes for refresh scheduling, SCADABLE scoring API support, and Open Roles integration, then merged them to main ([PR #63](https://github.com/csc301-2026-f/10-SCADABLE/pull/63)).
