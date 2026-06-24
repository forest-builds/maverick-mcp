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


# ── Portfolio constants ────────────────────────────────────────────────────────

# Traditional (margin) account — long-term holds, lower monitoring frequency.
TRADITIONAL_TICKERS = {"NVDA", "PLTR", "IREN", "OKLO", "COIN"}

# Roth position themes — used for correlated-move detection and attention map.
THEMES: dict[str, set[str]] = {
    "Space": {"RDW", "PL", "LUNR", "ASTS", "RKLB"},
    "Drones/Autonomy": {"ONDS", "RCAT", "JOBY"},
    "Nuclear/Materials": {"UUUU", "USAR", "UEC", "MP", "KRKNF"},
    "Quantum": {"IONQ", "RGTI"},
    "Energy Storage": {"ENVX", "EOSE"},
    "Precision Tech": {"CGNX"},
}

CASH_TARGET = (0.10, 0.15)   # 10–15% is the target band
CASH_FLOOR  = 0.10           # never deploy below this


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _fetch_benchmark_return() -> dict[str, float | None]:
    """Fetch today's % return for QQQ, IWM, SPY. Returns {} on import failure or error."""
    try:
        import yfinance as yf
        result: dict[str, float | None] = {}
        for sym in ("QQQ", "IWM", "SPY"):
            try:
                hist = yf.Ticker(sym).history(period="2d")
                if len(hist) >= 2:
                    prev, today = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                    result[sym] = (today - prev) / prev * 100
                else:
                    result[sym] = None
            except Exception:
                result[sym] = None
        return result
    except ImportError:
        return {}


def _slot_meta(now: datetime) -> tuple[str, str, str, str]:
    """Return (emoji, label, next_run_str, context) for the nearest of 3 daily slots."""
    schedule = [(8, 0), (12, 30), (16, 15)]
    labels   = ["🌅 Morning", "☀️ Midday", "🌙 Close"]
    contexts = [
        "Command brief — prepare for the day",
        "Exception report — material changes only",
        "Decision memo — what we learned, what\\'s next",
    ]
    total = now.hour * 60 + now.minute
    slot_mins = [h * 60 + m for h, m in schedule]
    idx = min(range(len(slot_mins)), key=lambda i: abs(slot_mins[i] - total))
    emoji_label = labels[idx]
    emoji, label = emoji_label.split(" ", 1)
    next_idx = idx + 1
    if next_idx < len(schedule):
        nh, nm = schedule[next_idx]
        suffix = "am" if nh < 12 else "pm"
        disp_h = nh if nh <= 12 else nh - 12
        next_str = f"{disp_h}:{nm:02d}{suffix}"
    else:
        next_str = "8:00am tomorrow"
    return emoji, label, next_str, contexts[idx]


def _theme_analysis(
    roth_rows: list,
    prev_value_by_ticker: dict[str, float],
    roth_total: float = 0.0,
) -> list[dict]:
    """Return notable theme-level findings sorted by priority, with dollar weights."""
    findings = []
    for theme_name, theme_tickers in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in theme_tickers]
        if len(in_theme) < 2:
            continue
        theme_value      = sum(r.market_value or 0 for r in in_theme)
        theme_pct        = (theme_value / roth_total * 100) if roth_total > 0 else 0.0
        theme_dollar_delta = 0.0
        moves: list[tuple[str, float]] = []
        for r in in_theme:
            prev = prev_value_by_ticker.get(r.ticker)
            cur  = r.market_value or 0
            if prev and prev > 0:
                pct = (cur - prev) / prev * 100
                theme_dollar_delta += cur - prev
                moves.append((r.ticker, pct))
        if not moves:
            continue

        all_up  = all(p > 1.0  for _, p in moves)
        all_dn  = all(p < -1.0 for _, p in moves)
        diverge = any(p > 2.0 for _, p in moves) and any(p < -2.0 for _, p in moves)
        tickers_str = "  ".join(
            f"{t}{'+' if p >= 0 else ''}{p:.1f}%"
            for t, p in sorted(moves, key=lambda x: -x[1])
        )
        weight_str = (
            f"${theme_value/1000:.1f}k / {theme_pct:.0f}% of Roth / {len(in_theme)} pos"
        )

        base = {
            "theme": theme_name,
            "weight": weight_str,
            "dollar_delta": theme_dollar_delta,
            "theme_value": theme_value,
            "theme_pct": theme_pct,
            "n_positions": len(in_theme),
        }
        if all_dn and len(moves) >= 3:
            findings.append({**base, "priority": "high",
                "title": f"{theme_name} basket all red",
                "detail": f"{tickers_str} — correlated move, treat as one risk event"})
        elif all_up and len(moves) >= 3:
            findings.append({**base, "priority": "low",
                "title": f"{theme_name} basket all green",
                "detail": f"{tickers_str} — correlated strength"})
        elif diverge:
            findings.append({**base, "priority": "medium",
                "title": f"{theme_name} diverging",
                "detail": f"{tickers_str} — investigate before acting"})

    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(findings, key=lambda x: order[x["priority"]])


