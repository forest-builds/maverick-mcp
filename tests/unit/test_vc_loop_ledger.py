"""Unit tests for the VC loop ThesisService."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from maverick_mcp.database.base import Base
from maverick_mcp.vc_loop.ledger import ThesisService, to_dict
from maverick_mcp.vc_loop.models import ThesisLedger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine)
    session = _Session()
    yield session
    session.close()


@pytest.fixture
def service(db_session):
    return ThesisService(db_session=db_session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_add_thesis_persists_and_returns_row(service):
    entry = service.add_thesis(
        ticker="aapl",
        thesis="Strong momentum and expanding margins.",
        conviction=72.5,
        features={"rsi": 61, "trend": "up"},
        score_breakdown={"momentum": 40, "fundamentals": 32.5},
        proposed_action="BUY watch",
        sector="Technology",
        note_path="vc/aapl-2026.md",
    )

    assert entry.id is not None
    assert entry.ticker == "AAPL"  # uppercased
    assert entry.thesis == "Strong momentum and expanding margins."
    assert entry.conviction == 72.5
    assert entry.features == {"rsi": 61, "trend": "up"}
    assert entry.score_breakdown == {"momentum": 40, "fundamentals": 32.5}
    assert entry.proposed_action == "BUY watch"
    assert entry.sector == "Technology"
    assert entry.note_path == "vc/aapl-2026.md"
    assert entry.status == "proposed"
    assert entry.thesis_date is not None


def test_add_thesis_defaults(service):
    entry = service.add_thesis(ticker="MSFT", thesis="Cloud growth.", conviction=50.0)
    assert entry.features == {}
    assert entry.score_breakdown == {}
    assert entry.outcome is None
    assert entry.status == "proposed"


def test_get_thesis(service):
    entry = service.add_thesis(ticker="NVDA", thesis="AI tailwind.", conviction=90.0)
    fetched = service.get_thesis(entry.id)
    assert fetched is not None
    assert fetched.id == entry.id
    assert fetched.ticker == "NVDA"

    assert service.get_thesis(999999) is None


def test_list_theses_filters_by_ticker(service):
    service.add_thesis(ticker="AAPL", thesis="t1", conviction=60.0)
    service.add_thesis(ticker="AAPL", thesis="t2", conviction=65.0)
    service.add_thesis(ticker="TSLA", thesis="t3", conviction=70.0)

    aapl = service.list_theses(ticker="aapl")
    assert len(aapl) == 2
    assert {e.ticker for e in aapl} == {"AAPL"}

    tsla = service.list_theses(ticker="TSLA")
    assert len(tsla) == 1


def test_list_theses_filters_by_status(service):
    e1 = service.add_thesis(ticker="AAPL", thesis="t1", conviction=60.0)
    service.add_thesis(ticker="TSLA", thesis="t2", conviction=70.0)

    # Promote one to "open"
    e1.status = "open"
    service._db.commit()

    proposed = service.list_theses(status="proposed")
    assert len(proposed) == 1
    assert proposed[0].ticker == "TSLA"

    open_rows = service.list_theses(status="open")
    assert len(open_rows) == 1
    assert open_rows[0].ticker == "AAPL"


def test_list_theses_respects_limit(service):
    for i in range(5):
        service.add_thesis(ticker="AAPL", thesis=f"t{i}", conviction=float(i))
    assert len(service.list_theses(limit=3)) == 3


def test_latest_run_returns_proposed(service):
    service.add_thesis(ticker="AAPL", thesis="t1", conviction=60.0)
    service.add_thesis(ticker="TSLA", thesis="t2", conviction=70.0)
    run = service.latest_run(limit=10)
    assert len(run) == 2
    assert all(r.status == "proposed" for r in run)


def test_thesis_stats_keys_and_counts(service):
    e1 = service.add_thesis(ticker="AAPL", thesis="t1", conviction=60.0)
    service.add_thesis(ticker="TSLA", thesis="t2", conviction=80.0)
    e1.status = "open"
    service._db.commit()

    stats = service.thesis_stats()
    assert set(stats.keys()) == {
        "total",
        "by_status",
        "avg_conviction",
        "closed_count",
        "hit_rate",
        "brier_score",
    }
    assert stats["total"] == 2
    assert stats["by_status"] == {"open": 1, "proposed": 1}
    assert stats["avg_conviction"] == pytest.approx(70.0)
    assert stats["closed_count"] == 0
    assert stats["hit_rate"] is None
    assert stats["brier_score"] is None


def test_thesis_stats_empty(service):
    stats = service.thesis_stats()
    assert stats["total"] == 0
    assert stats["by_status"] == {}
    assert stats["avg_conviction"] is None


def test_to_dict_shape(service):
    entry = service.add_thesis(
        ticker="AAPL",
        thesis="rationale",
        conviction=55.0,
        features={"a": 1},
        score_breakdown={"b": 2},
        proposed_action="trim",
        sector="Tech",
        note_path="vc/aapl.md",
    )
    d = to_dict(entry)
    assert d["id"] == entry.id
    assert d["ticker"] == "AAPL"
    assert isinstance(d["thesis_date"], str)  # ISO formatted
    assert d["thesis"] == "rationale"
    assert d["features"] == {"a": 1}
    assert d["conviction"] == 55.0
    assert d["score_breakdown"] == {"b": 2}
    assert d["status"] == "proposed"
    assert d["proposed_action"] == "trim"
    assert d["sector"] == "Tech"
    assert d["outcome"] is None
    assert d["note_path"] == "vc/aapl.md"


def test_model_table_name():
    assert ThesisLedger.__tablename__ == "vc_thesis_ledger"
