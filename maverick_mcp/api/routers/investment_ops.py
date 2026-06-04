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


def _conviction_to_target_pct(
    score: float | None,
    on_accumulate: bool,
    on_off_screen: bool,
) -> float:
    """
    Map conviction score + screen status to a dynamic target portfolio weight.

    Off-screen names always exit (0%). On-screen names scale continuously with
    conviction so high-conviction positions earn more size automatically.

    Curve (score → target weight):
      ≥ 80  →  6–8%   (max conviction, core position)
      65–79 →  4–6%   (high conviction)
      50–64 →  2–4%   (moderate conviction)
      35–49 →  0.5–2% (weak conviction, hold small)
      < 35  →  0%     (exit)
      on screen, no score → 3% default
      not on screen → 2% cap (hold, no add)
    """
    if on_off_screen:
        return 0.0
    if not on_accumulate:
        # Not in our universe — hold at small size, no add signal
        if score and score >= 65:
            return 0.02
        return 0.015
    if score is None:
        return 0.03  # on screen but no vc_loop data yet
    if score >= 80:
        return min(0.08, 0.06 + (score - 80) / 20 * 0.02)
    elif score >= 65:
        return 0.04 + (score - 65) / 15 * 0.02
    elif score >= 50:
        return 0.02 + (score - 50) / 15 * 0.02
    elif score >= 35:
        return 0.005 + (score - 35) / 15 * 0.015
    else:
        return 0.0  # exit below conviction 35


def _portfolio_stats(
    positions: list[dict],
    conviction_scores: dict[str, float],
    accumulate: set,
    off_screen: set,
    total_value: float,
) -> dict:
    """Conviction-weighted intelligence metrics — used for the brief synthesis."""
    if not positions or total_value <= 0:
        return {}
    weighted_sum = 0.0
    weight_total = 0.0
    high_conv_value = 0.0
    off_screen_value = 0.0
    on_screen_value = 0.0
    no_score_value = 0.0

    for pos in positions:
        tkr = pos["ticker"].upper()
        mv = pos["market_value"]
        score = conviction_scores.get(tkr)
        w = mv / total_value
        if score is not None:
            weighted_sum += score * w
            weight_total += w
            if score >= 65:
                high_conv_value += mv
        else:
            no_score_value += mv
        if tkr in off_screen:
            off_screen_value += mv
        elif tkr in accumulate:
            on_screen_value += mv

    scored = sorted(
        [{"ticker": p["ticker"].upper(), "score": conviction_scores[p["ticker"].upper()]}
         for p in positions if p["ticker"].upper() in conviction_scores],
        key=lambda x: x["score"], reverse=True,
    )
    return {
        "portfolio_conviction_score": round(weighted_sum / weight_total, 1) if weight_total else None,
        "high_conviction_pct": round(high_conv_value / total_value * 100, 1),
        "on_screen_pct": round(on_screen_value / total_value * 100, 1),
        "off_screen_held_pct": round(off_screen_value / total_value * 100, 1),
        "no_conviction_data_pct": round(no_score_value / total_value * 100, 1),
        "top_conviction": [s["ticker"] for s in scored[:5]],
        "lowest_conviction": [s["ticker"] for s in scored if s["score"] < 50][:3],
    }


def _get_screen_sets() -> tuple[set, set, set]:
    """Return (accumulate, watch, off_screen) ticker sets — ADR-filtered growth names only."""
    try:
        from maverick_mcp.api.routers.screening import (
            get_maverick_stocks,
            get_maverick_bear_stocks,
        )
        bull = get_maverick_stocks(limit=100, min_adr_pct=3.5)
        bear = get_maverick_bear_stocks(limit=100)
        accumulate = {(s.get("ticker") or "").upper() for s in bull.get("stocks", []) if s.get("ticker")}
        off_screen  = {(s.get("ticker") or "").upper() for s in bear.get("stocks", []) if s.get("ticker")}
        return accumulate, set(), off_screen
    except Exception as e:
        logger.warning("Could not load screen sets: %s", e)
        return set(), set(), set()


