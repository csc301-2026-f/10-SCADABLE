# YOUR PRODUCT/TEAM NAME

> Aim for 1000-2000 words. Be concise and analytical. We care about reasoning, impact, and integration quality, not length.

## 1. What Needed to Improve
## 1. What Needed to Improve

Following D2, the most significant limitation of Live SWOT was the reliability and consistency of its end-to-end competitor research workflow. Although users could add competitors, initiate research, and view AI-generated SWOT findings, successful execution did not always produce complete or dependable results. Feature scores were inconsistently generated across competitors, some SWOT findings lacked reliable source attribution, and the overall comparison did not always maintain a consistent perspective between SCADABLE and its competitors. Additionally, failures in the scraping and LLM pipeline could be obscured by fallback behavior, making it difficult to distinguish genuine research from incomplete results. **Another significant limitation was the lack of detailed API cost tracking. The application did not consistently record or display the costs associated with individual AI operations or research runs, making it difficult to evaluate resource consumption and the efficiency of the research pipeline.** Research was also primarily initiated manually, meaning previously generated intelligence could become outdated without further user intervention. These issues directly affected the application's core purpose: providing SCADABLE with trustworthy, up-to-date competitive intelligence. A dashboard that displays incomplete, inconsistently scored, or outdated findings, while offering limited visibility into research costs, cannot reliably support informed competitive analysis or efficient resource management.

We identified these limitations through D2 demonstrations, multi-competitor testing, partner feedback, and our team retrospective. For example, Vanta returned 11 extracted features with 11 feature scores and 7 SWOT findings, while Foresite returned 13 features but no feature scores, and Drata returned 2 features with no feature scores. Partner feedback also highlighted the need for clearer citations, a more intuitive SWOT presentation, and more informative research-run details. **Our team also recognized that the absence of per-operation AI cost tracking limited our ability to monitor API spending and identify expensive stages of the research workflow.** Our retrospective identified late integration between the frontend, backend, scraper, and LLM components, along with insufficient end-to-end testing, as contributing factors. These findings established our D3 priority: improving research reliability, result consistency, freshness, cost transparency, and visibility throughout the existing workflow rather than simply adding unrelated features.

## 2. What You Did

Describe what was implemented, key architectural or design changes, and integration challenges.

## 3. Before vs. After

Show concrete improvements: workflow clarity, speed, error reduction, reliability, etc.

## 4. Reflection

What worked well, what was harder than expected, and how this affects your path to D4.
