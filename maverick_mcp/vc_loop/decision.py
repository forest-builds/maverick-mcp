"""The trader decision layer — turns conviction scores into sized, reasoned trades.

The scorer answers "how good is this name?" This module answers the harder,
money-making question: "given my whole book, my cash, the regime, and where each
thesis stands — what do I actually DO, and how much?"

It encodes the discipline the backtest proved was missing. Recall the finding:
the old ``proposed_action`` bought the highest conviction names *blind*, and that
"Accumulate" bucket was the WORST performer (−3.36% excess) — it averaged down
into falling knives during a risk-off, mean-reverting tape. The rules here exist
to stop exactly that:

  * Size by conviction AND risk (inverse-volatility tilt) — equal risk, not
    equal dollars, and CAPPED past CONVICTION_SIZING_CAP (see below — do not
    size up into the same overconfident top band that made "Accumulate" the
    worst-performing bucket).
  * Respect the regime — in risk-off, raise the cash target and the bar to add.
  * Do not ADD to a name whose conviction just broke materially — that is the
    knife-catch. (A fresh drop alone does NOT force selling an existing
    position; see the backtest note on THESIS_BREAK_DROP below.)
  * Let winners run — do not trim a name that is merely up if its thesis holds.
  * Cash is a position — only deploy when a name is both high-conviction AND
    underweight versus its target.

Every rule here was checked against 1,882 real 7-day thesis outcomes via
``scripts/backtest_decision_layer.py`` before being trusted — two of the three
originally-drafted rules failed that check and were corrected in place (see
CONVICTION_SIZING_CAP and THESIS_BREAK_DROP below for what changed and why).
Re-run that script whenever CONVICTION_FLOOR, CONVICTION_SIZING_CAP, or
THESIS_BREAK_DROP change.

Everything is advisory/educational output, never an execution instruction. It is
deterministic and dependency-light so it can be unit-tested and backtested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Conviction below this is "no signal" — never a buy candidate.
# Backtested: above-floor names beat below-floor by +3.75% excess (n=1215 vs
# 667). PASSED — this rule is trusted as a hard EXIT trigger.
CONVICTION_FLOOR = 45.0
# Sizing stops rewarding conviction increases past this level — it does NOT
# cap what counts as a valid buy (that's CONVICTION_FLOOR / regime.add_floor).
# Backtested: cv60-68 excess -3.45%, cv68+ excess -3.02% — the TOP band was the
# WORST band (same overconfidence that made the old "Accumulate" bucket, itself
# defined as cv>=60, the worst performer at -3.36%). The original formula sized
# UP quadratically all the way to conviction=100, i.e. biggest bets on the worst
# band. Capping effective conviction here for sizing purposes fixes that without
# touching CONVICTION_FLOOR (the buy/hold gate, which passed its own test).
CONVICTION_SIZING_CAP = 60.0
# A conviction drop of this many points since the prior thesis = a "thesis
# break". Backtested and FAILED as a hard EXIT trigger: n=14 broken theses
# actually averaged +3.57% excess vs +0.02% for stable ones — backwards, and
# far too small a sample (43 tickers had any repeat thesis at all) to trust
# either direction. Demoted to advisory-only: blocks new ADDs to the name (the
# knife-catch case) but does NOT force a sale of an existing position. Revisit
# once scripts/backtest_decision_layer.py has more repeat-thesis history.
THESIS_BREAK_DROP = 8.0
# Don't bother emitting sub-dollar noise / churn.
MIN_TRADE_DOLLARS = 150.0


@dataclass(frozen=True)
class Regime:
    """Market regime and the risk posture it implies."""

    label: str  # "risk_on" | "neutral" | "risk_off"
    cash_target: float  # fraction of equity to hold in cash
    add_floor: float  # min conviction required to ADD/BUY in this regime
    size_scale: float  # multiplier on new-buy sizing (defensive < 1.0)
    note: str


def detect_regime(
    bench_5d_return: float | None, breadth_pct_up: float | None
) -> Regime:
    """Classify the tape from benchmark trend + breadth (share of names green).

    Deliberately simple and robust: the point is a *posture*, not a forecast.
    Risk-off widens the cash buffer and raises the bar to add, which is precisely
    what protects capital when dip-buying stops working.
    """
    b = 0.0 if bench_5d_return is None else bench_5d_return
    breadth = 0.5 if breadth_pct_up is None else breadth_pct_up

    if b <= -2.0 or breadth < 0.35:
        return Regime(
            "risk_off",
            cash_target=0.20,
            add_floor=58.0,
            size_scale=0.6,
            note="defensive — deploy slowly, favor only top convictions",
        )
    if b >= 1.5 and breadth > 0.55:
        return Regime(
            "risk_on",
            cash_target=0.08,
            add_floor=50.0,
            size_scale=1.0,
            note="constructive — deploy into strength",
        )
    return Regime(
        "neutral",
        cash_target=0.12,
        add_floor=53.0,
        size_scale=0.85,
        note="balanced — selective adds",
    )


@dataclass
class Position:
    ticker: str
    market_value: float
    conviction: float
    prior_conviction: float | None = None  # for thesis-break detection
    volatility: float | None = None  # recent daily-return stdev (risk tilt)
    cluster: str | None = None  # correlation/theme group for caps


@dataclass
class Decision:
    ticker: str
    action: str  # BUY | ADD | TRIM | EXIT | HOLD | AVOID
    dollars: float  # signed suggested $ (+add / −trim); 0 for HOLD
    conviction: float
    reason: str
    priority: int = 0  # higher = more urgent (sorted desc for the brief)
    tags: list[str] = field(default_factory=list)


def _target_weights(
    positions: list[Position], regime: Regime, cluster_cap: float = 0.30
) -> dict[str, float]:
    """Conviction-weighted, inverse-vol-tilted, cluster-capped target weights.

    Weights sum to the *invested* fraction (1 − cash_target). Only names above
    the regime's add_floor OR already held with conviction ≥ FLOOR get a target;
    everything else targets 0 (i.e. a candidate to trim/exit).
    """
    raw: dict[str, float] = {}
    for p in positions:
        if p.conviction < CONVICTION_FLOOR:
            continue
        # Conviction above the floor, squared to reward the high end, but
        # capped at CONVICTION_SIZING_CAP — see the constant's docstring for
        # the backtest showing cv60+ is currently the WORST band, not the
        # best. Sizing must not keep growing into that overconfident tier.
        effective_cv = min(p.conviction, CONVICTION_SIZING_CAP)
        span = CONVICTION_SIZING_CAP - CONVICTION_FLOOR
        edge = ((effective_cv - CONVICTION_FLOOR) / span) ** 2 if span > 0 else 1.0
        vol = p.volatility if (p.volatility and p.volatility > 0) else 0.04
        raw[p.ticker] = edge / vol

    if not raw:
        return {}

    # Cluster caps: no correlated basket may exceed cluster_cap of the book.
    by_cluster: dict[str, list[str]] = {}
    for p in positions:
        if p.ticker in raw:
            by_cluster.setdefault(p.cluster or p.ticker, []).append(p.ticker)

    total = sum(raw.values())
    weights = {t: v / total for t, v in raw.items()}
    for _cluster, members in by_cluster.items():
        csum = sum(weights[t] for t in members)
        if csum > cluster_cap:
            scale = cluster_cap / csum
            for t in members:
                weights[t] *= scale

    # Renormalize to the invested fraction after any capping.
    invested = 1.0 - regime.cash_target
    s = sum(weights.values())
    if s > 0:
        weights = {t: (w / s) * invested for t, w in weights.items()}
    return weights


def decide(
    positions: list[Position],
    *,
    cash: float,
    regime: Regime,
    cluster_cap: float = 0.30,
) -> list[Decision]:
    """Produce sized, reasoned decisions for the whole book.

    Args:
        positions: current holdings (market_value>0) AND any zero-value
            candidates you want considered for a new BUY.
        cash: idle cash available to deploy.
        regime: output of :func:`detect_regime`.
    """
    equity = sum(p.market_value for p in positions) + cash
    if equity <= 0:
        return []
    targets = _target_weights(positions, regime, cluster_cap)
    deployable = max(0.0, cash - regime.cash_target * equity)

    decisions: list[Decision] = []
    for p in positions:
        cur_w = p.market_value / equity
        tgt_w = targets.get(p.ticker, 0.0)
        tgt_val = tgt_w * equity
        gap = tgt_val - p.market_value  # + underweight / − overweight
        broke = (
            p.prior_conviction is not None
            and (p.prior_conviction - p.conviction) >= THESIS_BREAK_DROP
        )

        # ── Exit a dead thesis (proven: +3.75% excess separating this gate) ──
        if p.market_value > 0 and p.conviction < CONVICTION_FLOOR:
            decisions.append(
                Decision(
                    p.ticker,
                    "EXIT",
                    dollars=-p.market_value,
                    conviction=p.conviction,
                    reason=f"conviction {p.conviction:.0f} < floor {CONVICTION_FLOOR:.0f} "
                    "— free capital, don't average down",
                    priority=90,
                    tags=["discipline"],
                )
            )
            continue

        # ── Recent break, still above floor: block new ADDs only ────────────
        # NOT a forced sale — backtested n=14 and the direction was backwards
        # (broken theses averaged +3.57% vs +0.02% stable), too small and too
        # contrary to trust as a sell trigger. See THESIS_BREAK_DROP docstring.
        if p.market_value > 0 and broke:
            decisions.append(
                Decision(
                    p.ticker,
                    "HOLD",
                    dollars=0.0,
                    conviction=p.conviction,
                    reason=f"cv dropped {p.prior_conviction:.0f}→{p.conviction:.0f} "
                    "recently — holding, not adding until it stabilizes "
                    "(unproven as a sell signal on current sample size)",
                    priority=40,
                    tags=["watch", "no-add"],
                )
            )
            continue

        # ── Overweight a still-good name → trim to target ───────────────────
        if p.market_value > 0 and gap <= -MIN_TRADE_DOLLARS:
            decisions.append(
                Decision(
                    p.ticker,
                    "TRIM",
                    dollars=gap,
                    conviction=p.conviction,
                    reason=f"overweight {cur_w * 100:.0f}% vs {tgt_w * 100:.0f}% target "
                    f"(cv {p.conviction:.0f}) — book risk, not thesis",
                    priority=55,
                    tags=["rebalance"],
                )
            )
            continue

        # ── Underweight, high conviction, regime-permitting → add ───────────
        if gap >= MIN_TRADE_DOLLARS and p.conviction >= regime.add_floor and not broke:
            add = min(gap * regime.size_scale, deployable)
            if add >= MIN_TRADE_DOLLARS:
                deployable -= add
                new_pos = p.market_value > 0
                decisions.append(
                    Decision(
                        p.ticker,
                        "ADD" if new_pos else "BUY",
                        dollars=add,
                        conviction=p.conviction,
                        reason=f"cv {p.conviction:.0f} ≥ {regime.add_floor:.0f} floor, "
                        f"underweight {cur_w * 100:.0f}%→{tgt_w * 100:.0f}% "
                        f"({regime.label}, {regime.size_scale:.0%} size)",
                        priority=70 + int(p.conviction - regime.add_floor),
                        tags=["deploy"],
                    )
                )
                continue

        # ── Otherwise hold (incl. winners we deliberately let run) ──────────
        if p.market_value > 0:
            note = "let it run" if gap < 0 else "on target"
            decisions.append(
                Decision(
                    p.ticker,
                    "HOLD",
                    dollars=0.0,
                    conviction=p.conviction,
                    reason=f"cv {p.conviction:.0f}, {note}",
                    priority=10,
                    tags=["hold"],
                )
            )

    decisions.sort(key=lambda d: d.priority, reverse=True)
    return decisions
