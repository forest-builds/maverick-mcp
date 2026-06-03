"""
Investment operations tools — the action layer.

Three tools that turn analysis into decisions:
  portfolio_rebalance_suggestions  — exact $ deltas per position
  investment_brief                 — daily CIO briefing in one call
  conviction_diff                  — what changed since last vc_loop run
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Target weight bands by screen tier (fraction of total portfolio)
_BANDS: dict[str, tuple[float, float]] = {
    "accumulate_t1": (0.04, 0.06),   # high-conviction Accumulate: 4-6%
    "accumulate":    (0.02, 0.04),   # standard Accumulate: 2-4%
    "watch":         (0.01, 0.03),   # Watch: hold, don't add
    "off_screen":    (0.00, 0.005),  # no momentum: trim to near-zero
    "unknown":       (0.00, 0.05),   # not in our universe: no opinion
}
_T1_CONVICTION_THRESHOLD = 70.0  # vc_loop conviction score for T1


def _band_midpoint(tier: str) -> float:
    lo, hi = _BANDS.get(tier, (0.0, 0.05))
    return (lo + hi) / 2


def _classify_tier(ticker: str, accumulate: set, watch: set, off_screen: set,
                   conviction_scores: dict[str, float]) -> str:
    if ticker in accumulate:
        score = conviction_scores.get(ticker, 0.0)
        return "accumulate_t1" if score >= _T1_CONVICTION_THRESHOLD else "accumulate"
    if ticker in watch:
        return "watch"
    if ticker in off_screen:
        return "off_screen"
    return "unknown"


def _get_screen_sets() -> tuple[set, set, set]:
    """Return (accumulate, watch, off_screen) ticker sets from latest screen."""
    try:
        from maverick_mcp.api.routers.screening import (
            get_all_screening_recommendations,
        )
        result = get_all_screening_recommendations()
        accumulate: set = set()
        off_screen: set = set()
        for s in result.get("maverick_stocks", []):
            tkr = (s.get("ticker") or "").upper()
            if tkr:
                accumulate.add(tkr)
        for s in result.get("maverick_bear_stocks", []):
            tkr = (s.get("ticker") or "").upper()
            if tkr:
                off_screen.add(tkr)
        # Watch = held names not on either list; computed at call site
        return accumulate, set(), off_screen
    except Exception as e:
        logger.warning("Could not load screen sets: %s", e)
        return set(), set(), set()


def _get_conviction_scores() -> dict[str, float]:
    """Return latest conviction score per ticker from the vc_thesis_ledger."""
    try:
        from maverick_mcp.data.models import SessionLocal
        from maverick_mcp.vc_loop.models import ThesisLedger

        with SessionLocal() as session:
            rows = (
                session.query(ThesisLedger)
                .order_by(ThesisLedger.thesis_date.desc())
                .limit(200)
                .all()
            )
            seen: dict[str, float] = {}
            for r in rows:
                if r.ticker not in seen:
                    seen[r.ticker] = float(r.conviction or 0)
            return seen
    except Exception as e:
        logger.warning("Could not load conviction scores: %s", e)
        return {}


def _get_broker_positions() -> list[dict]:
    """Return current positions from Schwab; fall back to local portfolio."""
    try:
        from maverick_mcp.providers.schwab import SchwabClient
        from maverick_mcp.providers.schwab.sync import fetch_schwab_positions
        from maverick_mcp.api.routers.schwab import _load_schwab

        config, store = _load_schwab()
        client = SchwabClient(config, store)
        positions = fetch_schwab_positions(client)
        return [
            {
                "ticker": p.ticker,
                "shares": float(p.shares),
                "market_value": float(p.market_value) if p.market_value else 0.0,
            }
            for p in positions
        ]
    except Exception:
        pass

    try:
        from maverick_mcp.api.routers.portfolio import get_my_portfolio
        result = get_my_portfolio()  # type: ignore[call-arg]
        return [
            {
                "ticker": p.get("ticker", ""),
                "shares": float(p.get("shares", 0)),
                "market_value": float(p.get("market_value") or p.get("current_value") or 0),
            }
            for p in result.get("positions", [])
        ]
    except Exception as e:
        logger.warning("Could not load positions: %s", e)
        return []


def register_investment_ops_tools(mcp: FastMCP) -> None:

    @mcp.tool(name="portfolio_rebalance_suggestions")
    def portfolio_rebalance_suggestions(
        total_portfolio_value: float | None = None,
    ) -> dict[str, Any]:
        """
        Generate specific rebalance actions for every position.

        Compares current position weights against Maverick target bands and
        outputs exact dollar deltas per ticker so you know exactly what to
        buy, trim, or exit.

        Tiers and target weight bands:
          accumulate_t1  (conviction >= 70): 4-6% of portfolio
          accumulate     (on screen):        2-4%
          watch          (no add signal):    1-3%, hold
          off_screen     (no momentum):      0-0.5%, exit candidate
          unknown        (not in universe):  no opinion

        Args:
            total_portfolio_value: Override total portfolio value in USD.
                Computed from broker positions if omitted.

        Returns:
            actions: list of per-ticker rebalance actions with delta_dollars
            summary: counts and total portfolio value
        """
        positions = _get_broker_positions()
        if not positions:
            return {"error": "No positions found. Check broker connection."}

        accumulate, watch, off_screen = _get_screen_sets()
        conviction_scores = _get_conviction_scores()

        total_value = total_portfolio_value or sum(p["market_value"] for p in positions)
        if total_value <= 0:
            return {"error": "Cannot compute weights: total portfolio value is zero."}

        actions = []
        for pos in positions:
            ticker = pos["ticker"].upper()
            mv = pos["market_value"]
            current_pct = mv / total_value

            tier = _classify_tier(ticker, accumulate, watch, off_screen, conviction_scores)
            target_pct = _band_midpoint(tier)
            delta_pct = target_pct - current_pct
            delta_dollars = delta_pct * total_value

            if abs(delta_dollars) < 50:
                action = "hold"
            elif delta_dollars > 0:
                action = "add"
            else:
                action = "trim" if target_pct > 0 else "exit"

            lo, hi = _BANDS.get(tier, (0.0, 0.05))
            rationale = (
                f"{tier.replace('_', ' ').title()} — "
                f"target band {lo*100:.0f}-{hi*100:.0f}%; "
                f"currently at {current_pct*100:.1f}%"
            )
            if tier == "off_screen":
                rationale += ". No momentum signal — exit candidate."

            actions.append({
                "ticker": ticker,
                "tier": tier,
                "conviction_score": conviction_scores.get(ticker),
                "current_value": round(mv, 2),
                "current_pct": round(current_pct * 100, 2),
                "target_pct": round(target_pct * 100, 2),
                "action": action,
                "delta_dollars": round(delta_dollars, 2),
                "rationale": rationale,
            })

        actions.sort(key=lambda x: abs(x["delta_dollars"]), reverse=True)

        exits = [a for a in actions if a["action"] == "exit"]
        trims = [a for a in actions if a["action"] == "trim"]
        adds = [a for a in actions if a["action"] == "add"]
        holds = [a for a in actions if a["action"] == "hold"]

        return {
            "as_of": datetime.now(UTC).isoformat(),
            "total_portfolio_value": round(total_value, 2),
            "actions": actions,
            "summary": {
                "exit_count": len(exits),
                "trim_count": len(trims),
                "add_count": len(adds),
                "hold_count": len(holds),
                "exits": [a["ticker"] for a in exits],
                "top_adds": [a["ticker"] for a in adds[:5]],
                "top_trims": [a["ticker"] for a in trims[:5]],
            },
        }

    @mcp.tool(name="investment_brief")
    def investment_brief() -> dict[str, Any]:
        """
        Daily CIO briefing — runs the full stack in one call.

        Pulls live positions, screen results, rebalance actions (trims AND
        redeployment targets), and new opportunities from the screener not
        currently in the book.

        Returns:
            portfolio: position count and total value
            screen: held names by tier
            rebalance: full capital flow — what to trim/exit and where it goes
            new_opportunities: screener hits not currently held (forward-looking)
            risk: basic concentration metrics from positions
            generated_at: ISO timestamp
        """
        brief: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat()}

        # --- Positions + cash ---
        positions = _get_broker_positions()
        total_value = sum(p["market_value"] for p in positions)
        held_tickers = {p["ticker"].upper() for p in positions}

        cash_balance: float | None = None
        try:
            from maverick_mcp.providers.schwab import SchwabClient
            from maverick_mcp.providers.schwab.sync import summarize_accounts
            from maverick_mcp.api.routers.schwab import _load_schwab
            config, store = _load_schwab()
            client = SchwabClient(config, store)
            summaries = summarize_accounts(client.accounts(fields="positions") or [])
            cash_balance = sum(
                float(s.cash_balance) for s in summaries if s.cash_balance
            )
        except Exception:
            pass

        brief["portfolio"] = {
            "position_count": len(positions),
            "equity_value": round(total_value, 2),
            "cash_balance": round(cash_balance, 2) if cash_balance is not None else None,
            "total_value": round(total_value + (cash_balance or 0), 2),
            "cash_pct": round(cash_balance / (total_value + cash_balance) * 100, 1)
                        if cash_balance else None,
        }

        # --- Screen results ---
        accumulate, _, off_screen = _get_screen_sets()
        held_accumulate = accumulate & held_tickers
        held_off_screen = off_screen & held_tickers
        held_no_signal = held_tickers - accumulate - off_screen
        brief["screen"] = {
            "held_accumulate": sorted(held_accumulate),
            "held_no_signal": sorted(held_no_signal),
            "held_off_screen": sorted(held_off_screen),
        }

        # --- New opportunities (not in book, on accumulate screen) ---
        new_opps = sorted(accumulate - held_tickers)
        brief["new_opportunities"] = new_opps[:10]

        # --- Rebalance: full capital flow ---
        try:
            rebalance = portfolio_rebalance_suggestions(total_portfolio_value=total_value)
            all_actions = rebalance.get("actions", [])
            exits = [a for a in all_actions if a["action"] == "exit"]
            trims = [a for a in all_actions if a["action"] == "trim"]
            adds  = [a for a in all_actions if a["action"] == "add"]

            capital_freed = round(sum(abs(a["delta_dollars"]) for a in exits + trims), 2)
            capital_needed = round(sum(a["delta_dollars"] for a in adds), 2)

            brief["rebalance"] = {
                "capital_freed": capital_freed,
                "capital_to_deploy": capital_needed,
                "net": round(capital_needed - capital_freed, 2),
                "exits": [{"ticker": a["ticker"], "delta": a["delta_dollars"]} for a in exits],
                "top_trims": [{"ticker": a["ticker"], "delta": a["delta_dollars"], "rationale": a["rationale"]} for a in trims[:5]],
                "top_adds": [{"ticker": a["ticker"], "delta": a["delta_dollars"], "rationale": a["rationale"]} for a in adds[:5]],
            }
        except Exception as e:
            brief["rebalance"] = {"error": str(e)}

        # --- Risk: basic concentration from positions (no nested import needed) ---
        try:
            pos_by_value = sorted(positions, key=lambda p: p["market_value"], reverse=True)
            top5_value = sum(p["market_value"] for p in pos_by_value[:5])
            top1 = pos_by_value[0] if pos_by_value else None
            brief["risk"] = {
                "largest_position": {"ticker": top1["ticker"], "pct": round(top1["market_value"] / total_value * 100, 1)} if top1 else None,
                "top5_concentration_pct": round(top5_value / total_value * 100, 1) if total_value else None,
                "off_screen_held": sorted(held_off_screen),
                "no_signal_held": sorted(held_no_signal),
            }
        except Exception as e:
            brief["risk"] = {"error": str(e)}

        return brief

    @mcp.tool(name="conviction_diff")
    def conviction_diff(days_back: int = 7) -> dict[str, Any]:
        """
        Show what changed in vc_loop conviction scores since the last run.

        Compares the most recent thesis entries against entries from
        `days_back` days ago and surfaces:
          - new entrants (first seen in last run)
          - exits (dropped off the ledger)
          - conviction movers (biggest score changes)
          - tier changes (e.g. watch → accumulate)

        Args:
            days_back: How many days back to compare against (default 7).

        Returns:
            new_entrants, exits, movers, tier_changes, as_of
        """
        try:
            from maverick_mcp.data.models import SessionLocal
            from maverick_mcp.vc_loop.models import ThesisLedger

            cutoff = datetime.now(UTC) - timedelta(days=days_back)

            with SessionLocal() as session:
                all_rows = (
                    session.query(ThesisLedger)
                    .order_by(ThesisLedger.thesis_date.desc())
                    .limit(500)
                    .all()
                )

            # Latest score per ticker
            latest: dict[str, dict] = {}
            prior: dict[str, dict] = {}
            for r in all_rows:
                td = r.thesis_date
                if hasattr(td, "date"):
                    td_aware = td.replace(tzinfo=UTC) if td.tzinfo is None else td
                else:
                    td_aware = datetime.now(UTC)

                entry = {
                    "ticker": r.ticker,
                    "conviction": float(r.conviction or 0),
                    "status": r.status,
                    "thesis_date": r.thesis_date.isoformat() if hasattr(r.thesis_date, "isoformat") else str(r.thesis_date),
                }
                if r.ticker not in latest:
                    latest[r.ticker] = entry
                if td_aware < cutoff and r.ticker not in prior:
                    prior[r.ticker] = entry

            latest_tickers = set(latest)
            prior_tickers = set(prior)

            new_entrants = [
                latest[t] for t in sorted(latest_tickers - prior_tickers)
            ]
            exits = [
                prior[t] for t in sorted(prior_tickers - latest_tickers)
            ]

            movers = []
            for t in latest_tickers & prior_tickers:
                delta = latest[t]["conviction"] - prior[t]["conviction"]
                if abs(delta) >= 5:
                    movers.append({
                        "ticker": t,
                        "conviction_now": latest[t]["conviction"],
                        "conviction_then": prior[t]["conviction"],
                        "delta": round(delta, 1),
                        "direction": "up" if delta > 0 else "down",
                    })
            movers.sort(key=lambda x: abs(x["delta"]), reverse=True)

            accumulate, watch, off_screen = _get_screen_sets()

            def _screen_tier(ticker: str) -> str:
                if ticker in accumulate:
                    return "accumulate"
                if ticker in watch:
                    return "watch"
                if ticker in off_screen:
                    return "off_screen"
                return "unknown"

            tier_changes = []
            for t in latest_tickers & prior_tickers:
                tier_now = _screen_tier(t)
                # We don't store historical tiers, so flag any off-screen names
                # that previously had high conviction as notable changes
                if tier_now == "off_screen" and prior[t]["conviction"] >= 50:
                    tier_changes.append({
                        "ticker": t,
                        "note": "was high-conviction, now off-screen",
                        "conviction_was": prior[t]["conviction"],
                    })

            return {
                "as_of": datetime.now(UTC).isoformat(),
                "comparison_window_days": days_back,
                "new_entrants": new_entrants,
                "exits": exits,
                "conviction_movers": movers[:10],
                "tier_changes": tier_changes,
                "total_tracked": len(latest),
            }

        except Exception as e:
            logger.error("conviction_diff error: %s", e)
            return {"error": str(e)}
