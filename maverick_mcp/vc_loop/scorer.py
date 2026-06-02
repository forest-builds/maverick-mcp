"""Deterministic conviction scorer for the VC loop.

Pure, deterministic, no I/O and no LLM. Given a feature dict of normalized
0-1 values, produce a 0-100 conviction score, a weighted breakdown, and an
advisory action band.

DISCLAIMER: educational/research output only, not investment advice and not
an execution instruction.
"""

from __future__ import annotations

# Weights for each feature, summing to 1.0.
WEIGHTS: dict[str, float] = {
    "screening_score": 0.35,
    "technical_strength": 0.25,
    "sentiment_confidence": 0.20,
    "catalyst_proximity": 0.10,
    "momentum": 0.10,
}

# Neutral defaults used when a feature is missing. Most features are centered
# at 0.5 (unknown / neutral); catalyst proximity defaults to 0.0 (no known
# catalyst is genuinely a zero signal, not a neutral one).
NEUTRAL: dict[str, float] = {
    "screening_score": 0.5,
    "technical_strength": 0.5,
    "sentiment_confidence": 0.5,
    "catalyst_proximity": 0.0,
    "momentum": 0.5,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def score_candidate(features: dict[str, float]) -> tuple[float, dict]:
    """Score a candidate from its normalized feature dict.

    Args:
        features: Mapping of feature name -> normalized 0-1 value. Missing
            features fall back to :data:`NEUTRAL`. Values outside 0-1 are
            clamped.

    Returns:
        A ``(conviction, breakdown)`` tuple where ``conviction`` is a float in
        ``[0, 100]`` rounded to one decimal place, and ``breakdown`` maps each
        feature to a dict with its ``weight``, the (clamped) ``value`` used,
        and its ``contribution`` to the final 0-100 score.
    """
    breakdown: dict[str, dict[str, float]] = {}
    total = 0.0

    for key, weight in WEIGHTS.items():
        raw = features.get(key, NEUTRAL[key])
        value = _clamp(float(raw))
        contribution = round(100 * weight * value, 2)
        breakdown[key] = {
            "weight": weight,
            "value": round(value, 4),
            "contribution": contribution,
        }
        total += 100 * weight * value

    conviction = round(_clamp(total, 0.0, 100.0), 1)
    return conviction, breakdown


def proposed_action(conviction: float) -> str:
    """Map a 0-100 conviction score to an advisory action band.

    Advisory text only; this never triggers execution.
    """
    if conviction >= 75:
        return "BUY (high conviction)"
    if conviction >= 60:
        return "Accumulate"
    if conviction >= 45:
        return "Watch"
    return "Pass"
