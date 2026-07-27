#!/usr/bin/env python
"""Verification harness for the VC-loop scoring engine.

"Is any scoring feature dead, and does the engine actually pick winners?"
This is the repeatable scorecard behind the core-system fixes: it audits every
feature for *liveness* (does it vary?) and *skill* (does it predict forward
return?), then grades the conviction ranking and the action bands on realized
outcomes.

Run:   uv run python scripts/verify_features.py
Exit:  0 if every feature is alive AND the ranking has positive skill; else 1.
       (CI-able gate — wire into `make verify` once green.)
"""

from __future__ import annotations

import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

DB = Path("maverick_mcp.db")
FEATURES = [
    "screening_score",
    "technical_strength",
    "sentiment_confidence",
    "catalyst_proximity",
    "momentum",
]
DEAD_STD = 0.05          # std below this ⇒ feature is effectively constant
WEAK_CORR = 0.05         # |Spearman| below this ⇒ no predictive signal


def _spearman(xs: list[float], ys: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    n = len(xs)
    if n < 3:
        return 0.0
    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (
        sum((x - mx) ** 2 for x in rx) * sum((y - my) ** 2 for y in ry)
    ) ** 0.5
    return num / den if den else 0.0


def _load() -> list[dict]:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute(
        "SELECT conviction, proposed_action, thesis_date, features, outcome "
        "FROM vc_thesis_ledger WHERE outcome IS NOT NULL"
    )
    rows = []
    for cv, act, td, feat, out in cur.fetchall():
        try:
            o = json.loads(out)
            f = json.loads(feat) if feat else {}
        except (TypeError, json.JSONDecodeError):
            continue
        rp = o.get("return_pct")
        # Drop the broken same-day outcomes (entry==exit, 0% return).
        if rp is None or o.get("entry_date") == o.get("exit_date"):
            continue
        rows.append(
            {
                "conviction": cv,
                "action": act or "",
                "date": (td or "")[:10],
                "features": f,
                "return": float(rp),
            }
        )
    con.close()
    return rows


def main() -> int:
    if not DB.exists():
        print(f"❌ {DB} not found — run from repo root.")
        return 1
    rows = _load()
    if len(rows) < 30:
        print(f"⚠️  Only {len(rows)} clean outcomes — verdict low-confidence.")
    rets = [r["return"] for r in rows]

    print(f"── FEATURE LIVENESS & SKILL  (n={len(rows)}) ──")
    print(f"{'feature':22}{'std':>7}{'corr→ret':>10}   verdict")
    all_alive = True
    for key in FEATURES:
        vals = [float(r["features"].get(key, 0.0)) for r in rows]
        std = st.pstdev(vals) if len(vals) > 1 else 0.0
        corr = _spearman(vals, rets)
        if std < DEAD_STD:
            verdict, ok = "💀 DEAD (constant)", False
        elif abs(corr) < WEAK_CORR:
            verdict, ok = "⚪ alive, no signal", True
        elif corr < 0:
            verdict, ok = "🔻 alive but ANTI-predictive", True
        else:
            verdict, ok = "✅ alive + predictive", True
        all_alive = all_alive and ok
        print(f"{key:22}{std:7.3f}{corr:+10.3f}   {verdict}")

    # Skill of the ranking: top vs bottom conviction quartile.
    by_cv = sorted(rows, key=lambda r: r["conviction"] or 0)
    q = max(1, len(by_cv) // 4)
    spread = st.mean([r["return"] for r in by_cv[-q:]]) - st.mean(
        [r["return"] for r in by_cv[:q]]
    )

    # Action bands: date-controlled excess return (skill vs the average pick).
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r["return"])
    dmean = {d: st.mean(v) for d, v in by_date.items()}
    act_excess: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        act_excess[r["action"]].append(r["return"] - dmean[r["date"]])

    print("\n── RANKING SKILL ──")
    print(f"top-vs-bottom conviction quartile spread: {spread:+.2f}%  "
          f"({'✅ positive' if spread > 0 else '❌ no skill'})")
    print("\n── ACTION BANDS (excess vs avg pick; buy signals should be >0) ──")
    for a, v in sorted(act_excess.items(), key=lambda x: -st.mean(x[1])):
        flag = "🚨" if a in {"Accumulate", "BUY (high conviction)"} and st.mean(v) < 0 else ""
        print(f"  {a or '(none)':26} n={len(v):4}  excess={st.mean(v):+5.2f}% {flag}")

    ok = all_alive and spread > 0
    print(f"\n{'✅ PASS — all features alive, ranking has skill' if ok else '❌ FAIL — dead features or no skill (see above)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
