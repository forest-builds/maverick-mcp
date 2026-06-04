# Brief

Call `investment_brief` from maverick-mcp. Render using EXACTLY this layout — no additions, no reordering, no disclaimers.

```
BRIEF · {DATE}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Equity    ${EQUITY}   {N} positions
  Cash      ${CASH}     {CASH_PCT}% of total
  Total     ${TOTAL}    {DEPLOYED_PCT}% deployed

SCREEN
  ✓ {TICKER}  {CUR_PCT}% → {TARGET_PCT}%  {DELTA_STR}  — {WHY}
  (one line per held accumulate position, sorted by delta_dollars desc)

  — {TICKER}  {CUR_PCT}%  hold, no signal
  (one line per held_no_signal)

  ✗ {TICKER}  {CUR_PCT}% → 0%  ${EXIT_AMT} — {WHY}
  (one line per held_off_screen — all exits are full unless delta > -$50)

CAPITAL FLOW
  FREE  ${FREED}
    {EXIT lines first, then TRIM lines}
    TRIM  {TICKER}  {CUR_PCT}% → {TARGET_PCT}%  -${AMT}  — {WHY}

  DEPLOY  ${DEPLOY}
    ADD  {TICKER}  {CUR_PCT}% → {TARGET_PCT}%  +${AMT}  — {WHY}

  NEW OPP  (niche / thematic — not widely followed)
    {TICKER}  — {WHY from screener: pattern · ADR · score}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Rules:
- DATE: today's date, e.g. "2026-06-03"
- DELTA_STR: "+$X" if add, "-$X" if trim, "hold" if within band
- Exit = full liquidation (off-screen means no signal, 0% target)
- Trim = partial reduction to band midpoint, position stays open
- WHY must always be present on every line — pull from the `why` field
- NEW OPP: max 6, niche names only (sector=None from screener), skip if none
- If cash unavailable: show "—" not an explanation
- No extra text outside the template
