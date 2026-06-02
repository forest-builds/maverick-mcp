"""Thesis ledger service for the VC loop.

Read/write access to the ``vc_thesis_ledger`` table plus a stub stats method
for the learning loop. Full outcome-based scoring math lands in Phase 2.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import Table
from sqlalchemy.orm import Session

from maverick_mcp.data.models import engine
from maverick_mcp.vc_loop.models import ThesisLedger

logger = logging.getLogger(__name__)

# Guards against repeated DDL on every service init.
_TABLE_ENSURED = False


def ensure_thesis_table() -> None:
    """Idempotently create the ``vc_thesis_ledger`` table.

    ``ensure_database_schema()`` only runs ``create_all`` for models imported
    at the time of the first ``SessionLocal()`` call. Because this model is
    lazily imported by the VC loop, its table may never be created otherwise.
    Creating it here (with ``checkfirst=True``) guarantees it exists regardless
    of import order.
    """
    global _TABLE_ENSURED
    if _TABLE_ENSURED:
        return
    cast(Table, ThesisLedger.__table__).create(bind=engine, checkfirst=True)
    _TABLE_ENSURED = True


def to_dict(thesis: ThesisLedger) -> dict[str, Any]:
    """Serialize a :class:`ThesisLedger` row to a JSON-friendly dict."""
    return {
        "id": thesis.id,
        "ticker": thesis.ticker,
        "thesis_date": thesis.thesis_date.isoformat()
        if thesis.thesis_date is not None
        else None,
        "thesis": thesis.thesis,
        "features": thesis.features or {},
        "conviction": thesis.conviction,
        "score_breakdown": thesis.score_breakdown or {},
        "status": thesis.status,
        "proposed_action": thesis.proposed_action,
        "sector": thesis.sector,
        "outcome": thesis.outcome,
        "note_path": thesis.note_path,
    }


class ThesisService:
    """Business logic for the VC loop thesis ledger.

    Args:
        db_session: A SQLAlchemy synchronous session.
    """

    def __init__(self, db_session: Session) -> None:
        ensure_thesis_table()
        self._db = db_session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_thesis(
        self,
        *,
        ticker: str,
        thesis: str,
        conviction: float,
        features: dict[str, Any] | None = None,
        score_breakdown: dict[str, Any] | None = None,
        proposed_action: str | None = None,
        sector: str | None = None,
        note_path: str | None = None,
    ) -> ThesisLedger:
        """Create and persist a new proposed thesis.

        Args:
            ticker: Ticker symbol (will be uppercased).
            thesis: Human-readable rationale.
            conviction: Conviction score on a 0-100 scale.
            features: Raw normalized signal inputs.
            score_breakdown: Per-component score contributions.
            proposed_action: Suggested action, e.g. ``"BUY watch"``.
            sector: Optional sector classification.
            note_path: Relative path to the Obsidian memo.

        Returns:
            The created :class:`ThesisLedger`.
        """
        entry = ThesisLedger(
            ticker=ticker.upper(),
            thesis=thesis,
            conviction=conviction,
            features=features or {},
            score_breakdown=score_breakdown or {},
            proposed_action=proposed_action,
            sector=sector,
            note_path=note_path,
            status="proposed",
        )
        self._db.add(entry)
        self._db.commit()
        self._db.refresh(entry)
        return entry

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_theses(
        self,
        *,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[ThesisLedger]:
        """Query theses with optional filters, newest first.

        Args:
            ticker: Filter by ticker symbol (case-insensitive).
            status: Filter by status — ``"proposed"`` / ``"open"`` / ``"closed"``.
            limit: Maximum number of results to return.

        Returns:
            List of :class:`ThesisLedger` ordered by ``thesis_date`` desc.
        """
        query = self._db.query(ThesisLedger)
        if ticker is not None:
            query = query.filter(ThesisLedger.ticker == ticker.upper())
        if status is not None:
            query = query.filter(ThesisLedger.status == status)
        return (
            query.order_by(ThesisLedger.thesis_date.desc()).limit(limit).all()
        )

    def get_thesis(self, thesis_id: int) -> ThesisLedger | None:
        """Fetch a single thesis by primary key.

        Returns:
            The :class:`ThesisLedger` or ``None`` if not found.
        """
        return (
            self._db.query(ThesisLedger)
            .filter(ThesisLedger.id == thesis_id)
            .first()
        )

    def latest_run(self, *, limit: int = 20) -> list[ThesisLedger]:
        """Return the most recently proposed theses, newest first.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of :class:`ThesisLedger` with status ``"proposed"``.
        """
        return self.list_theses(status="proposed", limit=limit)

    # ------------------------------------------------------------------
    # Stats (stub for the learning loop)
    # ------------------------------------------------------------------

    def thesis_stats(self) -> dict[str, Any]:
        """Aggregate stats over the ledger.

        Computes what is possible without realized outcomes: total count,
        a per-status breakdown, and average conviction. Outcome-based metrics
        (``hit_rate``, ``brier_score``) land in Phase 2 once outcomes are
        recorded, so they are returned as ``None`` here.

        Returns:
            A dict with keys ``total``, ``by_status``, ``avg_conviction``,
            ``hit_rate`` (None), and ``brier_score`` (None).
        """
        rows = self._db.query(ThesisLedger).all()
        total = len(rows)

        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1

        avg_conviction = (
            sum(row.conviction for row in rows) / total if total else None
        )

        return {
            "total": total,
            "by_status": by_status,
            "avg_conviction": avg_conviction,
            # Phase 2: outcome-based scoring once `outcome` is populated.
            "hit_rate": None,
            "brier_score": None,
        }
