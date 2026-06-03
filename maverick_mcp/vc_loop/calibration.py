"""Outcome review and calibration for the VC loop.

Phase 2 closes the loop: proposed theses become observed outcomes, calibration
shows whether conviction is trustworthy, and feature attribution suggests which
signals deserve more or less weight.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import sqrt
from typing import Any

from sqlalchemy import asc
from sqlalchemy.orm import Session

from maverick_mcp.data.models import PriceCache, Stock
from maverick_mcp.vc_loop.models import ThesisLedger
from maverick_mcp.vc_loop.scorer import WEIGHTS


@dataclass(frozen=True)
class PricePoint:
    """A price observation used to review a thesis outcome."""

    observed_date: date
    close_price: float


def _as_date(value: date | datetime) -> date:
    """Normalize date/datetime values to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    return value


def _price_on_or_after(
    session: Session, ticker: str, target_date: date
) -> PricePoint | None:
    """Return the first close price for ``ticker`` on or after ``target_date``."""
    row = (
        session.query(PriceCache)
        .join(Stock, PriceCache.stock_id == Stock.stock_id)
        .filter(Stock.ticker_symbol == ticker.upper())
        .filter(PriceCache.date >= target_date)
        .filter(PriceCache.close_price.isnot(None))
        .order_by(asc(PriceCache.date))
        .first()
    )
    if row is None:
        return None
    return PricePoint(
        observed_date=row.date,
        close_price=float(row.close_price),
    )


def _closed_rows(rows: list[ThesisLedger]) -> list[ThesisLedger]:
    """Return rows with usable outcome payloads."""
    return [
        row
        for row in rows
        if row.status == "closed"
        and isinstance(row.outcome, dict)
        and "win" in row.outcome
    ]


def brier_score(rows: list[ThesisLedger]) -> float | None:
    """Compute Brier score over closed theses.

    Prediction is ``conviction / 100`` and the realized outcome is ``win`` as
    1.0/0.0. Lower is better.
    """
    closed = _closed_rows(rows)
    if not closed:
        return None
    total = 0.0
    for row in closed:
        predicted = max(0.0, min(1.0, float(row.conviction) / 100.0))
        actual = 1.0 if row.outcome and row.outcome.get("win") else 0.0
        total += (predicted - actual) ** 2
    return round(total / len(closed), 4)


