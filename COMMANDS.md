# Maverick Commands

Your AI-native VC firm, accessible from Claude Code.

## Daily Workflow

| Command | What it does | Example |
|---------|-------------|---------|
| `/brief` | Full CIO morning read — positions, screen, risk, top actions | `/brief` |
| `/positions` | Live Schwab holdings with P&L | `/positions` |
| `/screen` | Maverick screener results — Accumulate and Bear lists | `/screen` |
| `/rebalance` | Exact $ deltas per position based on conviction tiers | `/rebalance` |
| `/diff` | What changed in conviction scores since last run | `/diff` or `/diff 14` |

## Deep Work

| Command | What it does | Example |
|---------|-------------|---------|
| `/vc-stack` | Full VC diligence — thesis brief, fast reject, specialist sweep, IC memo | `/vc-stack RXRX` |
| `/investment-brain` | Activate the full investment team for any decision | `/investment-brain` |

## How Rebalance Tiers Work

| Tier | Target Weight | Trigger |
|------|--------------|---------|
| Accumulate T1 | 4–6% | On screen + conviction score ≥ 70 |
| Accumulate | 2–4% | On screen |
| Watch | 1–3% | Hold, don't add |
| Off-screen | 0–0.5% | Exit candidate |

## Tools Available (call directly or through commands)

**Portfolio**
- `portfolio_rebalance_suggestions` — conviction + correlation-aware sizing deltas (cluster caps, risk parity)
- `portfolio_diversification` — Dalio layer: effective bets, correlated clusters, risk contribution, A–F grade
- `portfolio_get_my_portfolio` — local portfolio state
- `portfolio_risk_adjusted_analysis` — risk-weighted view
- `portfolio_portfolio_correlation_analysis` — raw correlation matrix

**Broker**
- `broker_positions` — live Schwab holdings
- `broker_account_summary` — account totals and cash
- `maverick_sync_portfolio` — sync Schwab → local DB

**Screening**
- `screening_get_maverick_stocks` — current Accumulate list
- `screening_get_maverick_bear_stocks` — Bear/Avoid list
- `screening_get_all_screening_recommendations` — full results

**VC Loop**
- `vc_loop_run` — run conviction scoring, write to ledger + Obsidian
- `vc_loop_pipeline` — full pipeline with tiers
- `vc_loop_review` — outcome review and calibration

**Research & Agents**
- `research_company_comprehensive` — deep dive on a ticker
- `agents_deep_research_financial` — autonomous research agent
- `agents_compare_personas_analysis` — bull vs bear debate
- `agents_orchestrated_analysis` — multi-agent coordination

**Technical**
- `technical_get_full_technical_analysis` — full TA for a ticker
- `technical_get_stock_chart_analysis` — chart with signals
- `technical_get_rsi_analysis` / `technical_get_macd_analysis`

**Risk**
- `get_portfolio_risk_dashboard` — full risk report
- `get_position_risk_check` — single position risk
- `get_regime_adjusted_sizing` — size by market regime
- `get_risk_alerts` — active alerts

**Intelligence**
- `investment_brief` — daily briefing (used by `/brief`)
- `conviction_diff` — ledger delta (used by `/diff`)
- `brief_history` — portfolio drift over time: conviction, deployment, effective bets
- `data_get_news_sentiment` — news sentiment for a ticker
- `get_upcoming_catalysts` — upcoming events for holdings
- `watchlist_brief` — opportunity pipeline summary

## The Closed Loop

The system runs itself even when you're away (launchd, weekdays 8am + 4:30pm):

1. **MEASURE** — `daily_intelligence_run.py` scores the universe (vc_loop) and snapshots the book
2. **ADJUST** — rebalance applies conviction curve → cluster caps → risk-parity tilt
3. **SURFACE** — `/brief` renders one cohesive CIO picture with the Dalio diversification lens
4. **RECORD** — every run writes to `brief_snapshots` (conviction, deployment, effective bets)
5. **LEARN** — `/diff` + `brief_history` show drift over time so you see the book improving

Dalio principle baked in: conviction sizes a bet, but **correlation caps it**. Five
space stocks are one bet, not five — the engine enforces a ~20% per-cluster budget
and tracks effective bets against a 15-bet diversification target.

## Principle

You are the CIO. Commands propose, never execute. Every real trade, transfer, or account change requires your explicit approval.
