"""Tiingo live-price and momentum helpers — the always-on price layer.

Static API key (``TIINGO_API_KEY``), so unlike the Schwab OAuth link this never
expires and needs no weekly re-auth. Used for:

  * intraday live quotes for the Telegram briefs / watcher (Schwab is the
    authoritative morning source; Tiingo drives the rest of the day), and
  * a real ``momentum`` scoring feature (previously dead — hardcoded 0.5).

Import-safe: no ``load_dotenv`` at import (library-module contract). The daily
run and server both populate ``TIINGO_API_KEY`` in the environment already.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, datetime, timedelta

import requests

logger = logging.getLogger("maverick_mcp.tiingo_data")

_IEX = "https://api.tiingo.com/iex"
_DAILY = "https://api.tiingo.com/tiingo/daily"
_TIMEOUT = 15.0


def _key() -> str | None:
    k = os.getenv("TIINGO_API_KEY", "").strip()
    return k or None


def get_live_prices(tickers: list[str]) -> dict[str, float]:
    """Return {TICKER: price} from Tiingo IEX. Intraday=last trade; after hours
    falls back to prevClose. Missing/failed tickers are omitted (never silently
    zero). Empty dict if no key or the call fails."""
    key = _key()
    if not key or not tickers:
        return {}
    syms = ",".join(sorted({t.upper() for t in tickers}))
    try:
        resp = requests.get(
            f"{_IEX}/", params={"tickers": syms, "token": key}, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        out: dict[str, float] = {}
        for q in resp.json():
            tk = str(q.get("ticker", "")).upper()
            price = q.get("last") or q.get("tngoLast") or q.get("prevClose")
            if tk and price is not None:
                out[tk] = float(price)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tiingo live prices failed for %s: %s", syms, exc)
        return {}


def get_daily_closes(ticker: str, lookback_days: int = 40) -> list[float]:
    """Return chronological daily closes for ``ticker`` (oldest→newest)."""
    key = _key()
    if not key:
        return []
    start = (datetime.now(UTC) - timedelta(days=lookback_days)).date().isoformat()
    try:
        resp = requests.get(
            f"{_DAILY}/{ticker.upper()}/prices",
            params={"startDate": start, "token": key},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        bars = resp.json()
        return [float(b["close"]) for b in bars if b.get("close") is not None]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tiingo daily history failed for %s: %s", ticker, exc)
        return []


def compute_momentum(ticker: str, closes: list[float] | None = None) -> float | None:
    """Normalized 0-1 momentum for the scorer (0.5 = neutral).

    Blends 10-day trailing return and price-vs-20-day-MA, squashed through a
    tanh so the feature lands in [0,1]. Returns ``None`` (caller keeps neutral)
    when there isn't enough history — but that is logged, not silently 0.5.
    """
    c = closes if closes is not None else get_daily_closes(ticker)
    if len(c) < 21:
        logger.info("momentum: insufficient history for %s (%d bars)", ticker, len(c))
        return None
    ret10 = c[-1] / c[-11] - 1.0
    ma20 = sum(c[-20:]) / 20.0
    vs_ma = (c[-1] / ma20 - 1.0) if ma20 else 0.0
    raw = 0.6 * ret10 + 0.4 * vs_ma
    # tanh squash: ~±12% move saturates toward 0/1; 0 move → 0.5.
    return round(0.5 + 0.5 * math.tanh(raw * 6.0), 4)


def momentum_batch(tickers: list[str]) -> dict[str, float]:
    """Best-effort momentum for many tickers. Omits those lacking history."""
    out: dict[str, float] = {}
    for t in tickers:
        m = compute_momentum(t)
        if m is not None:
            out[t.upper()] = m
    return out
