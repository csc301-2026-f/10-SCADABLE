# Live SWOT - Individual Contributions (D3)

> For each team member: name, role, and specific contributions. Be precise about what you did, not how much you wrote.

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

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]
