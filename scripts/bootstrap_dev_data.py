#!/usr/bin/env python3
"""Bootstrap a live development dataset for Maverick-MCP.

Idempotent. Populates the app database (``sqlite:///maverick_mcp.db`` by
default) with a real, screenable dataset so tools like ``vc_loop_run`` return
genuine candidates:

  1. ensure the full schema exists (all component tables),
  2. create/refresh a curated stock universe (with real sectors),
  3. fetch real daily price history (enough for SMA-200 screening),
  4. run the real screening algorithms in ``run_stock_screening.py``.

Usage:
    uv run python scripts/bootstrap_dev_data.py            # default 500 days
    uv run python scripts/bootstrap_dev_data.py --days 400 --no-screen
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from maverick_mcp.data.models import (  # noqa: E402
    SessionLocal,
    Stock,
    bulk_insert_price_data,
    ensure_database_schema,
)
from maverick_mcp.providers.stock_data import EnhancedStockDataProvider  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("bootstrap_dev_data")

# Curated, liquid universe with real sectors. Chosen to actually produce
# momentum / supply-demand screening hits and to avoid delisted tickers.
UNIVERSE: dict[str, str] = {
    # Mega-cap tech
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "META": "Communication Services",
    "NVDA": "Technology",
    "AVGO": "Technology",
    "ORCL": "Technology",
    "CRM": "Technology",
    "ADBE": "Technology",
    "AMD": "Technology",
    "NFLX": "Communication Services",
    # Financials
    "JPM": "Financials",
    "V": "Financials",
    "MA": "Financials",
    "GS": "Financials",
    # Healthcare
    "LLY": "Healthcare",
    "UNH": "Healthcare",
    "JNJ": "Healthcare",
    # Consumer / industrials / energy
    "COST": "Consumer Staples",
    "WMT": "Consumer Staples",
    "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "CAT": "Industrials",
    "GE": "Industrials",
    "XOM": "Energy",
    "CVX": "Energy",
    # Higher-beta growth
    "TSLA": "Consumer Discretionary",
    "PLTR": "Technology",
    "SHOP": "Technology",
}


def seed_universe(session) -> int:
    """Create/refresh Stock rows with real sectors. Idempotent."""
    count = 0
    for ticker, sector in UNIVERSE.items():
        try:
            Stock.get_or_create(
                session,
                ticker_symbol=ticker,
                company_name=f"{ticker}",
                sector=sector,
                exchange="NASDAQ",
                country="US",
                currency="USD",
                is_active=True,
            )
            count += 1
        except Exception as e:
            logger.warning("Could not create stock %s: %s", ticker, e)
    session.commit()
    logger.info("Universe ready: %d stocks", count)
    return count


def load_prices(session, provider: EnhancedStockDataProvider, days: int) -> int:
    """Fetch real daily price history and store it. Returns stocks loaded."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    loaded = 0
    for ticker in UNIVERSE:
        try:
            df = provider.get_stock_data(ticker, start_date=start_s, end_date=end_s)
            if df is None or df.empty:
                logger.warning("No price data for %s", ticker)
                continue
            n = bulk_insert_price_data(session, ticker, df)
            logger.info("%s: %d price rows (%d total in df)", ticker, n, len(df))
            loaded += 1
        except Exception as e:
            logger.warning("Price load failed for %s: %s", ticker, e)
    session.commit()
    logger.info("Price history loaded for %d/%d stocks", loaded, len(UNIVERSE))
    return loaded


def run_screening() -> bool:
    """Run the real screening algorithms against the loaded price data."""
    logger.info("Running screening algorithms (run_stock_screening.py --all)...")
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_stock_screening.py"),
            "--all",
            "--database-url",
            "sqlite:///maverick_mcp.db",
        ],
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap live dev dataset")
    parser.add_argument(
        "--days", type=int, default=500, help="Calendar days of price history"
    )
    parser.add_argument(
        "--no-screen", action="store_true", help="Skip running the screeners"
    )
    args = parser.parse_args()

    logger.info("Ensuring full database schema...")
    ensure_database_schema(force=True)

    with SessionLocal() as session:
        provider = EnhancedStockDataProvider(db_session=session)
        seed_universe(session)
        load_prices(session, provider, args.days)

    if not args.no_screen:
        ok = run_screening()
        if not ok:
            logger.error("Screening step failed")
            return 1

    logger.info("✅ Bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
