#!/usr/bin/env python
"""Backtest the decision-layer rules (maverick_mcp/vc_loop/decision.py) against
1,882 real 7-day thesis outcomes, BEFORE the layer is wired into any live flow.

decision.py encodes three claims. This script tests each one independently on
history and reports pass/fail with numbers, not vibes:

  1. THESIS-BREAK RULE: when a ticker's conviction drops materially since its
     prior thesis, decision.py exits/avoids rather than holding or adding.
     Test: do "broken" theses go on to underperform "stable/improving" ones?

  2. CONVICTION-FLOOR RULE: decision.py never buys/holds below CONVICTION_FLOOR
     (45) — it exits. Test: does the sub-floor bucket actually underperform?

  3. SIZING-BY-CONVICTION RULE: decision.py sizes bigger for higher conviction
     within the buy zone (conviction, squared). Test: is forward excess return
     monotonically increasing across conviction bands >= the floor?

A fourth claim (regime/momentum-gated sizing) is NOT re-tested here — it was
already disproven as an alpha source in this specific mean-reverting window
(see prior session finding: uptrend tercile underperformed downtrend tercile).
It remains in decision.py as a risk-control (shrinks new-buy size when the
tape is broad-red), not a return driver, and that distinction is reported
below rather than re-litigated.

Run: uv run python scripts/backtest_decision_layer.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

DB = Path("maverick_mcp.db")
CONVICTION_FLOOR = 45.0
THESIS_BREAK_DROP = 8.0


def _load_clean_theses() -> list[dict]:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        "SELECT ticker, thesis_date, conviction, outcome "
        "FROM vc_thesis_ledger WHERE outcome IS NOT NULL ORDER BY ticker, thesis_date"
    )
    rows = []
    for tk, td, cv, out in cur.fetchall():
        try:
            o = json.loads(out)
        except (TypeError, json.JSONDecodeError):
            continue
        rp = o.get("return_pct")
        if rp is None or o.get("entry_date") == o.get("exit_date") or cv is None:
            continue
        rows.append(
            {"ticker": tk, "date": (td or "")[:10], "cv": float(cv), "ret": float(rp)}
        )
    con.close()
    return rows


def _excess_fn(rows: list[dict]):
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r["ret"])
    dmean = {d: st.mean(v) for d, v in by_date.items()}
    return lambda r: r["ret"] - dmean[r["date"]]


def rule1_thesis_break(rows: list[dict], excess) -> None:
    print("── RULE 1: thesis-break avoidance ──")
    print(
        "Claim: conviction dropped ≥8pts since the ticker's prior thesis ⇒ avoid/exit,"
    )
    print(
        "       don't hold through it or add. Test: does 'broken' underperform 'stable'?\n"
    )

    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    for v in by_ticker.values():
        v.sort(key=lambda r: r["date"])

    broke_excess, stable_excess, improve_excess = [], [], []
    for seq in by_ticker.values():
        for i in range(1, len(seq)):
            prev_cv, cur = seq[i - 1]["cv"], seq[i]
            delta = cur["cv"] - prev_cv
            e = excess(cur)
            if delta <= -THESIS_BREAK_DROP:
                broke_excess.append(e)
            elif delta >= THESIS_BREAK_DROP:
                improve_excess.append(e)
            else:
                stable_excess.append(e)

    def report(name, v):
        if not v:
            print(f"  {name:34} n=   0  (no observations)")
            return None
        m = st.mean(v)
        print(f"  {name:34} n={len(v):4}  excess={m:+5.2f}%")
        return m

    m_broke = report("BROKEN (cv dropped ≥8pts)", broke_excess)
    m_stable = report("stable (cv within ±8pts)", stable_excess)
    report("improving (cv rose ≥8pts)", improve_excess)

    if m_broke is not None and m_stable is not None:
        gap = m_stable - m_broke
        print(
            f"\n  Stable − Broken = {gap:+.2f}%  "
            f"({'✅ PASS — broken theses really do underperform, avoiding them is correct' if gap > 0 else '❌ FAIL — no separation, rule adds nothing measurable'})"
        )
    print()


def rule2_conviction_floor(rows: list[dict], excess) -> None:
    print("── RULE 2: conviction-floor exit (never hold/buy below cv 45) ──\n")
    below = [excess(r) for r in rows if r["cv"] < CONVICTION_FLOOR]
    above = [excess(r) for r in rows if r["cv"] >= CONVICTION_FLOOR]
    m_below = st.mean(below) if below else 0.0
    m_above = st.mean(above) if above else 0.0
    print(
        f"  below floor (cv<{CONVICTION_FLOOR:.0f})   n={len(below):4}  excess={m_below:+5.2f}%"
    )
    print(f"  at/above floor         n={len(above):4}  excess={m_above:+5.2f}%")
    gap = m_above - m_below
    print(
        f"\n  Above − Below = {gap:+.2f}%  "
        f"({'✅ PASS — floor correctly separates good from bad names' if gap > 0 else '❌ FAIL'})\n"
    )


def rule3_sizing_monotonic(rows: list[dict], excess) -> None:
    print("── RULE 3: sizing scales with conviction within the buy zone (cv≥45) ──\n")
    buy_zone = [r for r in rows if r["cv"] >= CONVICTION_FLOOR]
    bands = [(45, 53), (53, 60), (60, 68), (68, 200)]
    means = []
    for lo, hi in bands:
        b = [excess(r) for r in buy_zone if lo <= r["cv"] < hi]
        m = st.mean(b) if b else None
        means.append(m)
        label = f"{lo}-{hi if hi < 200 else '+'}"
        print(
            f"  cv {label:8} n={len(b):4}  excess={'n/a' if m is None else f'{m:+5.2f}%'}"
        )

    valid = [m for m in means if m is not None]
    monotonic = (
        all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1))
        if len(valid) > 1
        else False
    )
    print(
        f"\n  Strictly increasing across bands: {'✅ yes' if monotonic else '⚠️ no — noisy/non-monotonic, see note below'}"
    )
    if not monotonic and len(valid) > 1:
        print(
            f"  Top band vs bottom band: {valid[-1] - valid[0]:+.2f}%  "
            f"({'✅ directionally positive even if not clean monotonic steps' if valid[-1] > valid[0] else '❌ top band does not even beat bottom band'})"
        )
    print()


def main() -> None:
    rows = _load_clean_theses()
    excess = _excess_fn(rows)
    print(
        f"n={len(rows)} clean thesis outcomes, {len({r['ticker'] for r in rows})} tickers\n"
    )

    rule1_thesis_break(rows, excess)
    rule2_conviction_floor(rows, excess)
    rule3_sizing_monotonic(rows, excess)

    print("── NOT RE-TESTED: regime/momentum sizing ──")
    print("Already measured in this session: real 10d-momentum vs forward return was")
    print("only +0.066 Spearman, and the uptrend tercile UNDERPERFORMED the downtrend")
    print("tercile in this window (mean-reverting tape, not trending). decision.py's")
    print("regime gate is kept as a RISK CONTROL (shrinks new-buy size when breadth is")
    print("weak) — it is not claimed as an alpha source, and this backtest doesn't")
    print("pretend otherwise.")


if __name__ == "__main__":
    main()
