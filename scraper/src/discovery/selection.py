"""Coordinate bounded decision cycles and feedback without owning fetch resources."""

from ..config import ScraperConfig
from ..content_identity import markdown_fingerprint
from ..coverage import CoverageContext, coverage_summary
from ..decision_models import SelectionDiagnostics
from ..interfaces import DecisionProvider
from ..models import DiscoveredUrl, FetchedPage
from .evidence import EvidenceLedger
from .traversal import UrlFrontier


class AdaptiveSelection:
    def __init__(self, frontier: UrlFrontier, settings: ScraperConfig, provider: DecisionProvider):
        self.frontier, self.settings, self.provider = frontier, settings, provider
        self.ledger = EvidenceLedger()
        self._last_evaluation_attempt = -5
        self._bodies: dict[str, FetchedPage] = {}
        self._coverage_pages: list[FetchedPage] = []

    def _mode(self):
        mode = self.provider.summary.effective_mode
        self.frontier.set_mode(mode)
        return mode

    async def rank_if_needed(self) -> None:
        if self._mode() != "llm" or self.settings.strategy == "bfs":
            return
        if self.frontier.attempted_pages >= self.settings.max_pages:
            return
        if self.frontier.attempted_pages - self._last_evaluation_attempt < 5 and (
            self.frontier.has_scored_candidates
        ):
            return
        setter = getattr(self.provider, "set_coverage_context", None)
        if self.settings.selection.coverage_enabled and callable(setter):
            context = CoverageContext.from_summary(coverage_summary(self._coverage_pages))
            setter(context)
            self.frontier.set_coverage_context(context)
        candidates = self.frontier.evaluation_candidates()
        if candidates:
            scores = await self.provider.score_candidates(candidates)
            self.frontier.apply_scores(candidates, scores)
            self._mode()
        self._last_evaluation_attempt = self.frontier.attempted_pages

    @property
    def evaluation_due(self) -> bool:
        """Finish a fetch batch at a scoring boundary, never reorder work in flight."""
        return (
            self._mode() == "llm"
            and self.settings.strategy == "best_first"
            and (
                self.frontier.attempted_pages - self._last_evaluation_attempt >= 5
                or not self.frontier.has_scored_candidates
            )
        )

    @staticmethod
    def attach(page: FetchedPage, record: DiscoveredUrl) -> FetchedPage:
        assessment = page.content_assessment
        available = assessment is not None and any(
            value is not None
            for value in (
                assessment.usefulness,
                assessment.novelty,
                assessment.category_confidence,
            )
        )
        return page.model_copy(
            update={
                "selection": SelectionDiagnostics(
                    method=record.selection_method,
                    relevance_score=record.relevance_score,
                    assessment_available=available,
                )
            }
        )

    async def assess(self, page: FetchedPage, record: DiscoveredUrl) -> FetchedPage:
        fingerprint = (
            markdown_fingerprint(page.raw_markdown)
            if self.settings.deduplicate_pages and page.crawl_status == "success"
            else None
        )
        if fingerprint:
            representative = self._bodies.get(fingerprint)
            metadata = {**page.metadata, "content_fingerprint": fingerprint}
            if representative is not None:
                assessment = representative.content_assessment
                if assessment is not None:
                    assessment = assessment.model_copy(
                        update={"novelty": 0.0, "novelty_confidence": None}
                    )
                return self.attach(
                    page.model_copy(
                        update={
                            "metadata": {
                                **metadata,
                                "duplicate_of": str(representative.source_url),
                            },
                            "content_assessment": assessment,
                        }
                    ),
                    record,
                )
            page = page.model_copy(update={"metadata": metadata})
        if page.crawl_status == "success" and self._mode() == "llm":
            assessment = await self.provider.assess_content(page, self.ledger.excerpts)
            page = page.model_copy(update={"content_assessment": assessment})
            self.frontier.note_assessment(str(record.normalized_url), assessment)
            self.ledger.add(page)
            self._mode()
        if fingerprint:
            self._bodies[fingerprint] = page
        if self.settings.selection.coverage_enabled and page.crawl_status == "success":
            self._coverage_pages.append(page)
        return self.attach(page, record)
