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
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta

logger = logging.getLogger(__name__)

# Small hand-rolled sentiment lexicon for scoring free yfinance headlines.
# Deliberately tiny and dependency-free (no NLTK/VADER, no LLM keys, no paid
# Tiingo news). Tokens are matched case-insensitively as substrings of the
# headline/summary text. This is a coarse signal, not NLP — it only needs to
# vary across tickers and lean the right direction on obvious news.
_POSITIVE_WORDS = frozenset(
    {
        "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
        "jump", "jumps", "gain", "gains", "upgrade", "upgraded", "outperform",
        "record", "strong", "growth", "profit", "wins", "win", "breakthrough",
        "bullish", "buy", "raise", "raises", "raised", "boost", "boosts",
        "top", "tops", "expand", "expands", "partnership", "approval",
        "approved", "milestone", "accelerate", "momentum", "high", "highs",
        "optimistic", "upside", "double", "positive", "success", "demand",
    }
)
_NEGATIVE_WORDS = frozenset(
    {
        "miss", "misses", "missed", "plunge", "plunges", "plummet", "slump",
        "fall", "falls", "drop", "drops", "decline", "declines", "downgrade",
        "downgraded", "underperform", "weak", "loss", "losses", "cut", "cuts",
        "warn", "warns", "warning", "lawsuit", "probe", "investigation",
        "bearish", "sell", "slash", "slashes", "layoff", "layoffs", "recall",
        "bankruptcy", "fraud", "delay", "delays", "halt", "halts", "risk",
        "concern", "concerns", "fear", "fears", "sink", "sinks", "tumble",
        "tumbles", "crash", "downside", "negative", "disappoint", "slowdown",
    }
)

# Neutral feature defaults for un-enriched / failed lookups. Kept consistent
# with the scorer's NEUTRAL map (catalyst proximity is a genuine 0.0 signal).
# Neutral defaults: unknown data is a mild negative signal, not a true neutral.
# Using 0.3 instead of 0.5 ensures unenriched candidates score lower than
# enriched ones, creating real spread instead of clustering around 62.5.
_NEUTRAL_TECHNICAL = 0.3
_NEUTRAL_SENTIMENT = 0.3
_NEUTRAL_CATALYST = 0.0

# Max combined_score produced by the screener (used for normalization).
_SCREENER_MAX_SCORE = 125.0

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
        # Normalize against screener max (125) not 100 — avoids score saturation
        # where every top name scores 1.0 and clustering results.
        "screening_score": _clamp(float(combined) / _SCREENER_MAX_SCORE),
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


@dataclass
class CatalystEvent:
    """A single upcoming catalyst (currently: scheduled earnings)."""

    symbol: str
    event_date: _date
    event_type: str = "earnings"


def _to_date(value) -> _date | None:
    """Best-effort coerce a yfinance date/Timestamp/datetime into a plain date."""
    if value is None:
        return None
    # datetime and pandas.Timestamp are both datetime subclasses -> .date().
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    # numpy datetime64 / anything else exposing a .date() callable.
    date_fn = getattr(value, "date", None)
    if callable(date_fn):
        try:
            coerced = date_fn()
        except Exception:  # noqa: BLE001
            return None
        return coerced if isinstance(coerced, _date) else None
    return None


def _earnings_dates_for(ticker: str, days_ahead: int) -> list[_date]:
    """Return upcoming earnings dates for ``ticker`` within the look-ahead window.

    Sources real dates from yfinance (``.calendar`` first, then
    ``.get_earnings_dates``). Best-effort: any failure yields an empty list.
    """
    import yfinance as yf

    today = _date.today()
    horizon = today + _timedelta(days=max(days_ahead, 1))
    found: set[_date] = set()

    tk = yf.Ticker(ticker)

    # 1) .calendar — cheapest, usually just the next scheduled report.
    try:
        cal = tk.calendar
        raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
        candidates = raw if isinstance(raw, list) else ([raw] if raw else [])
        for item in candidates:
            d = _to_date(item)
            if d is not None and today <= d <= horizon:
                found.add(d)
    except Exception as exc:  # noqa: BLE001
        logger.debug("VC loop: yf calendar failed for %s: %s", ticker, exc)

    # 2) .get_earnings_dates — DataFrame of past+future reports; keep upcoming.
    if not found:
        try:
            df = tk.get_earnings_dates(limit=12)
            index = getattr(df, "index", None)
            for item in list(index) if index is not None else []:
                d = _to_date(item)
                if d is not None and today <= d <= horizon:
                    found.add(d)
        except Exception as exc:  # noqa: BLE001
            logger.debug("VC loop: yf earnings_dates failed for %s: %s", ticker, exc)

    return sorted(found)


def _fetch_catalysts(tickers: list[str], days_ahead: int) -> dict[str, list]:
    """Best-effort fetch of upcoming catalysts keyed by ticker. Never raises.

    Sources real upcoming earnings dates from yfinance. Any per-ticker failure
    is swallowed so the loop always continues with a neutral (0.0) catalyst.
    """
    events_by_ticker: dict[str, list] = {}
    if not tickers:
        return events_by_ticker
    for ticker in tickers:
        sym = ticker.upper()
        try:
            for d in _earnings_dates_for(sym, days_ahead):
                events_by_ticker.setdefault(sym, []).append(
                    CatalystEvent(symbol=sym, event_date=d, event_type="earnings")
                )
        except Exception as exc:  # noqa: BLE001 - defensive, never raise
            logger.warning("VC loop: catalyst fetch failed for %s: %s", sym, exc)
    return events_by_ticker


