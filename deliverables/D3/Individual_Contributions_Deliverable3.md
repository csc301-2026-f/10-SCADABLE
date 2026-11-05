# YOUR PRODUCT/TEAM NAME - Individual Contributions (D3)

> For each team member: name, role, and specific contributions. Be precise about what you did, not how much you wrote.

**Essam Elmancy**, Backend API development and data reliability
> - Exposed the LLM's 0–10 feature scores and the research run history through the competitor profile API, so the "Scores vs SCADABLE" and "Activity over time" charts show real data instead of staying empty. Scores carry over from the reused analysis when a re-run is skipped because the website hasn't changed (commit `df9e660`).
> - Made deleting a competitor trigger a background refresh of the overall SWOT, so the overview no longer shows findings about removed competitors (commit `65d719a`).
> - Prevented failed or empty LLM comparisons from erasing existing feature scores: scores are only overwritten when the comparison returns valid ones, malformed or out-of-range scores are filtered out, and the profile falls back to the newest run with valid scores ([PR #50](https://github.com/csc301-2026-f/10-SCADABLE/pull/50)).
> - Fixed failed real crawls silently falling back to mock data (seen with Sprinto and Drata on Railway): the research run is now marked failed with "Could not crawl the competitor's website.", and mock pages are only used when `ALLOW_MOCK_CRAWLER` is set for offline development ([PR #62](https://github.com/csc301-2026-f/10-SCADABLE/pull/62)).
> - Added API, crawler, and orchestrator tests for each change, and ran the full backend suite against PostgreSQL in Docker before merging.
> - Reviewed Ibraheem's conflicting-data table ([PR #55](https://github.com/csc301-2026-f/10-SCADABLE/pull/55)), checked the deployed Railway backend's health and recent research runs, and audited the backend for open bugs to share with the team.

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]
