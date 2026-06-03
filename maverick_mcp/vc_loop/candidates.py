"""Candidate gathering for the VC loop.

Sources candidate tickers from the screening routers and best-effort enriches
the top N with technical strength, news sentiment, and upcoming catalysts.

Every external call is wrapped so that a failure or error dict degrades to a
neutral feature value and the loop continues. This module does NOT score
candidates (see :mod:`maverick_mcp.vc_loop.scorer`) and performs no DB writes.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Neutral feature defaults for un-enriched / failed lookups. Kept consistent
# with the scorer's NEUTRAL map (catalyst proximity is a genuine 0.0 signal).
_NEUTRAL_TECHNICAL = 0.5
_NEUTRAL_SENTIMENT = 0.5
_NEUTRAL_CATALYST = 0.0

# Map textual technical outlook -> 0-1 strength, used when the technical
# analysis returns a string outlook instead of a structured object.
_OUTLOOK_STRENGTH = {
    "strongly bullish": 1.0,
    "moderately bullish": 0.75,
    "neutral": 0.5,
    "moderately bearish": 0.25,
    "strongly bearish": 0.0,
}

_STRATEGY_FNS = {
    "maverick_bullish": "get_maverick_stocks",
    "maverick_bearish": "get_maverick_bear_stocks",
    "supply_demand": "get_supply_demand_breakouts",
}


@dataclass
class Candidate:
    """A single VC-loop candidate.

    Attributes:
        ticker: Uppercased ticker symbol.
        sector: Optional sector label (not guaranteed by screening data).
        features: Normalized 0-1 feature values consumed by the scorer. Keys:
            ``screening_score``, ``momentum``, ``technical_strength``,
            ``sentiment_confidence``, ``catalyst_proximity``.
        raw: Raw extras kept for reference (original screening dict, trend,
            sentiment label, catalyst events, etc.).
    """

    ticker: str
    sector: str | None = None
    features: dict[str, float] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _pick_screening_fn(strategy: str):
    """Return the screening callable for ``strategy`` (default bullish)."""
    from maverick_mcp.api.routers import screening

    fn_name = _STRATEGY_FNS.get(strategy, "get_maverick_stocks")
    return getattr(screening, fn_name)


def _base_candidate(stock: dict) -> Candidate | None:
    """Build a base Candidate from a screening ``to_dict`` entry."""
    ticker = stock.get("ticker")
    if not ticker:
        return None

    combined = stock.get("combined_score") or 0
    momentum = stock.get("momentum_score") or 0

    features = {
        "screening_score": _clamp(float(combined) / 100.0),
        "momentum": _clamp(float(momentum) / 100.0),
        "technical_strength": _NEUTRAL_TECHNICAL,
        "sentiment_confidence": _NEUTRAL_SENTIMENT,
        "catalyst_proximity": _NEUTRAL_CATALYST,
    }
    return Candidate(
        ticker=str(ticker).upper(),
        sector=stock.get("sector"),
        features=features,
        raw={"screening": stock},
    )


def _technical_strength(result: dict) -> float | None:
    """Extract a 0-1 strength from a technical-analysis result, or None."""
    if not isinstance(result, dict) or result.get("status") == "error":
        return None
    outlook = result.get("outlook")
    # Structured outlook: {"strength": 0-1, ...}
    if isinstance(outlook, dict) and "strength" in outlook:
        return _clamp(float(outlook["strength"]))
    # String outlook (current implementation): map known phrases.
    if isinstance(outlook, str):
        return _OUTLOOK_STRENGTH.get(outlook.strip().lower())
    # Fall back to trend if available.
    trend = result.get("trend")
    if isinstance(trend, str):
        if trend == "uptrend":
            return 0.75
        if trend == "downtrend":
            return 0.25
        return 0.5
    return None


def _sentiment_feature(result: dict) -> float | None:
    """Map a sentiment result to a signed 0-1 feature.

    Bullish pushes toward 1.0, bearish toward 0.0, scaled by confidence and
    re-centered on 0.5 (neutral). Returns None on failure.
    """
    if not isinstance(result, dict) or result.get("status") == "error":
        return None
    sentiment = str(result.get("sentiment", "neutral")).lower()
    confidence = result.get("confidence", 0.5)
    try:
        confidence = _clamp(float(confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    sign = 0.0
    if sentiment == "bullish":
        sign = 1.0
    elif sentiment == "bearish":
        sign = -1.0
    # 0.5 +/- (confidence/2): neutral or zero-confidence -> 0.5.
    return _clamp(0.5 + sign * confidence * 0.5)


def _catalyst_proximity(event_date, days_ahead: int) -> float:
    """Closer catalysts score higher; outside the window scores 0.0."""
    from datetime import date as _date

    if event_date is None:
        return _NEUTRAL_CATALYST
    try:
        today = _date.today()
        delta = (event_date - today).days
    except TypeError:
        return 1.0  # have a catalyst but can't compute distance -> treat as present
    if delta < 0 or delta > days_ahead:
        return _NEUTRAL_CATALYST
    # Linear: today -> 1.0, edge of window -> ~1/days_ahead.
    return _clamp(1.0 - (delta / max(days_ahead, 1)))


def _fetch_catalysts(tickers: list[str], days_ahead: int) -> dict[str, list]:
    """Best-effort fetch of upcoming catalysts keyed by ticker. Never raises."""
    events_by_ticker: dict[str, list] = {}
    if not tickers:
        return events_by_ticker
    try:
        from maverick_mcp.data.models import SessionLocal
        from maverick_mcp.services.watchlist.catalysts import CatalystTracker

        with SessionLocal() as session:
            tracker = CatalystTracker(session)
            events = tracker.get_upcoming(symbols=tickers, days_ahead=days_ahead)
            for ev in events or []:
                sym = str(getattr(ev, "symbol", "")).upper()
                if sym:
                    events_by_ticker.setdefault(sym, []).append(ev)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("VC loop: catalyst fetch failed: %s", exc)
    return events_by_ticker


async def _enrich(candidate: Candidate, catalysts: list, days_ahead: int) -> None:
    """Best-effort enrichment of one candidate. Mutates in place, never raises."""
    from maverick_mcp.api.routers.news_sentiment_enhanced import (
        get_news_sentiment_enhanced,
    )
    from maverick_mcp.api.routers.technical import get_full_technical_analysis

    # Technical strength.
    try:
        tech = await get_full_technical_analysis(candidate.ticker)
        strength = _technical_strength(tech)
        if strength is not None:
            candidate.features["technical_strength"] = strength
            candidate.raw["trend"] = tech.get("trend")
            candidate.raw["outlook"] = tech.get("outlook")
    except Exception as exc:
        logger.warning(
            "VC loop: technical analysis failed for %s: %s", candidate.ticker, exc
        )

    # News sentiment.
    try:
        sent = await get_news_sentiment_enhanced(candidate.ticker)
        feature = _sentiment_feature(sent)
        if feature is not None:
            candidate.features["sentiment_confidence"] = feature
            candidate.raw["sentiment"] = sent.get("sentiment")
            candidate.raw["sentiment_confidence_raw"] = sent.get("confidence")
    except Exception as exc:
        logger.warning(
            "VC loop: sentiment failed for %s: %s", candidate.ticker, exc
        )

    # Catalysts (already fetched in bulk; just compute proximity here).
    try:
        if catalysts:
            nearest = min(
                catalysts,
                key=lambda e: getattr(e, "event_date", None) or _far_date(),
            )
            candidate.features["catalyst_proximity"] = _catalyst_proximity(
                getattr(nearest, "event_date", None), days_ahead
            )
            candidate.raw["catalysts"] = [
                {
                    "event_type": getattr(e, "event_type", None),
                    "event_date": getattr(e, "event_date", None),
                }
                for e in catalysts
            ]
    except Exception as exc:
        logger.warning(
            "VC loop: catalyst proximity failed for %s: %s", candidate.ticker, exc
        )


def _far_date():
    from datetime import date as _date

    return _date.max


async def gather_candidates(
    *,
    strategy: str = "maverick_bullish",
    limit: int = 20,
    enrich_top_n: int = 10,
    days_ahead: int = 30,
) -> list[Candidate]:
    """Gather (but do not score) VC-loop candidates.

    Args:
        strategy: One of ``maverick_bullish``, ``maverick_bearish``,
            ``supply_demand``. Unknown values default to bullish.
        limit: Max stocks to pull from the screen.
        enrich_top_n: Only the top N screened candidates are enriched with
            technical/sentiment/catalyst data (to bound cost). The rest keep
            neutral feature defaults.
        days_ahead: Catalyst look-ahead window in days.

    Returns:
        A list of :class:`Candidate` in screening order. Never raises for a
        single failed source — degrades to neutral defaults instead.
    """
    screening_fn = _pick_screening_fn(strategy)

    try:
        result = screening_fn(limit=limit)
    except Exception as exc:
        logger.warning("VC loop: screening call failed for %s: %s", strategy, exc)
        return []

    if not isinstance(result, dict) or result.get("status") == "error":
        logger.warning("VC loop: screening returned error for %s: %s", strategy, result)
        return []

    candidates: list[Candidate] = []
    for stock in result.get("stocks", []):
        cand = _base_candidate(stock)
        if cand is not None:
            candidates.append(cand)

    if not candidates:
        return []

    top = candidates[: max(0, enrich_top_n)]
    top_tickers = [c.ticker for c in top]
    catalysts_by_ticker = _fetch_catalysts(top_tickers, days_ahead)

    for cand in top:
        await _enrich(
            cand, catalysts_by_ticker.get(cand.ticker, []), days_ahead
        )

    return candidates