def _interpret_cash(cash: float, roth_total: float) -> list[str]:
    """Return lines describing cash position vs target band, showing both floor and target."""
    if roth_total <= 0:
        return []
    pct        = cash / roth_total * 100
    floor_val  = roth_total * CASH_FLOOR
    target_val = roth_total * CASH_TARGET[1]
    lo, hi     = CASH_TARGET

    above_floor  = max(0.0, cash - floor_val)
    above_target = max(0.0, cash - target_val)
    pct_str = f"${cash/1000:.1f}k / {pct:.0f}%"

    if pct < lo * 100:
        return [f"Cash {pct_str} — below {lo*100:.0f}% floor, no deployment"]
    elif pct > hi * 100:
        return [
            f"Cash {pct_str} — above {lo*100:.0f}–{hi*100:.0f}% target",
            f"  Above {lo*100:.0f}% floor:  ${above_floor:,.0f} available",
            f"  Above {hi*100:.0f}% target: ${above_target:,.0f} available",
        ]
    else:
        return [f"Cash {pct_str} — within {lo*100:.0f}–{hi*100:.0f}% target"]


def _capital_conviction_issues(
    roth_rows: list,
    conviction_by_ticker: dict[str, float],
) -> list[str]:
    """Return plain-English misalignment warnings."""
    by_value = sorted(roth_rows, key=lambda r: r.market_value or 0, reverse=True)
    by_cv    = sorted(roth_rows, key=lambda r: conviction_by_ticker.get(r.ticker, 0), reverse=True)
    top_val_set = {r.ticker for r in by_value[:5]}
    top_cv_set  = {r.ticker for r in by_cv[:5]}

    issues = []
    low_cv_cap = sum(r.market_value or 0 for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40)
    if low_cv_cap >= 1500:
        low_cv_count = sum(1 for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40)
        issues.append(
            f"${low_cv_cap:,.0f} across {low_cv_count} sub-40 conviction positions"
            f" — capital not matching scores"
        )

    mismatched = [r for r in by_value[:3] if r.ticker not in top_cv_set]
    if mismatched:
        names = "  ".join(
            f"{r.ticker} cv{conviction_by_ticker.get(r.ticker, 0):.0f}"
            for r in mismatched[:2]
        )
        issues.append(f"Large positions with low conviction: {names}")

    return issues


# ── Morning slot (8am) ────────────────────────────────────────────────────────

