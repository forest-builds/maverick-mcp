"""Portfolio construction math — the Dalio diversification layer.

Pure, deterministic, no I/O. Given position weights, a correlation matrix, and
per-name volatilities, compute the metrics that conviction sizing alone ignores:

  - effective_bets:  how many *independent* bets you really have (Dalio's "15")
  - clusters:        correlated groups that move together (one real bet each)
  - cluster_weights: how concentrated each correlated group is
  - risk_contribution: each position's share of total portfolio risk (not dollars)

The key insight (Dalio): five space stocks at 5% each are not five 5% bets — they
are one ~25% bet. Conviction sizing must be penalized by correlation, and risk
must be balanced by volatility, or the portfolio is far less diversified than it
looks on a position-count basis.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

from typing import Any


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """Normalize weights to sum to 1.0 (drops non-positive)."""
    pos = {t: w for t, w in weights.items() if w > 0}
    total = sum(pos.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in pos.items()}


def effective_bets(
    weights: dict[str, float],
    corr: dict[str, dict[str, float]],
) -> float:
    """Effective number of independent bets ≈ diversification ratio squared.

    Computed as 1 / (wᵀ R w) on normalized weights, assuming roughly equal
    per-name volatility (correlation, not covariance). Interpretation:
      - all positions uncorrelated  → effective_bets ≈ Herfindahl N (max)
      - all positions perfectly corr → effective_bets ≈ 1 (one real bet)

    Dalio's target for a well-diversified book is ~15.
    """
    w = _normalize(weights)
    if not w:
        return 0.0
    tickers = list(w)
    quad = 0.0
    for i in tickers:
        for j in tickers:
            r = 1.0 if i == j else corr.get(i, {}).get(j, 0.0)
            quad += w[i] * w[j] * r
    if quad <= 0:
        return float(len(tickers))
    return 1.0 / quad


def cluster_positions(
    corr: dict[str, dict[str, float]],
    tickers: list[str],
    threshold: float = 0.6,
) -> list[list[str]]:
    """Group tickers into correlated clusters via union-find on the corr graph.

    Two tickers share a cluster if their pairwise correlation ≥ threshold.
    Transitive: A~B and B~C puts A, B, C together even if A~C is below the line.
    Each returned cluster is roughly one independent bet.
    """
    parent = {t: t for t in tickers}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            r = corr.get(a, {}).get(b, 0.0)
            if r >= threshold:
                union(a, b)

    groups: dict[str, list[str]] = {}
    for t in tickers:
        groups.setdefault(find(t), []).append(t)
    # Largest clusters first
    return sorted(groups.values(), key=len, reverse=True)


def cluster_weights(
    clusters: list[list[str]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Total portfolio weight held in each correlated cluster, largest first."""
    w = _normalize(weights)
    out = []
    for members in clusters:
        total = sum(w.get(t, 0.0) for t in members)
        out.append({
            "members": sorted(members, key=lambda t: w.get(t, 0.0), reverse=True),
            "size": len(members),
            "weight_pct": round(total * 100, 1),
        })
    return sorted(out, key=lambda c: c["weight_pct"], reverse=True)