def calibration_bins(
    rows: list[ThesisLedger], *, bin_size: int = 10
) -> list[dict[str, Any]]:
    """Group closed theses into conviction buckets."""
    closed = _closed_rows(rows)
    buckets: dict[int, list[ThesisLedger]] = {}
    for row in closed:
        bucket = int(float(row.conviction) // bin_size) * bin_size
        bucket = min(bucket, 100 - bin_size)
        buckets.setdefault(bucket, []).append(row)

    result: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        bucket_rows = buckets[bucket]
        wins = sum(1 for row in bucket_rows if row.outcome and row.outcome.get("win"))
        avg_prediction = sum(float(row.conviction) / 100.0 for row in bucket_rows) / len(
            bucket_rows
        )
        hit_rate = wins / len(bucket_rows)
        result.append(
            {
                "bucket": f"{bucket}-{bucket + bin_size}",
                "count": len(bucket_rows),
                "avg_prediction": round(avg_prediction, 4),
                "hit_rate": round(hit_rate, 4),
                "calibration_error": round(abs(avg_prediction - hit_rate), 4),
            }
        )
    return result


def edge_attribution(rows: list[ThesisLedger]) -> dict[str, dict[str, Any]]:
    """Estimate which normalized features predict wins or returns.

    This intentionally stays simple and explainable: each feature receives a
    Pearson correlation to binary wins and realized returns over closed rows.
    """
    closed = [
        row
        for row in _closed_rows(rows)
        if isinstance(row.features, dict) and isinstance(row.outcome, dict)
    ]
    if not closed:
        return {}

    keys = sorted({key for row in closed for key in (row.features or {})})
    attribution: dict[str, dict[str, Any]] = {}
    wins = [1.0 if row.outcome and row.outcome.get("win") else 0.0 for row in closed]
    returns = [
        float(row.outcome.get("return_pct", 0.0)) if row.outcome else 0.0
        for row in closed
    ]

    for key in keys:
        values = [float((row.features or {}).get(key, 0.0)) for row in closed]
        attribution[key] = {
            "sample_count": len(closed),
            "avg_value": round(sum(values) / len(values), 4),
            "win_correlation": _correlation(values, wins),
            "return_correlation": _correlation(values, returns),
        }
    return attribution


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    """Return Pearson correlation, or None when variance is zero."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    avg_x = sum(xs) / len(xs)
    avg_y = sum(ys) / len(ys)
    dx = [x - avg_x for x in xs]
    dy = [y - avg_y for y in ys]
    denom = sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom == 0:
        return None
    return round(sum(x * y for x, y in zip(dx, dy, strict=True)) / denom, 4)


def learned_weights(
    rows: list[ThesisLedger],
    *,
    prior_weights: dict[str, float] | None = None,
    min_samples: int = 5,
    blend: float = 0.35,
) -> dict[str, Any]:
    """Return explainable candidate weights learned from closed outcomes.

    Positive predictive features receive more weight. Non-predictive or
    negatively predictive features keep a small floor so the scorer remains
    stable. Learned weights are blended with prior weights by default.
    """
    prior = prior_weights or WEIGHTS
    closed = _closed_rows(rows)
    if len(closed) < min_samples:
        return {
            "status": "insufficient_data",
            "sample_count": len(closed),
            "min_samples": min_samples,
            "weights": dict(prior),
        }

    attribution = edge_attribution(closed)
    raw_scores: dict[str, float] = {}
    for key in prior:
        attr = attribution.get(key, {})
        win_corr = attr.get("win_correlation") or 0.0
        return_corr = attr.get("return_correlation") or 0.0
        # Reward positive edge; keep floor for stability and unknowns.
        raw_scores[key] = max(0.05, (max(win_corr, 0.0) + max(return_corr, 0.0)) / 2)

    total_raw = sum(raw_scores.values()) or 1.0
    purely_learned = {key: raw_scores[key] / total_raw for key in prior}
    learned = {
        key: (1.0 - blend) * prior[key] + blend * purely_learned[key]
        for key in prior
    }
    total = sum(learned.values()) or 1.0
    normalized = {key: round(value / total, 4) for key, value in learned.items()}

    # Correct rounding drift so callers can use the result directly.
    drift = round(1.0 - sum(normalized.values()), 4)
    if normalized:
        largest = max(normalized, key=normalized.get)
        normalized[largest] = round(normalized[largest] + drift, 4)

    return {
        "status": "ok",
        "sample_count": len(closed),
        "weights": normalized,
        "attribution": attribution,
    }


def apply_learned_weights(learning_result: dict[str, Any]) -> dict[str, Any]:
    """Apply learned weights to the scorer's mutable ``WEIGHTS`` map.

    Returns a small status payload so the review tool can report whether the
    runtime scorer changed. This is process-local; durable persistence can land
    later once enough outcomes prove the learning loop is worth saving.
    """
    if learning_result.get("status") != "ok":
        return {
            "status": "skipped",
            "reason": learning_result.get("status", "unknown"),
            "weights": dict(WEIGHTS),
        }

    weights = learning_result.get("weights")
    if not isinstance(weights, dict) or not weights:
        return {"status": "skipped", "reason": "missing_weights", "weights": dict(WEIGHTS)}

    WEIGHTS.clear()
    WEIGHTS.update({str(key): float(value) for key, value in weights.items()})
    return {"status": "updated", "weights": dict(WEIGHTS)}


def review_theses(
    session: Session,
    *,
    days_after: int = 30,
    limit: int = 100,
    min_return_pct: float = 0.0,
    thesis_ids: list[int] | None = None,
    update_weights: bool = True,
) -> dict[str, Any]:
    """Close due theses by comparing later prices against thesis-date prices."""
    target_statuses = {"proposed", "open"}
    query = session.query(ThesisLedger).filter(ThesisLedger.status.in_(target_statuses))
    if thesis_ids:
        query = query.filter(ThesisLedger.id.in_(thesis_ids))
    rows = query.order_by(ThesisLedger.thesis_date.asc()).limit(limit).all()

    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    today = date.today()

    for row in rows:
        thesis_date = _as_date(row.thesis_date)
        target_date = thesis_date + timedelta(days=days_after)
        if target_date > today:
            skipped.append(
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "reason": "not_due",
                    "target_date": target_date.isoformat(),
                }
            )
            continue

        entry = _price_on_or_after(session, row.ticker, thesis_date)
        exit_ = _price_on_or_after(session, row.ticker, target_date)
        if entry is None or exit_ is None:
            skipped.append(
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "reason": "missing_price_data",
                    "target_date": target_date.isoformat(),
                }
            )
            continue

        return_pct = ((exit_.close_price - entry.close_price) / entry.close_price) * 100
        win = return_pct >= min_return_pct
        outcome = {
            "reviewed_at": datetime.now().isoformat(),
            "days_after": days_after,
            "entry_date": entry.observed_date.isoformat(),
            "entry_price": round(entry.close_price, 4),
            "exit_date": exit_.observed_date.isoformat(),
            "exit_price": round(exit_.close_price, 4),
            "return_pct": round(return_pct, 4),
            "min_return_pct": min_return_pct,
            "win": win,
        }
        row.outcome = outcome
        row.status = "closed"
        reviewed.append(
            {
                "id": row.id,
                "ticker": row.ticker,
                "conviction": row.conviction,
                "outcome": outcome,
            }
        )

    session.commit()

    all_rows = session.query(ThesisLedger).all()
    learning_result = learned_weights(all_rows)
    weight_update = (
        apply_learned_weights(learning_result)
        if update_weights
        else {"status": "disabled", "weights": dict(WEIGHTS)}
    )
    return {
        "status": "ok",
        "days_after": days_after,
        "reviewed_count": len(reviewed),
        "skipped_count": len(skipped),
        "reviewed": reviewed,
        "skipped": skipped,
        "calibration": {
            "brier_score": brier_score(all_rows),
            "bins": calibration_bins(all_rows),
            "edge_attribution": edge_attribution(all_rows),
            "learned_weights": learning_result,
            "weight_update": weight_update,
        },
    }
