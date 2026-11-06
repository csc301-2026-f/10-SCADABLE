"""LLM synthesis interface, adapters, and realistic mock fixtures.

Enables the research orchestrator to invoke OpenAI-based competitor SWOT
comparison against SCADABLE's baseline context, or fall back to high-fidelity
mock analysis during offline testing.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.services.open_roles import count_open_roles, normalize_job_postings

logger = logging.getLogger(__name__)


class AnalyzerProtocol(Protocol):
    """Protocol defining the analysis interface expected by the orchestrator."""

    async def analyze(
        self,
        scraped_data: dict[str, Any],
        baseline_context: dict[str, Any],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]: ...

    async def compare(
        self,
        baseline_context: dict[str, Any],
        competitors: list[dict[str, Any]],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]: ...


def _comparison_time() -> str:
    """Stands in for SCADABLE's crawl time, which the backend never has (static baseline)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _pick_source_url(urls: list[str], scadable_url: str) -> str | None:
    """Pick the one source URL saved for an overall finding.

    The database keeps one URL per finding. A scraped competitor page is
    preferred because SCADABLE's own claims only cite its homepage.
    """
    for url in urls:
        if url != scadable_url:
            return url
    return urls[0] if urls else None


def _build_openai_client() -> Any:
    """Create the shared OpenAI client; raises if the package or key is missing."""
    from llm.client import OpenAIClient
    from llm.config import Settings
    from scraper.environment import load_environment

    load_environment()
    settings = Settings()
    if not settings.openai_model.strip():
        # Same model the scraper uses for page selection
        settings = settings.model_copy(update={"openai_model": "gpt-6-luna"})
    return OpenAIClient(settings)