def risk_contributions(
    weights: dict[str, float],
    vols: dict[str, float],
    corr: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Each position's share of total portfolio risk (% of variance contribution).

    Uses marginal risk contribution: MRCᵢ = wᵢ · (Σⱼ wⱼ σᵢ σⱼ ρᵢⱼ). Normalized to
    sum to 100%. A name can be a small dollar weight but a large risk share if it
    is highly volatile or highly correlated with the rest of the book.
    """
    w = _normalize(weights)
    if not w:
        return {}
    tickers = list(w)
    # Default vol = median of known vols (so unknown names aren't zero-risk)
    known = [v for v in vols.values() if v and v > 0]
    default_vol = (sorted(known)[len(known) // 2] if known else 1.0)

    def vol(t: str) -> float:
        v = vols.get(t)
        return v if v and v > 0 else default_vol

    contrib: dict[str, float] = {}
    for i in tickers:
        s = 0.0
        for j in tickers:
            r = 1.0 if i == j else corr.get(i, {}).get(j, 0.0)
            s += w[j] * vol(i) * vol(j) * r
        contrib[i] = w[i] * s
    total = sum(contrib.values())
    if total <= 0:
        return {t: round(w[t] * 100, 1) for t in tickers}
    return {t: round(c / total * 100, 1) for t, c in contrib.items()}


def apply_cluster_caps(
    standalone_targets: dict[str, float],
    clusters: list[list[str]],
    cluster_cap: float = 0.20,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Scale down conviction targets so no correlated cluster exceeds cluster_cap.

    Within an over-budget cluster, members are scaled proportionally — conviction
    ranking is preserved, the whole group just fits under the cap. Returns the
    adjusted targets plus a log of which clusters were capped (for the brief).
    """
    adjusted = dict(standalone_targets)
    caps_applied: list[dict[str, Any]] = []
    for members in clusters:
        if len(members) < 2:
            continue
        cluster_total = sum(standalone_targets.get(t, 0.0) for t in members)
        if cluster_total > cluster_cap and cluster_total > 0:
            scale = cluster_cap / cluster_total
            for t in members:
                adjusted[t] = standalone_targets.get(t, 0.0) * scale
            caps_applied.append({
                "members": sorted(members),
                "wanted_pct": round(cluster_total * 100, 1),
                "capped_pct": round(cluster_cap * 100, 1),
                "scale_factor": round(scale, 3),
            })
    return adjusted, caps_applied


def risk_parity_tilt(
    targets: dict[str, float],
    vols: dict[str, float],
    *,
    floor: float = 0.7,
    ceiling: float = 1.3,
) -> dict[str, float]:
    """Mild inverse-vol tilt: lower a name's target if it's more volatile than peers.

    Scales each target by clamp(median_vol / name_vol, floor, ceiling), then
    renormalizes so the total invested weight is unchanged. Conviction still
    dominates; this only balances risk *within* the conviction-derived sizing so
    one hyper-volatile name doesn't quietly dominate portfolio risk.
    """
    known = [v for v in vols.values() if v and v > 0]
    if not known or not targets:
        return dict(targets)
    median_vol = sorted(known)[len(known) // 2]

    tilted: dict[str, float] = {}
    for t, tgt in targets.items():
        v = vols.get(t)
        if v and v > 0:
            factor = max(floor, min(ceiling, median_vol / v))
        else:
            factor = 1.0
        tilted[t] = tgt * factor

    # Renormalize to preserve total invested weight
    before = sum(targets.values())
    after = sum(tilted.values())
    if after > 0 and before > 0:
        k = before / after
        tilted = {t: v * k for t, v in tilted.items()}
    return tilted


def diversification_grade(effective_n: float, target: float = 15.0) -> dict[str, Any]:
    """Letter grade + message for how diversified the book is vs Dalio's target."""
    ratio = effective_n / target if target > 0 else 0
    if ratio >= 0.8:
        grade, msg = "A", "Well diversified — near the 15-bet target."
    elif ratio >= 0.6:
        grade, msg = "B", "Reasonably diversified — room to spread risk further."
    elif ratio >= 0.4:
        grade, msg = "C", "Concentrated — correlated clusters are doing the work of one bet."
    elif ratio >= 0.25:
        grade, msg = "D", "Heavily concentrated — few independent bets despite many positions."
    else:
        grade, msg = "F", "Single-factor book — almost everything moves together."
    return {
        "grade": grade,
        "effective_bets": round(effective_n, 1),
        "target_bets": target,
        "message": msg,
    }


def diversification_report(
    weights: dict[str, float],
    corr: dict[str, dict[str, float]],
    vols: dict[str, float] | None = None,
    *,
    cluster_threshold: float = 0.6,
) -> dict[str, Any]:
    """One-call report combining every metric — used by the tool + brief."""
    vols = vols or {}
    tickers = [t for t, w in weights.items() if w > 0]
    eff = effective_bets(weights, corr)
    clusters = cluster_positions(corr, tickers, threshold=cluster_threshold)
    cw = cluster_weights(clusters, weights)
    rc = risk_contributions(weights, vols, corr)
    multi = [c for c in cw if c["size"] > 1]
    top_risk = sorted(rc.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "grade": diversification_grade(eff),
        "clusters": cw,
        "correlated_clusters": multi,
        "largest_cluster": multi[0] if multi else None,
        "risk_contribution": rc,
        "top_risk_contributors": [{"ticker": t, "risk_pct": p} for t, p in top_risk],
        "position_count": len(tickers),
    }
