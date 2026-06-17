"""One-time bootstrap: close historical ThesisLedger rows using 7-day returns.

We have ~336 thesis rows from June 5 onwards. This script:
1. Pre-fetches price history for all tickers so review_theses() has cache hits
2. Runs review_theses(days_after=7) to close all eligible rows
3. Persists the first set of learned weights immediately

Run once: uv run python scripts/bootstrap_outcomes.py
After this, daily_intelligence_run.py handles ongoing review automatically.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bootstrap_outcomes")


def prefetch_prices(tickers: list[str], earliest_date: date) -> None:
    """Warm the PriceCache for all tickers from earliest_date through today."""
    from maverick_mcp.providers.stock_data import StockDataProvider

    provider = StockDataProvider()
    start = (earliest_date - timedelta(days=2)).strftime("%Y-%m-%d")
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            df = provider.get_stock_data(ticker, start_date=start, end_date=end)
            logger.info("  %s: %d rows cached", ticker, len(df))
        except Exception as exc:
            logger.warning("  %s: price fetch failed — %s", ticker, exc)


def run_bootstrap() -> None:
    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.calibration import (
        brier_score,
        review_theses,
        save_learned_weights,
    )
    from maverick_mcp.vc_loop.models import ThesisLedger

    logger.info("=== Bootstrap outcomes — %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    with SessionLocal() as session:
        open_rows = (
            session.query(ThesisLedger)
            .filter(ThesisLedger.status.in_(["proposed", "open"]))
            .all()
        )

    if not open_rows:
        logger.info("No open thesis rows found — nothing to bootstrap.")
        return

    tickers = sorted({row.ticker for row in open_rows})
    earliest = min(
        row.thesis_date.date() if isinstance(row.thesis_date, datetime) else row.thesis_date
        for row in open_rows
    )
    logger.info(
        "Found %d open rows across %d tickers (earliest: %s)",
        len(open_rows),
        len(tickers),
        earliest,
    )

    logger.info("Pre-fetching price history for %d tickers...", len(tickers))
    prefetch_prices(tickers, earliest)

    logger.info("Running review_theses(days_after=7)...")
    with SessionLocal() as session:
        result = review_theses(session, days_after=7, limit=1000, update_weights=True)

    reviewed = result.get("reviewed_count", 0)
    skipped = result.get("skipped_count", 0)
    cal = result.get("calibration", {})
    lw = cal.get("learned_weights", {})

    logger.info("Reviewed: %d  Skipped: %d", reviewed, skipped)

    if lw.get("status") == "ok":
        weights = lw["weights"]
        sample_count = lw.get("sample_count", 0)

        with SessionLocal() as session:
            all_rows = session.query(ThesisLedger).all()
            brier = brier_score(all_rows)

        save_learned_weights(weights, brier=brier, sample_count=sample_count)
        logger.info(
            "Learned weights saved from %d outcomes (brier=%.4f):",
            sample_count,
            brier or 0.0,
        )
        for feature, w in sorted(weights.items()):
            logger.info("  %s: %.4f", feature, w)
    else:
        logger.info("Not enough closed outcomes yet: %s", lw.get("status"))

    logger.info("=== Bootstrap complete ===")


if __name__ == "__main__":
    run_bootstrap()