def _yf_news_sentiment(ticker: str) -> float | None:
    """Score free yfinance headlines with the hand-rolled lexicon.

    Returns a signed 0-1 sentiment feature (0.5 = neutral) or ``None`` when
    there are no usable headlines — in which case the caller leaves the feature
    neutral rather than fabricating a signal. Never raises.
    """
    try:
        import yfinance as yf

        items = yf.Ticker(ticker).news or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("VC loop: yf news failed for %s: %s", ticker, exc)
        return None

    texts: list[str] = []
    for item in items:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, dict):
            title = content.get("title") or ""
            summary = content.get("summary") or content.get("description") or ""
        else:
            # Older yfinance shape: {"title": ...} at top level.
            title = item.get("title", "") if isinstance(item, dict) else ""
            summary = ""
        blob = f"{title} {summary}".strip()
        if blob:
            texts.append(blob.lower())

    if not texts:
        return None

    pos = neg = 0
    for blob in texts:
        tokens = set(_tokenize(blob))
        pos += len(tokens & _POSITIVE_WORDS)
        neg += len(tokens & _NEGATIVE_WORDS)

    total = pos + neg
    if total == 0:
        # Headlines exist but carry no lexicon signal -> genuine neutral.
        return 0.5
    # Net polarity in [-1, 1], scaled by how decisive the coverage is so a
    # single lopsided headline moves the feature less than broad agreement.
    net = (pos - neg) / total
    decisiveness = min(1.0, total / 6.0)
    return _clamp(0.5 + 0.5 * net * decisiveness)


def _tokenize(text: str) -> list[str]:
    """Split headline text into lowercase alphabetic word tokens."""
    word = []
    tokens: list[str] = []
    for ch in text:
        if ch.isalpha():
            word.append(ch)
        elif word:
            tokens.append("".join(word))
            word = []
    if word:
        tokens.append("".join(word))
    return tokens


async def _enrich(candidate: Candidate, catalysts: list, days_ahead: int) -> None:
    """Best-effort enrichment of one candidate. Mutates in place, never raises."""
    from maverick_mcp.api.routers.technical import get_full_technical_analysis
    from maverick_mcp.providers.tiingo_data import compute_momentum

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

    # Momentum — real 0-1 signal from Tiingo daily closes (was hardcoded 0.5).
    # compute_momentum returns None when there isn't enough price history, in
    # which case we leave whatever default the caller set rather than faking it.
    try:
        momentum = compute_momentum(candidate.ticker)
        if momentum is not None:
            candidate.features["momentum"] = momentum
            candidate.raw["momentum_raw"] = momentum
    except Exception as exc:
        logger.warning(
            "VC loop: momentum failed for %s: %s", candidate.ticker, exc
        )

    # News sentiment — free yfinance headlines scored with a hand-rolled
    # lexicon (Tiingo news is paid/403 and the LLM sentiment path returns
    # confidence 0 with placeholder keys). No headlines -> stays neutral.
    try:
        feature = _yf_news_sentiment(candidate.ticker)
        if feature is not None:
            candidate.features["sentiment_confidence"] = feature
            candidate.raw["sentiment"] = (
                "bullish" if feature > 0.55 else "bearish" if feature < 0.45 else "neutral"
            )
            candidate.raw["sentiment_confidence_raw"] = round(abs(feature - 0.5) * 2, 4)
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


async def gather_portfolio_candidates(
    tickers: list[str],
    *,
    on_screen: set[str] | None = None,
    on_off_screen: set[str] | None = None,
    days_ahead: int = 30,
) -> list[Candidate]:
    """Build candidates for held portfolio positions, regardless of screen status.

    Used to ensure every held position gets a conviction score even if it didn't
    surface through the screener. screening_score is set by screen status:
      on accumulate screen → 0.8   (partial credit)
      not on any screen   → 0.3   (mild negative — no screener signal)
      on bear/off screen  → 0.0   (no signal, bearish)

    All candidates are enriched (no top-N limit) since the universe is small.
    """
    on_screen = on_screen or set()
    on_off_screen = on_off_screen or set()
    candidates: list[Candidate] = []

    for ticker in tickers:
        t = ticker.upper()
        if t in on_off_screen:
            screen_signal = 0.0
        elif t in on_screen:
            screen_signal = 0.8
        else:
            screen_signal = 0.3

        features: dict[str, float] = {
            "screening_score": screen_signal,
            "momentum": 0.5,
            "technical_strength": _NEUTRAL_TECHNICAL,
            "sentiment_confidence": _NEUTRAL_SENTIMENT,
            "catalyst_proximity": _NEUTRAL_CATALYST,
        }
        cand = Candidate(
            ticker=t,
            sector=None,
            features=features,
            raw={},
        )
        candidates.append(cand)

    if candidates:
        catalysts_by_ticker = _fetch_catalysts([c.ticker for c in candidates], days_ahead)
        for cand in candidates:
            await _enrich(cand, catalysts_by_ticker.get(cand.ticker, []), days_ahead)

    return candidates


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