class MockOpenAIAnalyzer:
    """Mock analyzer returning realistic SWOT findings with citations.

    Produces structured compliance SWOT findings comparing competitor capabilities
    against SCADABLE's static baseline profile.
    """

    def __init__(self, should_fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.should_fail = should_fail
        self.delay_seconds = delay_seconds

    async def analyze(
        self,
        scraped_data: dict[str, Any],
        baseline_context: dict[str, Any],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.should_fail:
            raise RuntimeError(
                "Mock analyzer simulated failure during OpenAI SWOT synthesis."
            )

        competitor_name = scraped_data.get("competitor_name", "Competitor")
        base_url = scraped_data.get("base_url", "https://example.com")
        features_url = f"{base_url}/features"
        pricing_url = f"{base_url}/pricing"
        docs_url = f"{base_url}/docs"

        return {
            "competitor_name": competitor_name,
            "summary": (
                f"{competitor_name} is an established compliance automation platform "
                "supporting SOC 2, ISO 27001, and HIPAA with wide cloud integration coverage."
            ),
            "features": [
                "Automated evidence collection",
                "Pre-built cloud integrations",
                "Auditor partner network",
            ],
            "swot_entries": [
                {
                    "category": "strength",
                    "content": (
                        f"{competitor_name} has broad pre-built integrations with major "
                        "cloud providers and common developer tools."
                    ),
                    "source_url": features_url,
                },
                {
                    "category": "strength",
                    "content": (
                        f"Established market presence and strong auditor partner network "
                        "for fast SOC 2 Type II sign-offs."
                    ),
                    "source_url": features_url,
                },
                {
                    "category": "weakness",
                    "content": (
                        f"High annual starting pricing and enterprise-heavy tiers ($7.5k to $25k+) "
                        "create friction for early-stage startups."
                    ),
                    "source_url": pricing_url,
                },
                {
                    "category": "weakness",
                    "content": (
                        f"{competitor_name} lacks automated code remediation and native EU Cyber "
                        "Resilience Act (CRA) workflows."
                    ),
                    "source_url": docs_url,
                },
                {
                    "category": "opportunity",
                    "content": (
                        "SCADABLE can position itself as the cost-effective developer alternative "
                        "with direct code fixes and European regulatory coverage."
                    ),
                    "source_url": pricing_url,
                },
                {
                    "category": "threat",
                    "content": (
                        f"Large competitor marketing budgets and entrenched partner relationships "
                        "raise customer acquisition hurdles."
                    ),
                    "source_url": features_url,
                },
            ],
        }

    async def compare(
        self,
        baseline_context: dict[str, Any],
        competitors: list[dict[str, Any]],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.should_fail:
            raise RuntimeError(
                "Mock analyzer simulated failure during overall SWOT comparison."
            )

        names = ", ".join(c["company_name"] for c in competitors) or "competitors"
        strengths = [
            {"category": "strength", "content": text, "source_url": None}
            for text in baseline_context.get("differentiators", [])[:2]
        ]
        return {
            "swot_entries": strengths + [
                {
                    "category": "weakness",
                    "content": f"SCADABLE has less brand recognition than {names}.",
                    "source_url": None,
                },
                {
                    "category": "opportunity",
                    "content": (
                        f"Win cost-sensitive startups priced out of enterprise tiers from {names}."
                    ),
                    "source_url": None,
                },
                {
                    "category": "threat",
                    "content": (
                        f"{names} can use larger partner networks to slow SCADABLE's growth."
                    ),
                    "source_url": None,
                },
            ],
            "scores": [
                {"company_name": c["company_name"], "feature": feature, "score": 7}
                for c in competitors
                for feature in c.get("features", [])
            ],
        }


class OpenAIAdapter:
    """Adapter wrapping Yash & Jaynik's OpenAI analysis engine.

    Delegates to the LLM analyzer, using mock data only when explicitly enabled.
    """

    def __init__(self) -> None:
        self._underlying: Any = None

    async def analyze(
        self,
        scraped_data: dict[str, Any],
        baseline_context: dict[str, Any],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]:
        # Names of paid OpenAI calls started, kept even if we fall back to the mock
        llm_calls: list[str] = []
        try:
            from llm.analyzer import Analyzer
            from llm.inputs import load_analysis_input
            from llm.schemas import (
                AnalysisInput,
                AnalysisPage,
                CrawlExportSummary,
                ExportedPage,
            )
            from llm.swot_entries import to_swot_entries
            from scraper.models import CrawlSummary
            from scraper.types import PageCategory

            # Check if summary_path exists on disk
            summary_path = scraped_data.get("summary_path")
            if summary_path and Path(summary_path).exists():
                analysis_input = await asyncio.to_thread(
                    load_analysis_input, summary_path
                )
            else:
                # Construct AnalysisInput from in-memory scraped_data snapshot
                valid_categories = {c.value for c in PageCategory}
                analysis_pages = []
                for idx, p in enumerate(scraped_data.get("pages", [])):
                    url_str = p.get("url", "")
                    body_md = p.get("text") or p.get("markdown") or ""
                    if not url_str or not body_md.strip():
                        continue
                    raw_cat = p.get("category", "features")
                    cat = raw_cat if raw_cat in valid_categories else "other"
                    exported = ExportedPage(
                        url=url_str,
                        url_path=urlsplit(url_str).path or "/",
                        markdown_file=f"pages/page_{idx}.md",
                        title=p.get("title"),
                        category=cat,
                        word_count=len(body_md.split()),
                        crawled_at=datetime.now(timezone.utc),
                        status="success",
                    )
                    analysis_pages.append(
                        AnalysisPage(
                            source=exported,
                            markdown=body_md,
                        )
                    )
                if not analysis_pages:
                    raise ValueError("No usable scraped pages found in scraped_data.")

                raw_base = scraped_data.get("base_url", "https://example.com")
                base_parts = urlsplit(raw_base)
                homepage_url = (
                    f"{base_parts.scheme}://{base_parts.netloc}"
                    if base_parts.scheme and base_parts.netloc
                    else "https://example.com"
                )

                now = datetime.now(timezone.utc)
                export_summary = CrawlExportSummary(
                    competitor_name=scraped_data.get("competitor_name", "Unknown"),
                    homepage_url=homepage_url,
                    started_at=now,
                    finished_at=now,
                    duration_seconds=1.0,
                    status="success",
                    completion_reason="completed",
                    counts=CrawlSummary(
                        successful=len(analysis_pages),
                        failed=0,
                        discovered=len(analysis_pages),
                    ),
                    pages=[ap.source for ap in analysis_pages],
                )
                analysis_input = AnalysisInput(
                    crawl_summary=export_summary,
                    pages=analysis_pages,
                )

            client = _build_openai_client()
            try:
                analyzer = Analyzer(client, trace_prefix=trace_prefix)
                if trace_prefix:
                    llm_calls.append(f"{trace_prefix}:extraction")
                facts = await analyzer.extract_information(analysis_input)
            finally:
                await client.close()

            # Stage 1 now extracts cited facts only. Stage 2 generates SCADABLE's
            # SWOT and scores during refresh_overall_swot(), not here.
            job_postings = normalize_job_postings(facts.job_postings)
            features = list(dict.fromkeys(
                claim.claim for claim in facts.claims_cited
                if claim.category == "feature" and claim.claim.strip()
            ))
            return {
                "schema_version": "backend-analysis-v2",
                "mode": "real",
                "perspective": "competitor_facts",
                "competitor_name": scraped_data.get("competitor_name") or facts.company_name,
                "summary": facts.summary,
                "features": features,
                # Persist the complete source-backed extraction for later overall
                # comparisons. Do not reduce the claims to just SWOT rows.
                "extracted_information": facts.model_dump(mode="json"),
                "conflicting_data": facts.conflicting_data.model_dump(mode="json"),
                "job_postings": job_postings,
                "open_roles": count_open_roles(job_postings),
                "swot_entries": to_swot_entries(facts),
                "llm_calls": llm_calls,
            }

        except Exception as exc:
            if os.getenv("ALLOW_MOCK_LLM", "").lower() not in {"1", "true", "yes"}:
                # A real run must not be marked successful with fictitious data.
                logger.exception("Real OpenAI extraction failed")
                raise
            logger.warning(
                "Real OpenAI extraction failed (%s); ALLOW_MOCK_LLM enabled, using mock data.", exc
            )
            mock = MockOpenAIAnalyzer()
            result = await mock.analyze(scraped_data, baseline_context)
            result["mode"] = "mock"
            if llm_calls:
                result["llm_calls"] = llm_calls
            return result

    async def compare(
        self,
        baseline_context: dict[str, Any],
        competitors: list[dict[str, Any]],
        trace_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Run stage-2 SWOT comparison using the stored stage-1 extractions.

        Legacy stored profiles are converted for compatibility. Real comparison
        errors are not silently replaced with mock results in production.
        """
        from llm.analyzer import Analyzer
        from llm.schemas import (
            ClaimCitedByURL,
            ComparisonInput,
            ConflictEntry,
            ExtractedInformationByURL,
        )

        # SCADABLE is a configured baseline, not a crawled company. Each static
        # capability is cited to the configured baseline URL for this comparison.
        scadable_url = baseline_context.get("website_url") or "https://scadable.com"
        baseline_name = baseline_context.get("company_name") or "SCADABLE"
        baseline_claims = [
            ClaimCitedByURL(category="feature", claim=feature, claim_page_url=scadable_url)
            for feature in baseline_context.get("core_capabilities", [])
            if isinstance(feature, str) and feature.strip()
        ] + [
            ClaimCitedByURL(category="differentiator", claim=claim, claim_page_url=scadable_url)
            for claim in baseline_context.get("differentiators", [])
            if isinstance(claim, str) and claim.strip()
        ]
        frameworks = baseline_context.get("supported_frameworks", [])
        if frameworks:
            baseline_claims.append(ClaimCitedByURL(
                category="differentiator",
                claim=f"Supported frameworks: {', '.join(frameworks)}",
                claim_page_url=scadable_url,
            ))
        baseline = ExtractedInformationByURL(
            company_name=baseline_name,
            summary=baseline_context.get("summary", ""),
            summary_page_urls=[scadable_url],
            claims_cited=baseline_claims,
            job_postings=[],
            conflicting_data=ConflictEntry(company_name=baseline_name, conflicts=[]),
            crawl_time_string=_comparison_time(),
        )

        comparison_competitors = []
        for competitor in competitors:
            stored = competitor.get("extracted_information")
            if isinstance(stored, dict):
                # Fully source-backed, including pricing, integrations, jobs,
                # and conflicting-data citations from extraction.
                facts = ExtractedInformationByURL.model_validate(stored)
                # The DB's canonical competitor name must be used for scores.
                facts = facts.model_copy(update={"company_name": competitor["company_name"]})
            else:
                # Historical D2 records lack the full extracted-information
                # schema. Keep old research usable until it is refreshed.
                website = competitor["website_url"]
                claims = []
                for row in competitor.get("claims", []):
                    if row.get("category") not in {"strength", "weakness", "feature", "differentiator", "pricing", "integrations", "target_customers"}:
                        continue
                    claims.append(ClaimCitedByURL(
                        category=row["category"],
                        claim=row["content"],
                        claim_page_url=row.get("source_url") or website,
                    ))
                for feature in competitor.get("features", []):
                    if isinstance(feature, str) and feature.strip() and not any(
                        claim.category == "feature" and claim.claim == feature for claim in claims
                    ):
                        claims.append(ClaimCitedByURL(
                            category="feature", claim=feature, claim_page_url=website,
                        ))
                facts = ExtractedInformationByURL(
                    company_name=competitor["company_name"],
                    summary=competitor.get("summary", ""),
                    summary_page_urls=[website],
                    claims_cited=claims,
                    job_postings=[],
                    conflicting_data=ConflictEntry(company_name=competitor["company_name"], conflicts=[]),
                    crawl_time_string=competitor.get("crawl_time_string") or "",
                )
            comparison_competitors.append(facts)

        try:
            client = _build_openai_client()
        except Exception:
            if os.getenv("ALLOW_MOCK_LLM", "").lower() not in {"1", "true", "yes"}:
                raise
            logger.exception("OpenAI comparison client unavailable; ALLOW_MOCK_LLM enabled")
            result = await MockOpenAIAnalyzer().compare(baseline_context, competitors)
            result["mode"] = "mock"
            return result
        try:
            swot = await Analyzer(client, trace_prefix=trace_prefix).compare(
                ComparisonInput(scadable=baseline, competitors=comparison_competitors)
            )
        except Exception:
            if os.getenv("ALLOW_MOCK_LLM", "").lower() not in {"1", "true", "yes"}:
                raise
            logger.exception("Real comparison failed; ALLOW_MOCK_LLM enabled, using mock data")
            result = await MockOpenAIAnalyzer().compare(baseline_context, competitors)
            result["mode"] = "mock"
            return result
        finally:
            await client.close()

        # Each competitor SWOT should cite the company it describes. Prefer
        # that company's own source when a finding cites multiple websites.
        claims_by_name = {
            company.company_name: {claim.claim_page_url for claim in company.claims_cited}
            for company in comparison_competitors
        }
        competitor_swots = []
        for block in swot.competitor_swots:
            own_pages = claims_by_name.get(block.company_name, set())
            competitor_swots.append({
                "company_name": block.company_name,
                "swot_entries": [
                    {
                        "category": entry.component,
                        "content": entry.what,
                        "source_url": next(
                            (url for url in entry.swot_component_page_urls if url in own_pages),
                            entry.swot_component_page_urls[0] if entry.swot_component_page_urls else None,
                        ),
                    }
                    for entry in block.SWOT_entries if entry.what.strip()
                ],
            })

        return {
            "schema_version": "backend-comparison-v4",
            "mode": "real",
            "swot_entries": [
                {
                    "category": entry.component,
                    "content": entry.what,
                    "source_url": _pick_source_url(entry.swot_component_page_urls, scadable_url),
                }
                for entry in swot.SWOT_entries
                if entry.what.strip()
            ],
            "scores": [score.model_dump() for score in swot.scores],
            "feature_pairs": [pair.model_dump() for pair in swot.feature_pairs],
            "competitor_swots": competitor_swots,
            "conflicting_data": [item.model_dump(mode="json") for item in swot.conflicting_data],
            "competitor_notes": [item.model_dump(mode="json") for item in swot.competitor_notes],
        }


def get_default_analyzer() -> AnalyzerProtocol:
    """Return the default analysis adapter."""
    return OpenAIAdapter()
