"""Daily intelligence run — vc_loop scoring + brief snapshot.

Runs automatically via launchd (see scripts/setup_launchd.sh).
Safe to run manually: uv run python scripts/daily_intelligence_run.py

Does NOT execute any trades. Proposals only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "logs" / "daily_intelligence.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("daily_intelligence")


def sync_positions() -> list[str]:
    """Pull live Schwab positions into mcp_portfolio_positions. Returns synced tickers."""
    try:
        from maverick_mcp.api.routers.schwab import _load_schwab
        from maverick_mcp.providers.schwab import SchwabClient
        from maverick_mcp.providers.schwab.sync import sync_schwab_portfolio

        config, store = _load_schwab()
        client = SchwabClient(config, store)
        result = sync_schwab_portfolio(client, portfolio_name="My Portfolio")
        count = result.get("positions_synced", 0)
        tickers = result.get("tickers", [])
        logger.info("Synced %d positions from Schwab: %s", count, tickers)
        return tickers
    except Exception as exc:
        logger.warning("Schwab sync skipped: %s", exc)
        return []


def _get_prev_snapshot_tickers() -> set[str]:
    """Return held tickers from the most recent position_snapshots batch."""
    from sqlalchemy import func

    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.models import PositionSnapshot

    with SessionLocal() as session:
        latest_at = (
            session.query(func.max(PositionSnapshot.snapshot_at))
            .filter(PositionSnapshot.position_closed == False)  # noqa: E712
            .scalar()
        )
        if not latest_at:
            return set()
        rows = (
            session.query(PositionSnapshot.ticker)
            .filter(
                PositionSnapshot.snapshot_at == latest_at,
                PositionSnapshot.position_closed == False,  # noqa: E712
            )
            .all()
        )
        return {r.ticker for r in rows}


def _write_position_snapshots(
    positions: list[dict],
    conviction_scores: dict[str, float],
    snapshot_at: datetime,
) -> None:
    """Write one PositionSnapshot per held position."""
    from maverick_mcp.data.models import PortfolioPosition, SessionLocal, UserPortfolio
    from maverick_mcp.vc_loop.models import PositionSnapshot

    avg_costs: dict[str, float] = {}
    with SessionLocal() as session:
        portfolio = (
            session.query(UserPortfolio)
            .filter_by(user_id="default", name="My Portfolio")
            .first()
        )
        if portfolio:
            for row in session.query(PortfolioPosition).filter_by(portfolio_id=portfolio.id).all():
                if row.average_cost_basis:
                    avg_costs[row.ticker.upper()] = float(row.average_cost_basis)

    snaps = []
    for pos in positions:
        ticker = pos["ticker"].upper()
        shares = float(pos.get("shares") or 0)
        market_value = float(pos.get("market_value") or 0)
        market_price = (market_value / shares) if shares else None
        avg_cost = avg_costs.get(ticker)
        cost_basis = (avg_cost * shares) if (avg_cost and shares) else None
        unrealized_pnl = (market_value - cost_basis) if cost_basis is not None else None
        unrealized_pnl_pct = (
            (unrealized_pnl / cost_basis * 100)
            if (cost_basis and cost_basis > 0 and unrealized_pnl is not None)
            else None
        )
        snaps.append(
            PositionSnapshot(
                snapshot_at=snapshot_at,
                ticker=ticker,
                shares=shares,
                avg_cost=avg_cost,
                market_price=market_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                conviction_at_snapshot=conviction_scores.get(ticker),
            )
        )

    with SessionLocal() as session:
        session.add_all(snaps)
        session.commit()

    logger.info("PositionSnapshot saved: %d positions", len(snaps))


def _close_thesis_outcomes(
    prev_tickers: set[str],
    current_tickers: set[str],
    snapshot_at: datetime,
) -> None:
    """Mark ThesisLedger rows closed for positions that exited since last run."""
    closed = prev_tickers - current_tickers
    if not closed:
        return

    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.models import PositionSnapshot, ThesisLedger

    with SessionLocal() as session:
        for ticker in closed:
            thesis = (
                session.query(ThesisLedger)
                .filter(
                    ThesisLedger.ticker == ticker,
                    ThesisLedger.status != "closed",
                )
                .order_by(ThesisLedger.thesis_date.desc())
                .first()
            )
            if not thesis:
                continue

            first_snap = (
                session.query(PositionSnapshot)
                .filter(
                    PositionSnapshot.ticker == ticker,
                    PositionSnapshot.position_closed == False,  # noqa: E712
                )
                .order_by(PositionSnapshot.snapshot_at.asc())
                .first()
            )
            last_snap = (
                session.query(PositionSnapshot)
                .filter(
                    PositionSnapshot.ticker == ticker,
                    PositionSnapshot.position_closed == False,  # noqa: E712
                )
                .order_by(PositionSnapshot.snapshot_at.desc())
                .first()
            )

            entry_price = first_snap.avg_cost if first_snap else None
            exit_price = last_snap.market_price if last_snap else None
            return_pct = None
            if entry_price and exit_price and entry_price > 0:
                return_pct = round((exit_price - entry_price) / entry_price * 100, 2)

            thesis.outcome = {
                "exit_date": snapshot_at.isoformat(),
                "exit_price": exit_price,
                "entry_price": entry_price,
                "return_pct": return_pct,
                "last_conviction": last_snap.conviction_at_snapshot if last_snap else None,
            }
            thesis.status = "closed"

            session.add(
                PositionSnapshot(
                    snapshot_at=snapshot_at,
                    ticker=ticker,
                    shares=0.0,
                    position_closed=True,
                )
            )

        session.commit()

    logger.info("Closed thesis outcomes for: %s", sorted(closed))


async def run_vc_loop_pass() -> int:
    """Score the investment universe and write to ledger + Obsidian."""
    from maverick_mcp.vc_loop.orchestrator import run_vc_loop

    vault_path = os.getenv(
        "OBSIDIAN_VAULT_PATH",
        str(ROOT / "obsidian"),
    )
    result = await run_vc_loop(
        vault_path=vault_path,
        strategy="maverick_bullish",
        limit=30,        # screener candidates (niche growth universe)
        top_n=30,        # enrich ALL of them (small universe, worth the cost)
        days_ahead=30,
    )
    count = result.get("count", 0)
    logger.info("vc_loop complete: %d candidates scored", count)
    return count


def save_snapshot() -> dict:
    """Pull live positions, save a brief snapshot + per-position P&L rows.

    Returns the brief dict (for use in the Telegram alert) or {} on failure.
    """
    from maverick_mcp.api.routers.investment_ops import (
        _fetch_corr_and_vols,
        _get_broker_positions,
        _get_conviction_scores,
        _get_new_opportunities,
        _get_screen_sets,
        _portfolio_stats,
        _save_brief_snapshot,
    )
    from maverick_mcp.vc_loop import diversification as dv

    snapshot_at = datetime.now(UTC)
    prev_tickers = _get_prev_snapshot_tickers()

    positions = _get_broker_positions()
    if not positions:
        logger.warning("No positions found — broker may be offline. Snapshot skipped.")
        return {}

    total_value = sum(p["market_value"] for p in positions)
    held_tickers = {p["ticker"].upper() for p in positions}
    accumulate, _, off_screen = _get_screen_sets()
    conviction_scores = _get_conviction_scores()

    # Diversification (Dalio layer) — track effective bets over time.
    diversification: dict = {"applied": False}
    try:
        tickers = [p["ticker"].upper() for p in positions]
        weights = {p["ticker"].upper(): p["market_value"] / total_value for p in positions}
        corr, vols = _fetch_corr_and_vols(tickers)
        if corr:
            clusters = dv.cluster_positions(corr, list(corr.keys()))
            _, caps_log = dv.apply_cluster_caps(
                {t: weights.get(t, 0) for t in tickers}, clusters
            )
            eb = dv.effective_bets(weights, corr)
            diversification = {
                "applied": True,
                "effective_bets": round(eb, 1),
                "grade": dv.diversification_grade(eb),
                "clusters_capped": caps_log,
            }
    except Exception as exc:
        logger.warning("Diversification calc failed: %s", exc)

    brief: dict = {
        "generated_at": snapshot_at.isoformat(),
        "portfolio": {
            "position_count": len(positions),
            "equity_value": round(total_value, 2),
            "total_value": round(total_value, 2),
        },
        "stats": _portfolio_stats(
            positions, conviction_scores, accumulate, off_screen, total_value
        ),
        "diversification": diversification,
        "screen": {
            "held_accumulate": sorted(accumulate & held_tickers),
            "held_off_screen": sorted(off_screen & held_tickers),
        },
        "new_opportunities": _get_new_opportunities(held_tickers),
        "rebalance": {},
    }
    _save_brief_snapshot(brief)
    logger.info(
        "Snapshot saved: equity=$%.0f positions=%d conviction=%.1f effective_bets=%s",
        total_value,
        len(positions),
        brief["stats"].get("portfolio_conviction_score") or 0,
        diversification.get("effective_bets", "n/a"),
    )

    _write_position_snapshots(positions, conviction_scores, snapshot_at)
    _close_thesis_outcomes(prev_tickers, held_tickers, snapshot_at)

    return brief


def review_and_learn() -> dict:
    """Close due theses, recompute learned weights, and persist them."""
    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.calibration import (
        brier_score,
        review_theses,
        save_learned_weights,
    )
    from maverick_mcp.vc_loop.models import ThesisLedger

    with SessionLocal() as session:
        result = review_theses(session, days_after=7, limit=500, update_weights=True)

    reviewed = result.get("reviewed_count", 0)
    cal = result.get("calibration", {})
    lw = cal.get("learned_weights", {})

    if lw.get("status") == "ok":
        weights = lw["weights"]
        sample_count = lw.get("sample_count", 0)
        brier = cal.get("brier_score")

        # Fetch all rows for brier if not already computed
        if brier is None:
            with SessionLocal() as session:
                all_rows = session.query(ThesisLedger).all()
                brier = brier_score(all_rows)

        save_learned_weights(weights, brier=brier, sample_count=sample_count)
        logger.info(
            "review_and_learn: %d theses reviewed, weights updated from %d outcomes (brier=%.4f)",
            reviewed,
            sample_count,
            brier or 0.0,
        )
    else:
        logger.info(
            "review_and_learn: %d theses reviewed, %s (need ≥5 closed outcomes)",
            reviewed,
            lw.get("status", "no update"),
        )

    return result


def send_run_alert(brief: dict) -> None:
    """Send a Telegram push notification with the run digest."""
    import os

    import requests

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return

    portfolio = brief.get("portfolio", {})
    stats = brief.get("stats", {})
    opps = brief.get("new_opportunities", [])

    equity = portfolio.get("equity_value") or 0
    positions_count = portfolio.get("position_count") or 0
    conviction = stats.get("portfolio_conviction_score") or 0
    deployed_pct = stats.get("deployed_pct") or 0
    regime = brief.get("regime", "")

    run_time = datetime.now().strftime("%I:%M%p").lstrip("0")

    # Top opportunities (not yet held, high conviction)
    top_opps = sorted(opps, key=lambda x: x.get("conviction", 0), reverse=True)[:3]
    opp_lines = "\n".join(
        f"  `{o['ticker']}` {o.get('conviction', 0):.0f} · {o.get('action', 'watch')}"
        for o in top_opps
    ) or "  _none_"

    regime_str = f" · {regime}" if regime else ""
    equity_str = f"${equity / 1000:.0f}k" if equity >= 1000 else f"${equity:.0f}"

    text = (
        f"*Maverick {run_time}* — {equity_str} | Conviction {conviction:.1f}{regime_str}\n\n"
        f"💼 {equity_str} · {deployed_pct:.0f}% deployed · {positions_count} positions\n\n"
        f"*Top signals*\n{opp_lines}"
    )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.ok:
            logger.info("Alert sent via Telegram")
        else:
            logger.warning("Telegram alert failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Telegram alert error: %s", exc)


async def main() -> None:
    logger.info("=== Maverick daily intelligence run — %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    # 1. Sync live positions from Schwab into local DB
    try:
        sync_positions()
    except Exception as exc:
        logger.error("Position sync failed: %s", exc, exc_info=True)

    # 2. Score the investment universe
    try:
        await run_vc_loop_pass()
    except Exception as exc:
        logger.error("vc_loop failed: %s", exc, exc_info=True)

    # 3. Save portfolio snapshot + position P&L rows; capture brief for alert
    brief: dict = {}
    try:
        brief = save_snapshot() or {}
    except Exception as exc:
        logger.error("Snapshot failed: %s", exc, exc_info=True)

    # 4. Close due theses (≥7 days old) and persist updated conviction weights
    try:
        review_and_learn()
    except Exception as exc:
        logger.error("review_and_learn failed: %s", exc, exc_info=True)

    # 5. Push Telegram digest
    try:
        send_run_alert(brief)
    except Exception as exc:
        logger.error("Alert failed: %s", exc, exc_info=True)

    logger.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
