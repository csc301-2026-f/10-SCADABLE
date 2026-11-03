import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ResearchStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SWOTCategory(str, enum.Enum):
    STRENGTH = "strength"
    WEAKNESS = "weakness"
    OPPORTUNITY = "opportunity"
    THREAT = "threat"


class SWOTScope(str, enum.Enum):
    # Analysis of one competitor, produced by one research run.
    COMPETITOR = "competitor"
    # SCADABLE's own SWOT derived across all competitors.
    OVERALL = "overall"


def _enum_column(enum_cls: type[enum.Enum], name: str) -> Enum:
    """Store enums as plain VARCHAR holding the lowercase value.

    native_enum=False avoids Postgres ENUM types, which are painful to alter
    when the team adds a status/category later.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [member.value for member in e],
        length=20,
    )


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_competitors_normalized_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL as the user entered it (with scheme added if missing), for display.
    website_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Domain identity (lowercase host without 'www.', plus any non-default
    # port; no scheme, path, query or fragment). Used only for duplicate
    # detection, so example.com and example.com/pricing are one competitor.
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    research_runs: Mapped[list["ResearchRun"]] = relationship(
        back_populates="competitor",
        cascade="all, delete-orphan",
        order_by="ResearchRun.created_at.desc()",
    )
    swot_entries: Mapped[list["SWOTEntry"]] = relationship(
        back_populates="competitor",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Competitor id={self.id} name={self.name!r}>"


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(
        ForeignKey("competitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ResearchStatus] = mapped_column(
        _enum_column(ResearchStatus, "research_status"),
        nullable=False,
        default=ResearchStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Output of the scraper/retrieval step (pages, text, URLs).
    scraped_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Output of the LLM analysis step, as returned before we split it into
    # SWOT rows. Kept separate so each team's interface can evolve on its own.
    analysis_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    competitor: Mapped["Competitor"] = relationship(
        back_populates="research_runs"
    )
    swot_entries: Mapped[list["SWOTEntry"]] = relationship(
        back_populates="research_run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<ResearchRun id={self.id} competitor_id={self.competitor_id} "
            f"status={self.status.value}>"
        )


class OverallSwotState(Base):
    """Persistent freshness marker for SCADABLE's overall comparison.

    A monotonically increasing input revision prevents an older, slow comparison
    from publishing results after a newer competitor change.
    """

    __tablename__ = "overall_swot_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)


class SWOTEntry(Base):
    __tablename__ = "swot_entries"
    __table_args__ = (
        # competitor-scope rows must point at a competitor and a run;
        # overall-scope rows must point at neither.
        CheckConstraint(
            "(scope = 'competitor' AND competitor_id IS NOT NULL "
            "AND research_run_id IS NOT NULL) "
            "OR (scope = 'overall' AND competitor_id IS NULL "
            "AND research_run_id IS NULL)",
            name="ck_swot_entries_scope_refs",
        ),
        Index("ix_swot_entries_competitor_run", "competitor_id", "research_run_id"),
        Index("ix_swot_entries_scope", "scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[SWOTScope] = mapped_column(
        _enum_column(SWOTScope, "swot_scope"),
        nullable=False,
        default=SWOTScope.COMPETITOR,
    )
    competitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("competitors.id", ondelete="CASCADE"), nullable=True
    )
    research_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=True
    )
    category: Mapped[SWOTCategory] = mapped_column(
        _enum_column(SWOTCategory, "swot_category"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    competitor: Mapped["Competitor | None"] = relationship(
        back_populates="swot_entries"
    )
    research_run: Mapped["ResearchRun | None"] = relationship(
        back_populates="swot_entries"
    )

    def __repr__(self) -> str:
        return (
            f"<SWOTEntry id={self.id} scope={self.scope.value} "
            f"category={self.category.value}>"
        )