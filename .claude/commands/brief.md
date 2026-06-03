# Daily Investment Brief

Call `investment_brief` from maverick-mcp. Then render the output using EXACTLY this template — no additions, no reordering, no extra prose.

```
━━━ MAVERICK BRIEF · {DATE} ━━━━━━━━━━━━━━━━━━━━

PORTFOLIO   ${EQUITY}  +  ${CASH} cash  =  ${TOTAL}  ({CASH_PCT}% deployed)
            {N} positions  ·  largest: {TICKER} {PCT}%  ·  top-5: {TOP5_PCT}%

SCREEN
  ✓ ACC   {HELD_ACCUMULATE}
  — NONE  {HELD_NO_SIGNAL}
  ✗ EXIT  {HELD_OFF_SCREEN}

CAPITAL FLOW
  FREE     ${FREED}    {EXIT_TICKERS}  {TRIM_TICKERS}
  DEPLOY   ${DEPLOY}   {ADD_TICKERS}
  NEW OPP  {NEW_OPPS}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Rules:
- DATE: today's date, e.g. "2026-06-03"
- CASH: show as "—" if unavailable
- DEPLOYED = 100 - cash_pct; if cash unknown say "deployment unknown"
- FREE: exits first (label EXIT), then top trims (label TRIM). Format: `TICKER -$AMOUNT`
- DEPLOY: underweight accumulate names. Format: `TICKER +$AMOUNT`
- NEW OPP: screener hits not currently held, max 6 tickers, no dollar amounts
- If a section has no data: show "—" not an explanation
- No disclaimers. No extra text outside the template.
