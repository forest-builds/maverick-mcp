"""Unit tests for VC-loop outcome review and calibration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from maverick_mcp.data.models import PriceCache, Stock
from maverick_mcp.database.base import Base
from maverick_mcp.vc_loop.calibration import (
    apply_learned_weights,
    brier_score,
    calibration_bins,
    edge_attribution,
    learned_weights,
    review_theses,
)
from maverick_mcp.vc_loop.ledger import ThesisService
from maverick_mcp.vc_loop.scorer import WEIGHTS


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine)
    session = _Session()
    yield session
    session.close()


def _add_price(session, ticker: str, price_date: date, close: float) -> None:
    stock = Stock.get_or_create(session, ticker)
    session.add(
        PriceCache(
            stock_id=stock.stock_id,
            date=price_date,
            open_price=Decimal(str(close)),
            high_price=Decimal(str(close)),
            low_price=Decimal(str(close)),
            close_price=Decimal(str(close)),
            volume=1000,
        )
    )
    session.commit()


def _add_thesis(
    service: ThesisService,
    *,
    ticker: str,
    conviction: float,
    thesis_date: date,
    features: dict[str, float] | None = None,
):
    row = service.add_thesis(
        ticker=ticker,
        thesis=f"{ticker} test thesis",
        conviction=conviction,
        features=features or {},
    )
    row.thesis_date = datetime(
        thesis_date.year, thesis_date.month, thesis_date.day, tzinfo=UTC
    )
    service._db.commit()
    return row


def test_review_theses_closes_due_outcome(db_session):
    service = ThesisService(db_session)
    row = _add_thesis(
        service,
        ticker="AAPL",
        conviction=70.0,
        thesis_date=date(2026, 1, 1),
        features={"screening_score": 0.8, "momentum": 0.7},
    )
    _add_price(db_session, "AAPL", date(2026, 1, 1), 100.0)
    _add_price(db_session, "AAPL", date(2026, 1, 31), 112.0)

    result = review_theses(db_session, days_after=30)

    assert result["status"] == "ok"
    assert result["reviewed_count"] == 1
    assert result["skipped_count"] == 0
    db_session.refresh(row)
    assert row.status == "closed"
    assert row.outcome["win"] is True
    assert row.outcome["return_pct"] == pytest.approx(12.0)
    assert result["calibration"]["brier_score"] == pytest.approx(0.09)


def test_review_theses_skips_missing_prices(db_session):
    service = ThesisService(db_session)
    row = _add_thesis(
        service,
        ticker="MSFT",
        conviction=65.0,
        thesis_date=date(2026, 1, 1),
    )

    result = review_theses(db_session, days_after=30)

    assert result["reviewed_count"] == 0
    assert result["skipped"] == [
        {
            "id": row.id,
            "ticker": "MSFT",
            "reason": "missing_price_data",
            "target_date": "2026-01-31",
        }
    ]
    db_session.refresh(row)
    assert row.status == "proposed"
    assert row.outcome is None


def test_calibration_bins_and_brier_score(db_session):
    service = ThesisService(db_session)
    row1 = _add_thesis(service, ticker="AAPL", conviction=70.0, thesis_date=date(2026, 1, 1))
    row2 = _add_thesis(service, ticker="MSFT", conviction=30.0, thesis_date=date(2026, 1, 1))
    row1.status = "closed"
    row1.outcome = {"win": True, "return_pct": 10.0}
    row2.status = "closed"
    row2.outcome = {"win": False, "return_pct": -5.0}
    db_session.commit()

    rows = service.list_theses(limit=10)

    assert brier_score(rows) == pytest.approx(((0.7 - 1.0) ** 2 + (0.3 - 0.0) ** 2) / 2)
    assert calibration_bins(rows) == [
        {
            "bucket": "30-40",
            "count": 1,
            "avg_prediction": 0.3,
            "hit_rate": 0.0,
            "calibration_error": 0.3,
        },
        {
            "bucket": "70-80",
            "count": 1,
            "avg_prediction": 0.7,
            "hit_rate": 1.0,
            "calibration_error": 0.3,
        },
    ]


def test_edge_attribution_and_learned_weights(db_session):
    service = ThesisService(db_session)
    fixtures = [
        ("A", 80.0, {"screening_score": 0.9, "momentum": 0.9}, True, 15.0),
        ("B", 75.0, {"screening_score": 0.8, "momentum": 0.8}, True, 10.0),
        ("C", 35.0, {"screening_score": 0.2, "momentum": 0.2}, False, -8.0),
    ]
    for ticker, conviction, features, win, return_pct in fixtures:
        row = _add_thesis(
            service,
            ticker=ticker,
            conviction=conviction,
            thesis_date=date(2026, 1, 1),
            features=features,
        )
        row.status = "closed"
        row.outcome = {"win": win, "return_pct": return_pct}
    db_session.commit()

    rows = service.list_theses(limit=10)
    attribution = edge_attribution(rows)
    assert attribution["screening_score"]["win_correlation"] > 0
    assert attribution["momentum"]["return_correlation"] > 0

    learned = learned_weights(
        rows,
        min_samples=3,
        prior_weights={"screening_score": 0.5, "momentum": 0.5},
    )
    assert learned["status"] == "ok"
    assert learned["sample_count"] == 3
    assert sum(learned["weights"].values()) == pytest.approx(1.0)


def test_learned_weights_requires_minimum_samples(db_session):
    service = ThesisService(db_session)
    row = _add_thesis(service, ticker="AAPL", conviction=70.0, thesis_date=date(2026, 1, 1))
    row.status = "closed"
    row.outcome = {"win": True, "return_pct": 5.0}
    db_session.commit()

    learned = learned_weights(service.list_theses(), min_samples=5)

    assert learned["status"] == "insufficient_data"
    assert learned["sample_count"] == 1


def test_apply_learned_weights_updates_runtime_scorer_weights():
    original = dict(WEIGHTS)
    try:
        result = apply_learned_weights(
            {
                "status": "ok",
                "weights": {"screening_score": 0.7, "momentum": 0.3},
            }
        )

        assert result["status"] == "updated"
        assert WEIGHTS == {"screening_score": 0.7, "momentum": 0.3}
    finally:
        WEIGHTS.clear()
        WEIGHTS.update(original)
