# YOUR PRODUCT/TEAM NAME - Individual Contributions (D3)

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

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]

**[Name]**, [Role]
> [Specific contributions]
