"""One auditable URL registry and deterministic BestFirst/BFS frontier per competitor."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from math import floor
from urllib.parse import urlsplit

from ..config import ScraperConfig
from ..coverage import CoverageContext
from ..decision_models import (
    CandidateObservation,
    CandidateScore,
    DecisionCandidate,
    PageAssessment,
)
from ..models import DiscoveredUrl, DiscoveryEvidence
from ..types import DiscoverySource, UrlDisposition
from .evidence import bounded_observations
from .relevance import UrlPolicy
from .urls import normalize_url

MAX_EVIDENCE_PER_URL = 8


@dataclass
class _Entry:
    record: DiscoveredUrl
    sequence: int
    observations: list[CandidateObservation] = field(default_factory=list)
    evaluations: int = 0
    evaluated_context: tuple = ()
    attempted: bool = False
    in_flight: bool = False


class UrlFrontier:
    def __init__(self, homepage: str, settings: ScraperConfig):
        self.settings = settings
        self.homepage = homepage
        self.policy = UrlPolicy(homepage, settings)
        self._entries: dict[str, _Entry] = {}
        self._children: dict[str, set[str]] = {}
        self.attempted_pages = 0
        self.invalid_links = 0
        self.truncated = False
        self.non_homepage_attempts = 0
        self._mode = settings.selection.mode
        self._section_attempts = Counter()
        self._section_novelty: dict[tuple, list[float]] = {}
        self._final_urls: dict[str, str] = {}
        self._coverage_state: tuple = ()
        self.discover(homepage, depth=0, source="homepage", depth_basis="homepage")

    @property
    def records(self) -> list[DiscoveredUrl]:
        return [entry.record.model_copy(deep=True) for entry in self._entries.values()]

    def _replace(self, entry: _Entry, **changes) -> None:
        entry.record = DiscoveredUrl.model_validate({**entry.record.model_dump(), **changes})

    def _refresh_policy(self, entry: _Entry) -> None:
        if entry.attempted:
            return
        record = entry.record
        decision = self.policy.evaluate(
            str(record.normalized_url),
            homepage=record.discovery_source == DiscoverySource.HOMEPAGE,
        )
        disposition, reason = decision.disposition, decision.reason
        if disposition == UrlDisposition.PENDING and record.depth > self.settings.max_depth:
            disposition, reason = UrlDisposition.DEFERRED, "depth_limit"
        if disposition == UrlDisposition.PENDING and str(record.normalized_url) in self._final_urls:
            disposition = UrlDisposition.SKIPPED
            reason = "duplicate_redirect:" + self._final_urls[str(record.normalized_url)]
        self._replace(
            entry,
            disposition=disposition,
            reason=reason,
        )

    def discover(
        self,
        raw_url: str,
        *,
        parent_url: str | None = None,
        depth: int = 1,
        source: str | DiscoverySource = DiscoverySource.INTERNAL_LINK,
        depth_basis: str = "link",
        title: str = "",
        observation: CandidateObservation | None = None,
    ) -> DiscoveredUrl | None:
        normalized = normalize_url(raw_url, base_url=parent_url or self.homepage)
        if normalized is None:
            self.invalid_links += 1
            return None
        key = normalized.normalized
        entry = self._entries.get(key)
        if entry is None and len(self._entries) >= self.settings.max_discovered_urls:
            self.truncated = True
            return None
        parent = normalize_url(parent_url) if parent_url else None
        evidence = DiscoveryEvidence(
            observed_url=raw_url,
            discovery_source=source,
            parent_url=parent.absolute if parent else None,
            depth=depth,
            title=title,
            observation=observation,
        )
        if entry is None:
            record = DiscoveredUrl(
                url=normalized.absolute,
                normalized_url=key,
                discovery_source=source,
                parent_url=evidence.parent_url,
                depth=depth,
                depth_basis=depth_basis,
                evidence=[evidence],
            )
            entry = _Entry(record, len(self._entries))
            self._entries[key] = entry
        else:
            items = entry.record.evidence
            changes = {"discovery_count": entry.record.discovery_count + 1}
            if len(items) < MAX_EVIDENCE_PER_URL and evidence not in items:
                changes["evidence"] = [*items, evidence]
            if depth < entry.record.depth:
                changes.update(depth=depth, depth_basis=depth_basis, parent_url=evidence.parent_url)
            self._replace(entry, **changes)
        if observation is None and title:
            observation = CandidateObservation(
                parent_url=parent.absolute if parent else None, anchor_text=title[:160]
            )
        if observation is not None:
            entry.observations = bounded_observations([*entry.observations, observation])
        if parent and parent.normalized in self._entries:
            self._children.setdefault(parent.normalized, set()).add(key)
        self._refresh_policy(entry)
        self._relax_descendants(key)
        return entry.record.model_copy(deep=True)

    def _relax_descendants(self, key: str) -> None:
        """Propagate shorter discovered paths without refetching already fetched pages."""
        pending = [key]
        while pending:
            parent_key = pending.pop()
            parent = self._entries[parent_key].record
            for child_key in sorted(self._children.get(parent_key, ())):
                child = self._entries[child_key]
                depth = parent.depth + 1
                if child.record.depth <= depth:
                    continue
                self._replace(
                    child, depth=depth, depth_basis="link", parent_url=parent.normalized_url
                )
                self._refresh_policy(child)
                pending.append(child_key)

    def pop_next(
        self, *, eligible: Callable[[DiscoveredUrl], bool] | None = None
    ) -> DiscoveredUrl | None:
        if self.attempted_pages >= self.settings.max_pages:
            return None

        # With at most 5,000 records and 50 attempts, scanning the registry is small and
        # avoids an unbounded heap of stale entries when URLs are rediscovered repeatedly.
        candidates = [
            entry
            for entry in self._entries.values()
            if not entry.attempted
            and entry.record.disposition == UrlDisposition.PENDING
            and (eligible is None or eligible(entry.record))
        ]
        if not candidates:
            return None
        homepages = [item for item in candidates if self._is_homepage(item)]
        if homepages:
            entry, method = homepages[0], "homepage"
        elif self.settings.strategy == "bfs":
            entry = min(candidates, key=lambda item: (item.record.depth, item.sequence))
            method = "bfs"
        else:
            fraction = self.settings.selection.exploration_fraction
            explore = floor((self.non_homepage_attempts + 1) * fraction + 1e-9) > floor(
                self.non_homepage_attempts * fraction + 1e-9
            )
            scored = [item for item in candidates if item.record.relevance_score is not None]
            if self._mode == "llm" and not explore and scored:
                entry = min(
                    scored, key=lambda item: (-self._rank(item), item.record.depth, item.sequence)
                )
                method = "ranked"
            else:
                entry = min(
                    candidates,
                    key=lambda item: (
                        self._section_attempts[self._section(item)],
                        item.evaluations,
                        item.record.depth,
                        item.sequence,
                    ),
                )
                method = "structural" if self._mode == "structural" else "exploration"
        entry.attempted = entry.in_flight = True
        self.attempted_pages += 1
        self._section_attempts[self._section(entry)] += 1
        if not self._is_homepage(entry):
            self.non_homepage_attempts += 1
        self._replace(entry, selection_method=method)
        return entry.record.model_copy(deep=True)

    @staticmethod
    def _is_homepage(entry):
        return entry.record.discovery_source == DiscoverySource.HOMEPAGE

    @staticmethod
    def _section(entry):
        parts = urlsplit(str(entry.record.normalized_url))
        return parts.hostname, next((part for part in parts.path.split("/") if part), "")

    def _rank(self, entry):
        section = self._section(entry)
        novelty = self._section_novelty.get(section, [])
        return (
            0.7 * entry.record.relevance_score
            + 0.2 / (1 + self._section_attempts[section])
            + 0.1 * (sum(novelty) / len(novelty) if novelty else 0.5)
        )

    def set_mode(self, mode: str) -> None:
        if mode not in {"llm", "structural"}:
            raise ValueError("Unknown selection mode")
        self._mode = mode

    def set_coverage_context(self, context: CoverageContext) -> None:
        self._coverage_state = context.states

    def _context(self, observations):
        return (
            self._coverage_state,
            tuple(
                sorted(
                    {
                        (item.anchor_text.casefold(), item.surrounding_text.casefold())
                        for item in observations
                    }
                )
            ),
        )

    def evaluation_candidates(self, limit: int = 50) -> list[DecisionCandidate]:
        """Round-robin sections, with unseen candidates before one permitted refresh."""
        pending = [
            entry
            for entry in self._entries.values()
            if not entry.attempted
            and entry.record.disposition == UrlDisposition.PENDING
            and not self._is_homepage(entry)
            and (
                entry.evaluations == 0
                or (
                    entry.evaluations == 1
                    and self._context(entry.observations) != entry.evaluated_context
                )
            )
        ]
        result = []
        chosen = Counter()
        while pending and len(result) < min(limit, 50):
            entry = min(
                pending,
                key=lambda item: (
                    item.evaluations,
                    chosen[self._section(item)],
                    self._section_attempts[self._section(item)],
                    item.record.depth,
                    item.sequence,
                ),
            )
            pending.remove(entry)
            chosen[self._section(entry)] += 1
            result.append(
                DecisionCandidate(
                    candidate_id=f"url_{entry.sequence}",
                    url=entry.record.normalized_url,
                    discovery_source=entry.record.discovery_source.value,
                    observations=list(entry.observations),
                )
            )
        return result

    def apply_scores(
        self, candidates: list[DecisionCandidate], scores: list[CandidateScore]
    ) -> None:
        counts = Counter(item.candidate_id for item in scores)
        results = {item.candidate_id: item for item in scores}
        for candidate in candidates:
            entry = self._entries[str(candidate.url)]
            entry.evaluations += 1
            entry.evaluated_context = self._context(candidate.observations)
            result = results.get(candidate.candidate_id)
            if counts[candidate.candidate_id] == 1 and result.status == "scored":
                self._replace(entry, relevance_score=result.score)
            else:
                self._replace(entry, relevance_score=None)

    @property
    def has_scored_candidates(self) -> bool:
        return any(
            not item.attempted
            and item.record.disposition == UrlDisposition.PENDING
            and item.record.relevance_score is not None
            for item in self._entries.values()
        )

    def note_assessment(self, url: str, assessment: PageAssessment) -> None:
        entry = self._entries[normalize_url(url).normalized]
        if entry.record.disposition == UrlDisposition.CRAWLED and assessment.novelty is not None:
            self._section_novelty.setdefault(self._section(entry), []).append(assessment.novelty)

    def get(self, url: str) -> DiscoveredUrl:
        normalized = normalize_url(url)
        if normalized is None or normalized.normalized not in self._entries:
            raise KeyError(url)
        return self._entries[normalized.normalized].record.model_copy(deep=True)

    def reject_reserved(self, url: str, *, reason: str, deferred: bool = False) -> None:
        """A preflight refusal is not a page fetch and releases its reserved page slot."""
        normalized = normalize_url(url)
        entry = self._entries[normalized.normalized]
        if not entry.in_flight:
            raise ValueError("Only a reserved URL can be rejected")
        entry.in_flight = False
        self.attempted_pages -= 1
        self._section_attempts[self._section(entry)] -= 1
        if not self._is_homepage(entry):
            self.non_homepage_attempts -= 1
        self._replace(
            entry,
            disposition=UrlDisposition.DEFERRED if deferred else UrlDisposition.SKIPPED,
            reason=reason,
        )

    def complete(self, url: str, *, success: bool, error: str | None = None) -> None:
        normalized = normalize_url(url)
        entry = self._entries.get(normalized.normalized if normalized else "")
        if entry is None or not entry.in_flight:
            raise ValueError("Only a reserved URL can be completed")
        entry.in_flight = False
        self._replace(
            entry,
            disposition=UrlDisposition.CRAWLED if success else UrlDisposition.FAILED,
            reason=None if success else (error or "fetch_failed"),
        )

    def register_final_url(self, final_url: str, representative: str) -> None:
        """Record observed successful targets; never infer aliases from host/canonical tags."""
        normalized = normalize_url(final_url)
        if not self.settings.deduplicate_pages or normalized is None:
            return
        self._final_urls.setdefault(normalized.normalized, representative)
        entry = self._entries.get(normalized.normalized)
        if entry is not None:
            self._refresh_policy(entry)

    def finalize(self, *, reason: str | None = None) -> None:
        reason = reason or (
            "page_limit" if self.attempted_pages >= self.settings.max_pages else "not_crawled"
        )
        for entry in self._entries.values():
            if entry.record.disposition == UrlDisposition.PENDING and not entry.in_flight:
                self._replace(entry, disposition=UrlDisposition.DEFERRED, reason=reason)
