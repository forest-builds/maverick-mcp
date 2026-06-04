# Portfolio Rebalance

Call `broker_account_summary` and `portfolio_rebalance_suggestions` (pass available_cash from account summary).

Quick sizing table — no AI synthesis, just the numbers.

```
REBALANCE · {DATE}  (conviction-curve targets, cash-first)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cash available: ${CASH}  — funding adds before any trims

EXITS  (off-screen → 0%)
  EXIT  {TICKER}  {CUR}%→0%  -${AMT}

DEPLOY  (cash-funded)
  ADD  {TICKER}  {CUR}%→{TGT}%  +${AMT}  [conv {SCORE}]  [{funded_by}]

CONCENTRATION  (optional — trims not needed for funding)
  TRIM  {TICKER}  {CUR}%→{TGT}%  -${AMT}  [conv {SCORE}]

Net cash after adds: ${REMAINING}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Rules:
- If $argument is a number, pass as total_portfolio_value override
- Show funded_by tag per add: [cash], [cash+trim], [needs trim]
- Omit CONCENTRATION section if trims_are_for_concentration_only=true and no major overweights
- Sort DEPLOY by conviction score desc, CONCENTRATION by abs(delta) desc
- No disclaimers