def _send_morning_command_brief(
    *,
    tg,
    roth_rows: list,
    conviction_by_ticker: dict,
    theme_findings: list,
    cash: float,
    roth_equity: float,
    roth_total: float,
    off_screen: list,
    fmt_k,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Single morning message: posture + attention map + action board."""
    from datetime import date

    run_time = now.strftime("%I:%M%p").lstrip("0")
    today    = date.today().strftime("%b %d")
    deployed_pct = int(100 * roth_equity / roth_total) if roth_total else 100

    lines = [f"🌅 *MAVERICK — MORNING BRIEF*", f"_{today} · {run_time} ET_", ""]

    # Portfolio
    lines.append("*PORTFOLIO*")
    lines.append(
        f"Roth {fmt_k(roth_total)}  ·  {len(roth_rows)} positions  ·  {deployed_pct}% deployed"
    )

    # Top 2 theme concentrations by value
    theme_totals = sorted(
        [
            (name, sum(r.market_value or 0 for r in roth_rows if r.ticker in tks))
            for name, tks in THEMES.items()
            if sum(1 for r in roth_rows if r.ticker in tks) >= 2
        ],
        key=lambda x: x[1], reverse=True,
    )
    if theme_totals:
        lines.append("Largest concentrations:")
        for name, val in theme_totals[:2]:
            pct = int(val / roth_total * 100) if roth_total else 0
            n   = sum(1 for r in roth_rows if r.ticker in THEMES[name])
            lines.append(f"  • {name}: {fmt_k(val)} / {pct}% / {n} pos")
    lines.append("")

    # Cash interpretation — floor and target separately
    lines.extend(_interpret_cash(cash, roth_total))
    lines.append("")

    # Attention map — theme findings + capital misalignment
    attention: list[str] = []
    for item in theme_findings[:2]:
        attention.append(f"*{item['title']}* ({item['weight']})\n   {item['detail']}")
    for issue in _capital_conviction_issues(roth_rows, conviction_by_ticker)[:1]:
        attention.append(issue)
    attention = attention[:3]

    if attention:
        lines.append("*TODAY\\'S ATTENTION*")
        for i, item in enumerate(attention, 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    # Action board
    lines.append("*ACTION BOARD*")

    # INVESTIGATE — off-screen positions
    for t in off_screen[:2]:
        row = next((r for r in roth_rows if r.ticker == t), None)
        val = fmt_k(row.market_value or 0) if row else ""
        lines.append(f"🔴 *INVESTIGATE:* {t} {val} — off screen, review exit")

    # INVESTIGATE — largest sub-40 conviction positions (hold / no add)
    low_cv_by_val = sorted(
        [r for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40],
        key=lambda r: r.market_value or 0, reverse=True,
    )
    for r in low_cv_by_val[:2]:
        cv = conviction_by_ticker.get(r.ticker, 0)
        lines.append(
            f"🔴 *INVESTIGATE:* {r.ticker} cv{cv:.0f}  {fmt_k(r.market_value or 0)} "
            f"— hold / no add until thesis resolved"
        )

    # WATCH — theme baskets with 3+ positions, showing $ weight
    watch_added = 0
    for theme_name, theme_tickers in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in theme_tickers]
        if len(in_theme) >= 3:
            tv  = sum(r.market_value or 0 for r in in_theme)
            pct = int(tv / roth_total * 100) if roth_total else 0
            lines.append(
                f"🟡 *WATCH:* {theme_name} {fmt_k(tv)} / {pct}%"
                f" — alert on correlated basket move"
            )
            watch_added += 1
        if watch_added >= 2:
            break

    lines.append("🟢 *IGNORE:* Moves <3% with no news, no volume spike, no threshold breach")
    lines.append("")

    lines.append("_Price alone does not change conviction._")
    lines.append(f"_Next: {next_run} ET_")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i:i + 4000])


# ── Midday slot (12:30pm) ─────────────────────────────────────────────────────

def _send_midday_exception_report(
    *,
    tg,
    roth_rows: list,
    prev_value_by_ticker: dict,
    position_dollar_deltas: dict,
    theme_findings: list,
    day_delta: float,
    fmt_k,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Single midday message: exceptions only. Lists checks performed when quiet."""
    run_time = now.strftime("%I:%M%p").lstrip("0")

    # Identify big movers (>3%) not already covered by theme findings
    theme_reported: set[str] = set()
    for f in theme_findings:
        theme_reported.update(THEMES.get(f["theme"], set()))

    big_movers: list[tuple[str, float, float | None]] = []
    for r in roth_rows:
        prev = prev_value_by_ticker.get(r.ticker)
        cur  = r.market_value or 0
        if prev and prev > 0:
            pct = (cur - prev) / prev * 100
            if abs(pct) >= 3.0:
                big_movers.append((r.ticker, pct, r.unrealized_pnl_pct))
    big_movers.sort(key=lambda x: abs(x[1]), reverse=True)

    is_quiet = not big_movers and not theme_findings

    lines = [f"☀️ *MAVERICK MIDDAY — {run_time}*", ""]

    if is_quiet:
        lines.append("_No material exceptions._")
        lines.append("Checked: theme correlation, moves >3%, volume context, thesis triggers.")
        lines.append("No thesis changes. No action triggers. Portfolio steady.")
        lines.append("")
        lines.append(f"_Next: {next_run} ET_")
    else:
        direction = "strengthened" if day_delta >= 0 else "softened"
        delta_str = f"+${day_delta:,.0f}" if day_delta >= 0 else f"-${abs(day_delta):,.0f}"
        lines.append(f"*BOTTOM LINE:* Portfolio {direction} {delta_str} since open.")
        lines.append("")
        lines.append("*MATERIAL CHANGES*")

        # Theme basket moves first — 1 event, not N tickers
        reported_tickers: set[str] = set()
        for item in theme_findings[:3]:
            icon = "🔴" if item["priority"] == "high" else "🟠"
            dd   = item.get("dollar_delta", 0.0)
            dd_str = f"  ({'+' if dd >= 0 else ''}${dd:,.0f})" if abs(dd) >= 10 else ""
            lines.append(f"{icon} *{item['title']}*{dd_str} ({item['weight']})")
            lines.append(f"   {item['detail']}")
            lines.append("")
            reported_tickers.update(THEMES.get(item["theme"], set()))

        # Solo movers not covered by a theme basket
        solo_movers = [(t, p, tp) for t, p, tp in big_movers if t not in reported_tickers]
        for ticker, pct, total_pct in solo_movers[:3]:
            sign = "▲" if pct >= 0 else "▼"
            icon = "🔴" if abs(pct) >= 5 else "🟠"
            dd   = position_dollar_deltas.get(ticker, 0.0)
            dd_str = f"  ({'+' if dd >= 0 else ''}${dd:,.0f})" if abs(dd) >= 10 else ""
            in_any_theme = any(ticker in tks for tks in THEMES.values())
            classification = "possible sector move" if in_any_theme else "cause unclassified"
            lines.append(
                f"{icon} *{ticker}* {sign}{abs(pct):.1f}%{dd_str} — {classification}"
            )
            lines.append("")

        lines.append("_No trade without news/volume confirmation._")
        lines.append(f"_Next: {next_run} ET_")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i:i + 4000])


