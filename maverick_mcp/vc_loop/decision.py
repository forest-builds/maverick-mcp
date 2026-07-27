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
    equal dollars.
  * Respect the regime — in risk-off, raise the cash target and the bar to add.
  * Never average down into a thesis *break* (conviction fell materially) — that
    is the knife-catch; hold or exit instead of adding.
  * Let winners run — do not trim a name that is merely up if its thesis holds.
  * Cash is a position — only deploy when a name is both high-conviction AND
    underweight versus its target.

Everything is advisory/educational output, never an execution instruction. It is
deterministic and dependency-light so it can be unit-tested and backtested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Conviction below this is "no signal" — never a buy candidate.
CONVICTION_FLOOR = 45.0
# A conviction drop of this many points since the prior thesis = a "thesis break".
THESIS_BREAK_DROP = 8.0
# Don't bother emitting sub-dollar noise / churn.
MIN_TRADE_DOLLARS = 150.0


@dataclass(frozen=True)
class Regime:
    """Market regime and the risk posture it implies."""

    label: str            # "risk_on" | "neutral" | "risk_off"
    cash_target: float    # fraction of equity to hold in cash
    add_floor: float      # min conviction required to ADD/BUY in this regime
    size_scale: float     # multiplier on new-buy sizing (defensive < 1.0)
    note: str


def detect_regime(bench_5d_return: float | None, breadth_pct_up: float | None) -> Regime:
    """Classify the tape from benchmark trend + breadth (share of names green).

    Deliberately simple and robust: the point is a *posture*, not a forecast.
    Risk-off widens the cash buffer and raises the bar to add, which is precisely
    what protects capital when dip-buying stops working.
    """
    b = 0.0 if bench_5d_return is None else bench_5d_return
    breadth = 0.5 if breadth_pct_up is None else breadth_pct_up

    if b <= -2.0 or breadth < 0.35:
        return Regime("risk_off", cash_target=0.20, add_floor=58.0, size_scale=0.6,
                      note="defensive — deploy slowly, favor only top convictions")
    if b >= 1.5 and breadth > 0.55:
        return Regime("risk_on", cash_target=0.08, add_floor=50.0, size_scale=1.0,
                      note="constructive — deploy into strength")
    return Regime("neutral", cash_target=0.12, add_floor=53.0, size_scale=0.85,
                  note="balanced — selective adds")


@dataclass
class Position:
    ticker: str
    market_value: float
    conviction: float
    prior_conviction: float | None = None   # for thesis-break detection
    volatility: float | None = None          # recent daily-return stdev (risk tilt)
    cluster: str | None = None               # correlation/theme group for caps


@dataclass
class Decision:
    ticker: str
    action: str            # BUY | ADD | TRIM | EXIT | HOLD | AVOID
    dollars: float         # signed suggested $ (+add / −trim); 0 for HOLD
    conviction: float
    reason: str
    priority: int = 0      # higher = more urgent (sorted desc for the brief)
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
        # Conviction above the floor, squared to reward the high end (the ranking
        # is where the skill lives), then divided by risk (inverse-vol tilt).
        edge = ((p.conviction - CONVICTION_FLOOR) / 55.0) ** 2
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

        # ── Exit / trim on a broken or dead thesis ──────────────────────────
        if p.market_value > 0 and (p.conviction < CONVICTION_FLOOR or broke):
            why = (
                f"thesis break (cv {p.prior_conviction:.0f}→{p.conviction:.0f})"
                if broke else f"conviction {p.conviction:.0f} < floor {CONVICTION_FLOOR:.0f}"
            )
            decisions.append(Decision(
                p.ticker, "EXIT" if p.conviction < CONVICTION_FLOOR else "TRIM",
                dollars=-p.market_value if p.conviction < CONVICTION_FLOOR else gap,
                conviction=p.conviction,
                reason=f"{why} — free capital, don't average down",
                priority=90, tags=["discipline"],
            ))
            continue

        # ── Overweight a still-good name → trim to target ───────────────────
        if p.market_value > 0 and gap <= -MIN_TRADE_DOLLARS:
            decisions.append(Decision(
                p.ticker, "TRIM", dollars=gap, conviction=p.conviction,
                reason=f"overweight {cur_w*100:.0f}% vs {tgt_w*100:.0f}% target "
                       f"(cv {p.conviction:.0f}) — book risk, not thesis",
                priority=55, tags=["rebalance"],
            ))
            continue

        # ── Underweight, high conviction, regime-permitting → add ───────────
        if gap >= MIN_TRADE_DOLLARS and p.conviction >= regime.add_floor and not broke:
            add = min(gap * regime.size_scale, deployable)
            if add >= MIN_TRADE_DOLLARS:
                deployable -= add
                new_pos = p.market_value > 0
                decisions.append(Decision(
                    p.ticker, "ADD" if new_pos else "BUY", dollars=add,
                    conviction=p.conviction,
                    reason=f"cv {p.conviction:.0f} ≥ {regime.add_floor:.0f} floor, "
                           f"underweight {cur_w*100:.0f}%→{tgt_w*100:.0f}% "
                           f"({regime.label}, {regime.size_scale:.0%} size)",
                    priority=70 + int(p.conviction - regime.add_floor),
                    tags=["deploy"],
                ))
                continue

        # ── Otherwise hold (incl. winners we deliberately let run) ──────────
        if p.market_value > 0:
            note = "let it run" if gap < 0 else "on target"
            decisions.append(Decision(
                p.ticker, "HOLD", dollars=0.0, conviction=p.conviction,
                reason=f"cv {p.conviction:.0f}, {note}", priority=10, tags=["hold"],
            ))

    decisions.sort(key=lambda d: d.priority, reverse=True)
    return decisions
