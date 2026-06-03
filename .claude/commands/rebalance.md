# Portfolio Rebalance

Call `portfolio_rebalance_suggestions` from maverick-mcp.

Present results as a table:

| Ticker | Tier | Current % | Target % | Action | Delta $ |
|--------|------|-----------|----------|--------|---------|

Sort: exits → trims → adds → holds. Omit holds unless there are fewer than 5 other rows.

After the table, one-line summary: "X exits, Y trims, Z adds — net $ to deploy / free up."

If $argument is provided (e.g. `/rebalance 85000`), pass it as total_portfolio_value.

No disclaimers. If data is unavailable, say what's missing and why.