# ── Close slot (4:15pm) ───────────────────────────────────────────────────────

def _send_close_decision_memo(
    *,
    tg,
    roth_rows: list,
    conviction_by_ticker: dict,
    prev_value_by_ticker: dict,
    position_dollar_deltas: dict,
    theme_findings: list,
    day_delta: float,
    roth_equity: float,
    roth_total: float,
    cash: float,
    off_screen: list,
    benchmark_returns: dict,
    fmt_k,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Single close message: performance, attribution by $ and theme, risk, tomorrow's queue."""
    from datetime import date

    today    = date.today().strftime("%b %d")
    run_time = now.strftime("%I:%M%p").lstrip("0")
    deployed_pct   = int(100 * roth_equity / roth_total) if roth_total else 100
    prior_value    = roth_total - day_delta
    day_return_pct = (day_delta / prior_value * 100) if prior_value > 0 else 0.0
    delta_sign     = "+" if day_delta >= 0 else "-"
    delta_str      = f"{delta_sign}${abs(day_delta):,.0f}"

    lines = [f"🌙 *MAVERICK CLOSE — {today}*", f"_{run_time} ET_", ""]

    # Performance
    lines.append("*PERFORMANCE*")
    ret_sign = "+" if day_return_pct >= 0 else ""
    lines.append(
        f"Roth {fmt_k(roth_total)}  ({delta_str} / {ret_sign}{day_return_pct:.2f}%)  ·  "
        f"{deployed_pct}% deployed"
    )

    if benchmark_returns:
        bench_parts: list[str] = []
        for sym in ("QQQ", "IWM", "SPY"):
            br = benchmark_returns.get(sym)
            if br is not None:
                bench_parts.append(f"{sym} {'+' if br >= 0 else ''}{br:.2f}%")
        if bench_parts:
            lines.append(f"Benchmark:  {' · '.join(bench_parts)}")
        qqq = benchmark_returns.get("QQQ")
        if qqq is not None:
            excess = day_return_pct - qqq
            lines.append(f"vs QQQ: {'+' if excess >= 0 else ''}{excess:.2f}%")
    lines.append("")

    # Attribution — position level (dollar contribution)
    lines.append("*ATTRIBUTION*")
    sorted_by_delta = sorted(position_dollar_deltas.items(), key=lambda x: x[1])
    detractors  = [(t, d) for t, d in sorted_by_delta if d < -5][:3]
    contributors = [(t, d) for t, d in reversed(sorted_by_delta) if d > 5][:3]

    if detractors:
        lines.append("Detractors:")
        for t, d in detractors:
            share = f"  ({abs(d) / abs(day_delta) * 100:.0f}% of loss)" if day_delta < 0 else ""
            lines.append(f"  {t}  ${d:,.0f}{share}")
    if contributors:
        lines.append("Contributors:")
        for t, d in contributors:
            share = f"  ({d / day_delta * 100:.0f}% of gain)" if day_delta > 0 else ""
            lines.append(f"  {t}  +${d:,.0f}{share}")

    # Theme attribution
    theme_deltas: list[tuple[str, float]] = []
    for theme_name, theme_tickers in THEMES.items():
        td = sum(position_dollar_deltas.get(t, 0.0) for t in theme_tickers)
        if abs(td) >= 10:
            theme_deltas.append((theme_name, td))
    theme_deltas.sort(key=lambda x: x[1])
    if theme_deltas:
        lines.append("By theme:")
        for name, td in theme_deltas:
            lines.append(f"  {name}: {'+' if td >= 0 else ''}${td:,.0f}")
    lines.append("")

    # What the market told us
    lines.append("*WHAT THE MARKET TOLD US*")
    reported_tickers: set[str] = set()
    for item in theme_findings[:2]:
        dd     = item.get("dollar_delta", 0.0)
        dd_str = f" ({'+' if dd >= 0 else ''}${dd:,.0f})" if abs(dd) >= 10 else ""
        lines.append(f"• {item['title']}{dd_str} — {item['detail']}")
        reported_tickers.update(THEMES.get(item["theme"], set()))

    solo_movers: list[tuple[str, float]] = []
    for r in roth_rows:
        prev = prev_value_by_ticker.get(r.ticker)
        cur  = r.market_value or 0
        if prev and prev > 0:
            pct = (cur - prev) / prev * 100
            if abs(pct) >= 1.5 and r.ticker not in reported_tickers:
                solo_movers.append((r.ticker, pct))
    solo_movers.sort(key=lambda x: abs(x[1]), reverse=True)
    for ticker, pct in solo_movers[:2]:
        sign = "+" if pct >= 0 else ""
        in_theme = any(ticker in tks for tks in THEMES.values())
        classification = "possible sector move" if in_theme else "cause unclassified"
        lines.append(f"• {ticker} {sign}{pct:.1f}% — {classification}")

    if not theme_findings and not solo_movers:
        lines.append("• No material moves — portfolio flat")
    lines.append("")

    # Thesis ledger
    lines.append("*THESIS LEDGER*")
    lines.append("Upgrades: None")
    lines.append("Downgrades: None")
    lines.append("_Price action alone does not change fundamental conviction._")
    lines.append("")

    # Risk — theme $ weights, not just position counts
    low_cv_cap   = sum(r.market_value or 0 for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40)
    low_cv_count = sum(1 for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40)
    cash_pct     = int(100 * cash / roth_total) if roth_total else 0

    lines.append("*RISK*")
    for theme_name, theme_tickers in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in theme_tickers]
        if len(in_theme) >= 3:
            tv  = sum(r.market_value or 0 for r in in_theme)
            pct = int(tv / roth_total * 100) if roth_total else 0
            lines.append(f"🔴 {theme_name}: {fmt_k(tv)} / {pct}% across {len(in_theme)} pos")
    if low_cv_cap >= 1500:
        lines.append(f"🔴 Sub-40 conviction: {fmt_k(low_cv_cap)} across {low_cv_count} positions")
    lines.extend(_interpret_cash(cash, roth_total))
    lines.append("")

    # Tomorrow's queue — specific, actionable
    lines.append("*TOMORROW\\'S QUEUE*")
    queue: list[str] = []

    low_cv_by_val = sorted(
        [r for r in roth_rows if conviction_by_ticker.get(r.ticker, 50) < 40],
        key=lambda r: r.market_value or 0, reverse=True,
    )
    if low_cv_by_val:
        t  = low_cv_by_val[0].ticker
        cv = conviction_by_ticker.get(t, 0)
        queue.append(
            f"{t}: draft hold / no-add thesis "
            f"(cv{cv:.0f}, {fmt_k(low_cv_by_val[0].market_value or 0)} at stake)"
        )

    if off_screen:
        queue.append(f"Exit review: {' · '.join(off_screen[:3])} — off screen")

    for theme_name, theme_tickers in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in theme_tickers]
        if len(in_theme) >= 3:
            cvs    = [conviction_by_ticker.get(r.ticker, 0) for r in in_theme]
            spread = max(cvs) - min(cvs)
            if spread >= 15:
                queue.append(
                    f"{theme_name}: rank by thesis quality "
                    f"(conviction spread {min(cvs):.0f}–{max(cvs):.0f})"
                )
                break

    if not queue:
        queue.append("No urgent items — monitor thesis triggers")

    for i, q in enumerate(queue[:3], 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    lines.append(f"_Next brief: {next_run} ET_")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i:i + 4000])


