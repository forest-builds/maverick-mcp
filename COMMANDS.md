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
- `portfolio_rebalance_suggestions` — sizing deltas
- `portfolio_get_my_portfolio` — local portfolio state
- `portfolio_risk_adjusted_analysis` — risk-weighted view
- `portfolio_portfolio_correlation_analysis` — concentration/correlation

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
- `data_get_news_sentiment` — news sentiment for a ticker
- `get_upcoming_catalysts` — upcoming events for holdings
- `watchlist_brief` — opportunity pipeline summary

## Principle

You are the CIO. Commands propose, never execute. Every real trade, transfer, or account change requires your explicit approval.