def _get_new_opportunities(held_tickers: set, limit: int = 6) -> list[dict]:
    """Return niche screener hits not in the book, with one-line rationale."""
    try:
        from maverick_mcp.api.routers.screening import get_maverick_stocks
        result = get_maverick_stocks(limit=100, min_adr_pct=4.0)
        opps = []
        for s in result.get("stocks", []):
            tkr = (s.get("ticker") or "").upper()
            if not tkr or tkr in held_tickers:
                continue
            # Prefer niche names (sector=None) over well-known large caps
            sector = s.get("sector")
            if sector is not None:
                continue  # skip named-sector stocks for new opps; they're known names
            pattern = s.get("pattern") or "Uptrend"
            adr = s.get("adr_pct") or 0
            score = s.get("combined_score") or 0
            opps.append({
                "ticker": tkr,
                "why": f"{pattern} · ADR {adr:.1f}% · score {score}",
                "adr_pct": adr,
                "combined_score": score,
            })
            if len(opps) >= limit:
                break
        return opps
    except Exception as e:
        logger.warning("Could not load new opportunities: %s", e)
        return []


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
        available_cash: float = 0.0,
    ) -> dict[str, Any]:
        """
        Conviction-driven rebalance with cash-first deployment.

        Position targets are computed from vc_loop conviction scores on a
        continuous curve (not fixed bands), so high-conviction names earn more
        size automatically. Available cash is deployed into underweight positions
        before any trimming is suggested — trims are only recommended when adds
        exceed available liquidity, or for concentration reduction.

        Args:
            total_portfolio_value: Override total portfolio value (equity only).
            available_cash: Idle cash available to deploy. Pass from broker
                account_summary for accurate cash-first logic.

        Returns:
            actions: per-ticker actions with conviction target, delta, and funding source
            cash_summary: how much cash deploys, whether trims are needed for funding
        """
        positions = _get_broker_positions()
        if not positions:
            return {"error": "No positions found. Check broker connection."}

        accumulate, _, off_screen = _get_screen_sets()
        conviction_scores = _get_conviction_scores()

        total_value = total_portfolio_value or sum(p["market_value"] for p in positions)
        if total_value <= 0:
            return {"error": "Cannot compute weights: total portfolio value is zero."}

        actions = []
        for pos in positions:
            ticker = pos["ticker"].upper()
            mv = pos["market_value"]
            current_pct = mv / total_value
            score = conviction_scores.get(ticker)

            on_acc = ticker in accumulate
            on_off = ticker in off_screen
            target_pct = _conviction_to_target_pct(score, on_acc, on_off)
            delta_pct = target_pct - current_pct
            delta_dollars = delta_pct * total_value

            if abs(delta_dollars) < 50:
                action = "hold"
            elif delta_dollars > 0:
                action = "add"
            else:
                action = "trim" if target_pct > 0 else "exit"

            cur_s = f"{current_pct*100:.1f}%"
            tgt_s = f"{target_pct*100:.1f}%"
            score_s = f" · conviction {score:.0f}" if score else " · no vc_loop score"
            if action == "exit":
                rationale = f"Off screen — exit fully ({cur_s} → 0%)"
            elif action == "trim":
                rationale = (
                    f"{'On screen' if on_acc else 'Not in universe'}, {cur_s} → {tgt_s} target"
                    f"{score_s} — trim ${abs(delta_dollars):.0f} (concentration reduction)"
                )
            elif action == "add":
                rationale = (
                    f"On screen, underweight: {cur_s} → {tgt_s} target"
                    f"{score_s} — add ${delta_dollars:.0f}"
                )
            else:
                rationale = f"At target {tgt_s}{score_s} — hold"

            actions.append({
                "ticker": ticker,
                "on_screen": on_acc,
                "on_off_screen": on_off,
                "conviction_score": score,
                "current_value": round(mv, 2),
                "current_pct": round(current_pct * 100, 2),
                "target_pct": round(target_pct * 100, 2),
                "action": action,
                "delta_dollars": round(delta_dollars, 2),
                "rationale": rationale,
            })

        exits = [a for a in actions if a["action"] == "exit"]
        adds_raw = sorted(
            [a for a in actions if a["action"] == "add"],
            key=lambda a: (a.get("conviction_score") or 0), reverse=True,
        )
        trims_raw = sorted(
            [a for a in actions if a["action"] == "trim"],
            key=lambda a: abs(a["delta_dollars"]), reverse=True,
        )
        holds = [a for a in actions if a["action"] == "hold"]

        # Cash-first: fund adds from available cash before trimming
        remaining_cash = available_cash
        adds: list[dict] = []
        for add in adds_raw:
            needed = add["delta_dollars"]
            if remaining_cash >= needed:
                add["funded_by"] = "cash"
                add["cash_used"] = round(needed, 2)
                remaining_cash -= needed
            elif remaining_cash > 0:
                add["funded_by"] = "cash_and_trim"
                add["cash_used"] = round(remaining_cash, 2)
                add["still_needs"] = round(needed - remaining_cash, 2)
                remaining_cash = 0
            else:
                add["funded_by"] = "requires_trim"
                add["cash_used"] = 0.0
                add["still_needs"] = round(needed, 2)
            adds.append(add)

        trim_needed_for_funding = round(sum(
            a.get("still_needs", 0) for a in adds
            if a.get("funded_by") in ("cash_and_trim", "requires_trim")
        ), 2)

        # Tag trims: funding vs concentration reduction
        trims: list[dict] = []
        cumulative = 0.0
        for trim in trims_raw:
            if cumulative < trim_needed_for_funding:
                trim["purpose"] = "funding"
                cumulative += abs(trim["delta_dollars"])
            else:
                trim["purpose"] = "concentration_reduction"
            trims.append(trim)

        all_actions = exits + trims + adds + holds
        return {
            "as_of": datetime.now(UTC).isoformat(),
            "total_portfolio_value": round(total_value, 2),
            "available_cash": round(available_cash, 2),
            "actions": all_actions,
            "cash_summary": {
                "cash_available": round(available_cash, 2),
                "cash_to_deploy": round(available_cash - remaining_cash, 2),
                "cash_remaining": round(remaining_cash, 2),
                "trim_needed_for_funding": trim_needed_for_funding,
                "trims_are_for_concentration_only": trim_needed_for_funding == 0,
            },
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

        # --- Rebalance (cash-first: pass actual cash so adds are funded from cash) ---
        action_by_ticker: dict[str, dict] = {}
        try:
            rebalance = portfolio_rebalance_suggestions(
                total_portfolio_value=total_value,
                available_cash=cash_balance or 0.0,
            )
            all_actions = rebalance.get("actions", [])
            action_by_ticker = {a["ticker"]: a for a in all_actions}
            exits = [a for a in all_actions if a["action"] == "exit"]
            trims = [a for a in all_actions if a["action"] == "trim"]
            adds  = [a for a in all_actions if a["action"] == "add"]

            capital_freed = round(sum(abs(a["delta_dollars"]) for a in exits + trims), 2)
            capital_needed = round(sum(a["delta_dollars"] for a in adds), 2)

            def _action_detail(a: dict) -> dict:
                return {
                    "ticker": a["ticker"],
                    "current_pct": a.get("current_pct"),
                    "target_pct": a.get("target_pct"),
                    "delta_dollars": a.get("delta_dollars"),
                    "conviction_score": a.get("conviction_score"),
                    "why": a.get("rationale", ""),
                }

            brief["rebalance"] = {
                "cash_summary": rebalance.get("cash_summary", {}),
                "exits": [_action_detail(a) for a in exits],
                "top_trims": [_action_detail(a) for a in trims[:6]],
                "top_adds": [_action_detail(a) for a in adds[:6]],
            }
        except Exception as e:
            brief["rebalance"] = {"error": str(e)}
            exits, trims, adds = [], [], []

        # --- Portfolio intelligence stats ---
        try:
            accumulate_for_stats, _, off_screen_for_stats = _get_screen_sets()
            conviction_scores = _get_conviction_scores()
            brief["stats"] = _portfolio_stats(
                positions, conviction_scores,
                accumulate_for_stats, off_screen_for_stats, total_value,
            )
        except Exception as e:
            brief["stats"] = {"error": str(e)}

        # --- Screen results (with per-position sizing) ---
        accumulate, _, off_screen = _get_screen_sets()
        held_accumulate = accumulate & held_tickers
        held_off_screen = off_screen & held_tickers
        held_no_signal = held_tickers - accumulate - off_screen

        def _screen_detail(tkr: str) -> dict:
            a = action_by_ticker.get(tkr, {})
            return {
                "ticker": tkr,
                "current_pct": a.get("current_pct"),
                "target_pct": a.get("target_pct"),
                "delta_dollars": a.get("delta_dollars"),
                "action": a.get("action", "hold"),
                "why": a.get("rationale", ""),
            }

        brief["screen"] = {
            "held_accumulate": [_screen_detail(t) for t in sorted(held_accumulate)],
            "held_no_signal":  [_screen_detail(t) for t in sorted(held_no_signal)],
            "held_off_screen": [_screen_detail(t) for t in sorted(held_off_screen)],
        }

        # --- New opportunities: niche thematic names not in book ---
        brief["new_opportunities"] = _get_new_opportunities(held_tickers, limit=6)

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