# ── Main alert dispatcher ─────────────────────────────────────────────────────

def send_run_alert(brief: dict) -> None:
    """Send Telegram push — exactly 3 messages per day: Morning, Midday, Close."""
    import requests

    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return

    from sqlalchemy import desc

    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.models import PositionSnapshot, ThesisLedger

    screen = brief.get("screen", {})
    now    = datetime.now()
    _, slot_label, next_run, _ = _slot_meta(now)

    # Load the two most recent snapshot batches
    with SessionLocal() as session:
        recent_times = (
            session.query(PositionSnapshot.snapshot_at)
            .filter(PositionSnapshot.position_closed == False)  # noqa: E712
            .distinct()
            .order_by(desc(PositionSnapshot.snapshot_at))
            .limit(2)
            .all()
        )
        times     = [r[0] for r in recent_times]
        latest_at = times[0] if times else None
        prev_at   = times[1] if len(times) > 1 else None

        def _load_snaps(at):
            if not at:
                return []
            return (
                session.query(PositionSnapshot)
                .filter(
                    PositionSnapshot.snapshot_at == at,
                    PositionSnapshot.position_closed == False,  # noqa: E712
                )
                .all()
            )

        snap_rows = _load_snaps(latest_at)
        prev_rows = _load_snaps(prev_at)
        prev_value_by_ticker = {r.ticker: r.market_value or 0 for r in prev_rows}

        conviction_by_ticker: dict[str, float] = {}
        for row in snap_rows:
            thesis = (
                session.query(ThesisLedger)
                .filter(ThesisLedger.ticker == row.ticker)
                .order_by(desc(ThesisLedger.thesis_date))
                .first()
            )
            if thesis and thesis.conviction is not None:
                conviction_by_ticker[row.ticker] = thesis.conviction

    # Cash from Schwab account summary
    cash = 0.0
    try:
        from maverick_mcp.api.routers.schwab import _load_schwab
        from maverick_mcp.providers.schwab.client import SchwabClient
        from maverick_mcp.providers.schwab.sync import summarize_accounts

        cfg, store = _load_schwab()
        client     = SchwabClient(cfg, store)
        summaries  = summarize_accounts(client.accounts())
        for s in summaries:
            if s.account_type == "CASH":
                cash = float(s.cash_balance or 0)
    except Exception:
        pass

    # Split Roth / long-term account
    roth_rows  = [r for r in snap_rows if r.ticker not in TRADITIONAL_TICKERS]
    roth_equity = sum(r.market_value or 0 for r in roth_rows)
    roth_total  = roth_equity + cash

    roth_tickers = {r.ticker for r in roth_rows}
    off_screen   = [t for t in (screen.get("held_off_screen") or []) if t not in TRADITIONAL_TICKERS]

    # ── Single source of truth: P&L attribution ───────────────────────────────
    # Computed once here; all slot builders receive it — no independent recalculation.
    position_dollar_deltas: dict[str, float] = {}
    for r in roth_rows:
        prev = prev_value_by_ticker.get(r.ticker)
        if prev is not None:
            position_dollar_deltas[r.ticker] = (r.market_value or 0) - prev
    day_delta = sum(position_dollar_deltas.values())

    # Theme analysis computed once and shared
    theme_findings = _theme_analysis(roth_rows, prev_value_by_ticker, roth_total)

    # Benchmark only at close — markets are settled, data is final
    benchmark_returns: dict[str, float | None] = {}
    if slot_label == "Close":
        benchmark_returns = _fetch_benchmark_return()

    def fmt_k(val: float) -> str:
        return f"${val / 1000:.1f}k" if val >= 1000 else f"${val:.0f}"

    def _tg(msg: str) -> None:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
            if not resp.ok:
                logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Telegram error: %s", exc)

    common = dict(
        tg=_tg,
        roth_rows=roth_rows,
        conviction_by_ticker=conviction_by_ticker,
        prev_value_by_ticker=prev_value_by_ticker,
        position_dollar_deltas=position_dollar_deltas,
        theme_findings=theme_findings,
        day_delta=day_delta,
        cash=cash,
        roth_equity=roth_equity,
        roth_total=roth_total,
        off_screen=off_screen,
        benchmark_returns=benchmark_returns,
        fmt_k=fmt_k,
        now=now,
        next_run=next_run,
    )

    if slot_label == "Morning":
        _send_morning_command_brief(**common)
        logger.info("Morning brief sent (1 message)")

    elif slot_label == "Midday":
        _send_midday_exception_report(**common)
        logger.info("Midday exception report sent")

    elif slot_label == "Close":
        _send_close_decision_memo(**common)
        logger.info("Close decision memo sent (1 message)")

    else:
        logger.warning("Unknown slot_label '%s' — no message sent", slot_label)


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
