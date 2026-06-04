# Conviction Diff

Call `conviction_diff` and `brief_history` in parallel.

If $argument is a number, use it as days_back for conviction_diff. Default 7.

Show two sections:

**CONVICTION DELTA**  (from conviction_diff)
- New entrants — tickers that appeared for the first time
- Exits — dropped off the ledger
- Movers — biggest score changes (↑↓ with delta)
- Tier changes — high-conviction names now off-screen

★ = currently held position. Flag movers that affect held positions.

**PORTFOLIO DRIFT**  (from brief_history)
- Conviction score trend: {N} runs ago → today (arrow + delta)
- Cash trend: deployed % over time (idle = warning)
- Any alerts from brief_history (declining conviction, stalled deployment, growing off-screen %)
- Total value trend

If brief_history has no data yet: say "No history yet — run /brief daily to build the drift picture."

Keep it tight. One line per entry.
