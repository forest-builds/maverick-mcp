"""Unit tests for the deterministic VC-loop scorer."""

from __future__ import annotations

import pytest

from maverick_mcp.vc_loop.scorer import (
    NEUTRAL,
    WEIGHTS,
    proposed_action,
    score_candidate,
)


def test_weights_sum_to_one():
    assert pytest.approx(sum(WEIGHTS.values()), abs=1e-9) == 1.0


def test_all_max_features_give_100():
    features = dict.fromkeys(WEIGHTS, 1.0)
    conviction, breakdown = score_candidate(features)
    assert conviction == 100.0
    # Each contribution equals weight * 100.
    for key, weight in WEIGHTS.items():
        assert breakdown[key]["contribution"] == pytest.approx(weight * 100, abs=0.01)


def test_all_zero_features_give_zero():
    features = dict.fromkeys(WEIGHTS, 0.0)
    conviction, _ = score_candidate(features)
    assert conviction == 0.0


def test_missing_features_use_neutral_defaults():
    # Empty feature dict -> every feature falls back to NEUTRAL.
    conviction, breakdown = score_candidate({})
    expected = round(100 * sum(WEIGHTS[k] * NEUTRAL[k] for k in WEIGHTS), 1)
    assert conviction == expected
    for key in WEIGHTS:
        assert breakdown[key]["value"] == pytest.approx(NEUTRAL[key], abs=1e-9)


def test_breakdown_contributions_sum_to_conviction():
    features = {
        "screening_score": 0.8,
        "technical_strength": 0.6,
        "sentiment_confidence": 0.7,
        "catalyst_proximity": 0.5,
        "momentum": 0.9,
    }
    conviction, breakdown = score_candidate(features)
    total = sum(comp["contribution"] for comp in breakdown.values())
    assert total == pytest.approx(conviction, abs=0.1)


def test_values_are_clamped():
    features = dict.fromkeys(WEIGHTS, 5.0)  # out of range high
    conviction, breakdown = score_candidate(features)
    assert conviction == 100.0
    for comp in breakdown.values():
        assert comp["value"] == 1.0

    features_low = dict.fromkeys(WEIGHTS, -3.0)
    conviction_low, _ = score_candidate(features_low)
    assert conviction_low == 0.0


@pytest.mark.parametrize(
    "conviction,expected",
    [
        (100.0, "BUY (high conviction)"),
        (75.0, "BUY (high conviction)"),
        (74.9, "Accumulate"),
        (60.0, "Accumulate"),
        (59.9, "Watch"),
        (45.0, "Watch"),
        (44.9, "Pass"),
        (0.0, "Pass"),
    ],
)
def test_proposed_action_bands(conviction, expected):
    assert proposed_action(conviction) == expected
