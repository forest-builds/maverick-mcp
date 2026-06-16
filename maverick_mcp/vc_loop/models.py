"""SQLAlchemy models for the VC loop thesis ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from maverick_mcp.data.models import TimestampMixin
from maverick_mcp.database.base import Base


class ThesisLedger(Base, TimestampMixin):
    """A single investment thesis proposed by the VC loop.

    Each row captures the rationale, the normalized signal inputs that drove
    it, a conviction score, and (later) the realized outcome used for the
    learning loop.
    """

    __tablename__ = "vc_thesis_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    thesis_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    # Human-readable rationale for the thesis.
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    # Raw normalized signal inputs that produced the thesis.
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Conviction on a 0-100 scale.
    conviction: Mapped[float] = mapped_column(Float, nullable=False)
    # Per-component contributions to the conviction score.
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # "proposed" | "open" | "closed"
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed"
    )
    # e.g. "BUY watch", "trim"
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Filled in a later phase once the outcome is known.
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Relative path to the Obsidian memo.
    note_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("idx_vc_thesis_ticker", "ticker"),
        Index("idx_vc_thesis_status", "status"),
    )


class BriefSnapshot(Base, TimestampMixin):
    """Portfolio-level intelligence snapshot captured on each /brief run.

    Enables drift detection (conviction score over time), action accountability
    (what was recommended vs what was done), and regime-correlated performance
    analysis. Complements ThesisLedger, which tracks per-ticker conviction.
    """

    __tablename__ = "brief_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Portfolio state
    equity_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployed_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Intelligence metrics
    portfolio_conviction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_conviction_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_screen_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    off_screen_held_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Regime at time of snapshot
    regime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Diversification (Dalio layer) — tracked over time to see if the book is
    # spreading risk or concentrating it.
    effective_bets: Mapped[float | None] = mapped_column(Float, nullable=True)
    diversification_grade: Mapped[str | None] = mapped_column(String(2), nullable=True)
    largest_cluster_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CIO synthesis text verbatim
    synthesis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full blobs for replay / diff
    positions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    screen_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_opportunities_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


class PositionSnapshot(Base, TimestampMixin):
    """Per-position P&L snapshot captured on each intelligence run.

    One row per held ticker per run. Enables conviction accuracy analysis:
    compare conviction_at_snapshot on day T to unrealized_pnl_pct on T+N.
    """

    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    conviction_at_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_pos_snap_ticker_at", "ticker", "snapshot_at"),
    )
