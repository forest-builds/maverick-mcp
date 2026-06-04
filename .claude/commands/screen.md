# Screen

Run the full screen stack, cross-reference with your book, and give a conviction-overlaid view.

## Step 1 — Pull data (parallel)
- `screening_get_maverick_stocks` — current accumulate universe (ADR-filtered, growth only)
- `screening_get_maverick_bear_stocks` — off-screen / avoid list
- `broker_positions` — live holdings for cross-reference
- `get_market_regime` — current regime (determines urgency of signals)

## Step 2 — Render

```
SCREEN · {DATE}  [{REGIME} — {confidence}% confidence]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ACCUMULATE  ({N} names)

  ★ HELD  (in your book)
    {TICKER}  {CUR}%  {action: add/trim/hold}  [{pattern · ADR}]

  + NEW OPP  (not in book — thematic/niche first)
    {TICKER}  [{pattern · ADR · score}]  [regime fit: ✓/~]

AVOID  ({N} bear names)
  ★ HELD  {TICKER}  {CUR}% — EXIT recommended
  {TICKER}  (not held)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Rules:
- ★ = currently held position
- Regime fit: ✓ bull supports this name, ~ transitional/caution
- Sort HELD accumulate by position size desc
- Sort NEW OPP: sector=None names first (niche/thematic), then named-sector growth
- If $argument is a ticker: show only that ticker's screen status and why
- No disclaimers
