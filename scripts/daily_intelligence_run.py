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
            for row in (
                session.query(PortfolioPosition)
                .filter_by(portfolio_id=portfolio.id)
                .all()
            ):
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
                "last_conviction": last_snap.conviction_at_snapshot
                if last_snap
                else None,
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
        limit=30,  # screener candidates (niche growth universe)
        top_n=30,  # enrich ALL of them (small universe, worth the cost)
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
        weights = {
            p["ticker"].upper(): p["market_value"] / total_value for p in positions
        }
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

# Roth position themes — used for correlated-move detection and the sector heat
# block. Every held Roth name should map to exactly one theme so nothing is
# orphaned out of the heat map (XYZ/GLXY → Fintech, NBIS → AI/Compute).
THEMES: dict[str, set[str]] = {
    "Space": {"RDW", "PL", "LUNR", "ASTS", "RKLB"},
    "Nuclear/Materials": {"UUUU", "USAR", "UEC", "MP", "KRKNF"},
    "Quantum": {"IONQ", "RGTI"},
    "Energy Storage": {"ENVX", "EOSE"},
    "Drones/Autonomy": {"ONDS", "RCAT", "JOBY"},
    "Fintech": {"XYZ", "GLXY"},
    "Precision Tech": {"CGNX"},
    "AI/Compute": {"NBIS"},
}

# Fixed emoji grammar for sector identity (never reused for risk/direction) +
# the short label shown in the compact heat block.
SECTOR_META: dict[str, tuple[str, str]] = {
    "Space": ("🛰️", "Space"),
    "Nuclear/Materials": ("☢️", "Nuclear"),
    "Quantum": ("⚛️", "Quantum"),
    "Energy Storage": ("🔋", "Storage"),
    "Drones/Autonomy": ("🚁", "Drones"),
    "Fintech": ("🏦", "Fintech"),
    "Precision Tech": ("🤖", "Vision"),
    "AI/Compute": ("🧠", "AI"),
}


def _load_watch_tickers() -> set[str]:
    """Symbols on the 'Radar' watchlist (managed via scripts/watch.py).

    Any of these held as tiny "buy more later" reminder-shares are surfaced on
    their own "watching to enter" line and kept out of the sector heat block.
    No ticker is hardcoded — add/remove with `make watch-add SYM=...`. Falls
    back to an empty set if the watchlist can't be read, so a held name simply
    appears normally rather than crashing the brief.
    """
    try:
        import sqlite3

        con = sqlite3.connect("maverick_mcp.db")
        cur = con.cursor()
        cur.execute(
            "SELECT wi.symbol FROM watchlist_items wi "
            "JOIN watchlists w ON w.id = wi.watchlist_id WHERE w.name = 'Radar'"
        )
        syms = {r[0].upper() for r in cur.fetchall()}
        con.close()
        return syms
    except Exception:  # noqa: BLE001
        return set()


# Names being watched for an entry (from the Radar watchlist).
WATCH_TICKERS = _load_watch_tickers()

CASH_TARGET = (0.10, 0.15)  # 10–15% is the target band
CASH_FLOOR = 0.10  # never deploy below this


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


def _slot_meta(now: datetime) -> tuple[str | None, str, str, str]:
    """Return (slot_label|None, emoji, next_run_str, context).

    slot_label is None for data-collection-only runs (no Telegram).
    Send windows are ±25 minutes around each designated slot time so that
    the 9:45 and 14:30 launchd runs collect snapshots silently without
    sending duplicate Morning / premature Close messages.
    """
    SEND_SLOTS = [
        (8, 0, "Morning", "🌅"),
        (12, 30, "Midday", "☀️"),
        (16, 15, "Close", "🌙"),
    ]
    CONTEXTS = [
        "Command brief — prepare for the day",
        "Exception report — material changes only",
        "Decision memo — what we learned, what's next",
    ]
    WINDOW = 25  # minutes either side

    total = now.hour * 60 + now.minute
    for i, (h, m, label, emoji) in enumerate(SEND_SLOTS):
        if abs(total - (h * 60 + m)) <= WINDOW:
            next_i = i + 1
            if next_i < len(SEND_SLOTS):
                nh, nm_next = SEND_SLOTS[next_i][:2]
                suffix = "am" if nh < 12 else "pm"
                disp_h = nh if nh <= 12 else nh - 12
                next_str = f"{disp_h}:{nm_next:02d}{suffix}"
            else:
                next_str = "8:00am tomorrow"
            return label, emoji, next_str, CONTEXTS[i]

    return None, "", "8:00am tomorrow", ""  # data-only run — no Telegram


# ── Sector heat + edge helpers (shared across slots) ───────────────────────────


def _fmt_k(val: float) -> str:
    """$X.Xk above 1k, $X below."""
    return f"${val / 1000:.1f}k" if abs(val) >= 1000 else f"${val:.0f}"


def _fmt_delta(d: float) -> str:
    """Signed dollar delta: +$180 / -$527."""
    return f"+${abs(d):,.0f}" if d >= 0 else f"-${abs(d):,.0f}"


def _arrow(pct: float | None, dead: float = 0.15) -> str:
    """Direction glyph: ▲ up / ▼ down / ▬ flat (|pct| < dead)."""
    if pct is None or abs(pct) < dead:
        return "▬"
    return "▲" if pct > 0 else "▼"


def _regime(qqq: float | None) -> str:
    """One-line market posture from QQQ day move."""
    if qqq is None:
        return "◽ Regime n/a"
    if qqq <= -0.5:
        return f"⚠️ Risk-Off · QQQ {qqq:.1f}%"
    if qqq >= 0.5:
        return f"✅ Risk-On · QQQ +{qqq:.1f}%"
    return f"◽ Neutral · QQQ {qqq:+.1f}%"


def _cur_val(r, live_val_by_ticker: dict[str, float]) -> float:
    """Live market value where available, else the snapshot value."""
    return live_val_by_ticker.get(r.ticker, r.market_value or 0)


def _sector_heat_rows(
    roth_rows: list,
    cur_val_by_ticker: dict[str, float],
    base_val_by_ticker: dict[str, float],
    roth_total: float,
) -> list[tuple]:
    """One (emoji, label, $val, %roth, move%) per held sector, sorted by $ desc."""
    rows = []
    for theme_name, tks in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in tks]
        if not in_theme:
            continue
        emoji, label = SECTOR_META[theme_name]
        cur = sum(
            cur_val_by_ticker.get(r.ticker, r.market_value or 0) for r in in_theme
        )
        base = sum(base_val_by_ticker.get(r.ticker, 0) for r in in_theme)
        pct = (cur / roth_total * 100) if roth_total > 0 else 0.0
        move = ((cur - base) / base * 100) if base > 0 else None
        rows.append((emoji, label, cur, pct, move))
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows


def _render_heat(rows: list[tuple], mark_weakest: bool = False) -> list[str]:
    """Format sector heat rows into aligned compact lines."""
    if not rows:
        return ["  (no mapped sector exposure)"]
    weakest_idx = None
    if mark_weakest:
        moved = [(i, r[4]) for i, r in enumerate(rows) if r[4] is not None]
        if moved:
            weakest_idx = min(moved, key=lambda x: x[1])[0]
    out = []
    for i, (emoji, label, cur, pct, move) in enumerate(rows):
        val_str = _fmt_k(cur)
        arrow = _arrow(move)
        move_str = "▬" if (move is None or arrow == "▬") else f"{arrow}{abs(move):.1f}%"
        line = f"{emoji} {label:<7} {val_str:>5} {pct:>2.0f}% {move_str}"
        if i == weakest_idx:
            line += " ← weakest"
        out.append(line)
    return out


def _dry_powder(cash: float, roth_total: float) -> tuple[float, float]:
    """(above_floor, above_target) deployable dollars."""
    if roth_total <= 0:
        return 0.0, 0.0
    above_floor = max(0.0, cash - roth_total * CASH_FLOOR)
    above_target = max(0.0, cash - roth_total * CASH_TARGET[1])
    return above_floor, above_target


def _trim_candidates(
    roth_rows: list,
    conviction_by_ticker: dict[str, float],
    cur_val_by_ticker: dict[str, float],
    n: int = 2,
) -> list[tuple[str, float, float]]:
    """Lowest-conviction names holding real capital → (ticker, cv, $val)."""
    cands = [
        (
            r.ticker,
            conviction_by_ticker.get(r.ticker, 50),
            _cur_val(r, cur_val_by_ticker),
        )
        for r in roth_rows
        if r.ticker not in WATCH_TICKERS
        and conviction_by_ticker.get(r.ticker, 50) < 40
        and _cur_val(r, cur_val_by_ticker) >= 200
    ]
    cands.sort(key=lambda x: (x[1], -x[2]))  # lowest cv, then largest $
    return cands[:n]


def _deploy_targets(
    roth_rows: list,
    conviction_by_ticker: dict[str, float],
    cur_val_by_ticker: dict[str, float],
    n: int = 4,
    cv_floor: float = 55.0,
) -> list[str]:
    """High-conviction underweights (below equal-weight) to add into, cv desc."""
    live = [r for r in roth_rows if r.ticker not in WATCH_TICKERS]
    if not live:
        return []
    eq_weight = sum(_cur_val(r, cur_val_by_ticker) for r in live) / len(live)
    cands = [
        r
        for r in live
        if conviction_by_ticker.get(r.ticker, 0) >= cv_floor
        and _cur_val(r, cur_val_by_ticker) < eq_weight
    ]
    cands.sort(key=lambda r: conviction_by_ticker.get(r.ticker, 0), reverse=True)
    return [r.ticker for r in cands[:n]]


def _watch_line(roth_rows: list) -> str | None:
    """`🎯 Watching to enter: SPCX` for any held reminder-share tickers."""
    held = [r.ticker for r in roth_rows if r.ticker in WATCH_TICKERS]
    if not held:
        return None
    return "🎯 Watching to enter: " + " ".join(sorted(held))


def _low_cv_summary(
    roth_rows: list,
    conviction_by_ticker: dict[str, float],
    cur_val_by_ticker: dict[str, float],
    roth_equity: float,
) -> str | None:
    """`🔴 $4.0k (35%) in N sub-40 names — capital≠conviction` or None."""
    names = [
        r
        for r in roth_rows
        if r.ticker not in WATCH_TICKERS and conviction_by_ticker.get(r.ticker, 50) < 40
    ]
    cap = sum(_cur_val(r, cur_val_by_ticker) for r in names)
    if cap < 1000 or not names:
        return None
    pct = int(cap / roth_equity * 100) if roth_equity > 0 else 0
    return (
        f"🔴 {_fmt_k(cap)} ({pct}%) in {len(names)} sub-40 names — capital≠conviction"
    )


# ── Morning slot (8am) ────────────────────────────────────────────────────────


def _send_morning_command_brief(
    *,
    tg,
    roth_rows: list,
    conviction_by_ticker: dict,
    prior_close_value_by_ticker: dict,
    day_delta: float,
    has_prior_close: bool,
    cash: float,
    cash_valid: bool,
    roth_equity: float,
    roth_total: float,
    off_screen: list,
    benchmark_returns: dict,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Command brief: posture, full sector heat, decisive edge with dollars."""
    day = now.strftime("%a %b %d")
    run_time = now.strftime("%-I:%M%p").lower().lstrip("0")
    deployed_pct = int(100 * roth_equity / roth_total) if roth_total else 100
    qqq = benchmark_returns.get("QQQ") if benchmark_returns else None

    lines = [f"🌅 MAVERICK · {day} · {run_time}"]
    lines.append(f"{_regime(qqq)} · book {_fmt_k(roth_total)}")

    # Account line
    if cash_valid:
        cash_pct = int(cash / roth_total * 100) if roth_total else 0
        lines.append(
            f"💰 ROTH {_fmt_k(roth_equity)} inv · {_fmt_k(cash)} cash "
            f"({cash_pct}%) · {deployed_pct}% deployed"
        )
    else:
        lines.append(
            f"💰 ROTH {_fmt_k(roth_equity)} inv · cash n/a · {len(roth_rows)} pos"
        )

    # Prior-session P&L vs QQQ
    if has_prior_close:
        prior_value = roth_equity - day_delta
        ret = (day_delta / prior_value * 100) if prior_value > 0 else 0.0
        line = f"   Prior {_arrow(ret)}{_fmt_delta(day_delta)} ({ret:+.1f}%)"
        if qqq is not None:
            excess = ret - qqq
            flag = "🟢" if excess >= 0 else "🔴"
            verb = "beat" if excess >= 0 else "lag"
            line += f" · {verb} QQQ {excess:+.1f}% {flag}"
        lines.append(line)

    # Dry powder bands
    if cash_valid:
        above_floor, above_target = _dry_powder(cash, roth_total)
        if above_floor > 0:
            lines.append(
                f"   Dry powder: {_fmt_k(above_floor)} >floor · "
                f"{_fmt_k(above_target)} >target"
            )
        else:
            lines.append("   Cash below floor — no deployment")

    # Full sector heat block (prior-close baseline = overnight move)
    lines.append("🔥 SECTORS")
    heat = _sector_heat_rows(roth_rows, {}, prior_close_value_by_ticker, roth_total)
    lines.extend(_render_heat(heat, mark_weakest=has_prior_close))

    # Edge — decisive, dollar-backed
    lines.append("🎯 EDGE")
    low_cv = _low_cv_summary(roth_rows, conviction_by_ticker, {}, roth_equity)
    if low_cv:
        lines.append(low_cv)
        trims = _trim_candidates(roth_rows, conviction_by_ticker, {})
        if trims:
            trim_str = " · ".join(f"{t} cv{cv:.0f}·{_fmt_k(v)}" for t, cv, v in trims)
            lines.append(f"   Trim: {trim_str}")

    if off_screen:
        lines.append(f"🔴 Off-screen exit review: {' · '.join(off_screen[:3])}")

    if cash_valid:
        above_floor, _ = _dry_powder(cash, roth_total)
        targets = _deploy_targets(roth_rows, conviction_by_ticker, {})
        if above_floor >= 250 and targets:
            lines.append(
                f"🟢 Deploy {_fmt_k(above_floor)} → cv55+ underweights: "
                + "·".join(targets)
            )

    watch = _watch_line(roth_rows)
    if watch:
        lines.append(watch)

    lines.append(f"{next_run} ›")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i : i + 4000])


# ── Midday slot (12:30pm) ─────────────────────────────────────────────────────


def _send_midday_exception_report(
    *,
    tg,
    roth_rows: list,
    prev_value_by_ticker: dict,
    live_val_by_ticker: dict,
    live_fresh: bool,
    conviction_by_ticker: dict,
    roth_equity: float,
    roth_total: float,
    benchmark_returns: dict,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Exception report: what moved since morning, off live prices when available."""
    run_time = now.strftime("%-I:%M%p").lower().lstrip("0")
    qqq = benchmark_returns.get("QQQ") if benchmark_returns else None

    # Live Roth equity + since-morning delta (live where fresh, else snapshot).
    live_equity = sum(
        _cur_val(r, live_val_by_ticker)
        for r in roth_rows
        if r.ticker not in WATCH_TICKERS
    )
    am_equity = sum(
        prev_value_by_ticker.get(r.ticker, _cur_val(r, live_val_by_ticker))
        for r in roth_rows
        if r.ticker not in WATCH_TICKERS
    )
    since_am = live_equity - am_equity

    # Per-ticker moves since morning.
    movers: list[tuple[str, float, float]] = []  # (ticker, pct, $delta)
    for r in roth_rows:
        if r.ticker in WATCH_TICKERS:
            continue
        prev = prev_value_by_ticker.get(r.ticker)
        cur = _cur_val(r, live_val_by_ticker)
        if prev and prev > 0:
            pct = (cur - prev) / prev * 100
            if abs(pct) >= 3.0:
                movers.append((r.ticker, pct, cur - prev))
    movers.sort(key=lambda x: abs(x[1]), reverse=True)

    # Theme baskets that moved as one unit since morning.
    baskets: list[tuple[str, float, float]] = []  # (theme, avg%, $delta)
    covered: set[str] = set()
    for theme_name, tks in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in tks]
        pcts, dd = [], 0.0
        for r in in_theme:
            prev = prev_value_by_ticker.get(r.ticker)
            cur = _cur_val(r, live_val_by_ticker)
            if prev and prev > 0:
                pcts.append((cur - prev) / prev * 100)
                dd += cur - prev
        if len(pcts) >= 3 and (
            all(p > 1.0 for p in pcts) or all(p < -1.0 for p in pcts)
        ):
            baskets.append((theme_name, sum(pcts) / len(pcts), dd))
            covered.update(tks)
    baskets.sort(key=lambda x: abs(x[1]), reverse=True)

    src = "·live" if live_fresh else "·snapshot"
    lines = [f"☀️ MAVERICK MIDDAY · {run_time} {src}"]
    qqq_str = f" · QQQ {qqq:+.1f}%" if qqq is not None else ""
    lines.append(
        f"📊 Roth {_fmt_k(live_equity)} · {_arrow(since_am if am_equity else None)}"
        f"{_fmt_delta(since_am)} since AM{qqq_str}"
    )

    if not movers and not baskets:
        lines.append("✓ No material exceptions since morning.")
        lines.append("Watching: theme correlation · moves >3% · thesis triggers.")
        lines.append(f"{next_run} ›")
        text = "\n".join(lines)
        for i in range(0, len(text), 4000):
            tg(text[i : i + 4000])
        return

    lines.append("— material changes —")
    for theme_name, avg, dd in baskets[:3]:
        emoji, label = SECTOR_META[theme_name]
        flag = "🔴" if avg < 0 else "🟢"
        word = "all red" if avg < 0 else "all green"
        names = " ".join(sorted(THEMES[theme_name]))
        rel = f" (vs QQQ {avg - qqq:+.1f}%)" if qqq is not None else ""
        lines.append(
            f"{flag} {emoji} {label} basket {word} {_arrow(avg)}{abs(avg):.1f}% "
            f"({_fmt_delta(dd)}){rel} · {names}"
        )

    for ticker, pct, dd in movers:
        if ticker in covered:
            continue
        flag = "🔴" if pct < 0 else "🟢"
        in_theme = any(ticker in tks for tks in THEMES.values())
        why = "sector move — check basket" if in_theme else "off-theme — check news"
        lines.append(
            f"{flag} {ticker} {_arrow(pct)}{abs(pct):.1f}% ({_fmt_delta(dd)}) — {why}"
        )

    lines.append("No trade without news/volume confirmation.")
    lines.append(f"{next_run} ›")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i : i + 4000])


# ── Close slot (4:15pm) ───────────────────────────────────────────────────────


def _send_close_decision_memo(
    *,
    tg,
    roth_rows: list,
    conviction_by_ticker: dict,
    prior_close_value_by_ticker: dict,
    live_val_by_ticker: dict,
    live_fresh: bool,
    has_prior_close: bool,
    roth_equity: float,
    roth_total: float,
    cash: float,
    cash_valid: bool,
    off_screen: list,
    benchmark_returns: dict,
    now: datetime,
    next_run: str,
    **_,
) -> None:
    """Decision memo: P&L, sector heat, attribution, decisive edge for tomorrow."""
    day = now.strftime("%a %b %d")
    run_time = now.strftime("%-I:%M%p").lower().lstrip("0")
    deployed_pct = int(100 * roth_equity / roth_total) if roth_total else 100
    qqq = benchmark_returns.get("QQQ") if benchmark_returns else None

    # Live-adjusted day deltas vs prior close.
    deltas: dict[str, float] = {}
    for r in roth_rows:
        if r.ticker in WATCH_TICKERS:
            continue
        prev = prior_close_value_by_ticker.get(r.ticker)
        if prev is not None:
            deltas[r.ticker] = _cur_val(r, live_val_by_ticker) - prev
    day_delta = sum(deltas.values())

    lines = [f"🌙 MAVERICK CLOSE · {day} · {run_time}"]

    # Account
    if cash_valid:
        lines.append(
            f"💰 ROTH {_fmt_k(roth_equity)} inv · {_fmt_k(cash)} cash · "
            f"{deployed_pct}% deployed"
        )
    else:
        lines.append(f"💰 ROTH {_fmt_k(roth_equity)} inv · cash n/a")

    # Performance
    if has_prior_close:
        prior_value = roth_equity - day_delta
        ret = (day_delta / prior_value * 100) if prior_value > 0 else 0.0
        line = f"{'📈' if day_delta >= 0 else '📉'} Day {_fmt_delta(day_delta)} ({ret:+.1f}%)"
        if qqq is not None:
            excess = ret - qqq
            flag = "🟢" if excess >= 0 else "🔴"
            line += f" · QQQ {qqq:+.1f}% · vs QQQ {excess:+.1f}% {flag}"
        lines.append(line)
    else:
        lines.append("⚠️ P&L n/a — no prior-close snapshot")

    # Sector heat (full day vs prior close)
    lines.append("🔥 SECTORS")
    heat = _sector_heat_rows(
        roth_rows, live_val_by_ticker, prior_close_value_by_ticker, roth_total
    )
    lines.extend(_render_heat(heat, mark_weakest=has_prior_close))

    # Attribution by theme
    if has_prior_close and deltas:
        theme_deltas: list[tuple[str, float]] = []
        for theme_name, tks in THEMES.items():
            td = sum(deltas.get(t, 0.0) for t in tks)
            if abs(td) >= 10:
                theme_deltas.append((theme_name, td))
        if theme_deltas:
            lines.append("🎯 ATTRIBUTION")
            det = sorted([x for x in theme_deltas if x[1] < 0], key=lambda x: x[1])
            con = sorted([x for x in theme_deltas if x[1] > 0], key=lambda x: -x[1])
            if det:
                s = " · ".join(
                    f"{SECTOR_META[n][1]} {_fmt_delta(d)}" for n, d in det[:3]
                )
                lines.append(f"🔴 Detractors: {s}")
            if con:
                s = " · ".join(
                    f"{SECTOR_META[n][1]} {_fmt_delta(d)}" for n, d in con[:3]
                )
                lines.append(f"🟢 Contributors: {s}")

    # Edge / tomorrow — decisive, dollar-backed
    lines.append("🎯 EDGE / TOMORROW")
    trims = _trim_candidates(roth_rows, conviction_by_ticker, live_val_by_ticker)
    if trims:
        t, cv, v = trims[0]
        lines.append(f"🔴 Trim: {t} cv{cv:.0f}·{_fmt_k(v)} — lowest cv, capital≠score")
    if off_screen:
        lines.append(f"🔴 Off-screen exit review: {' · '.join(off_screen[:3])}")
    if cash_valid:
        above_floor, _ = _dry_powder(cash, roth_total)
        targets = _deploy_targets(roth_rows, conviction_by_ticker, live_val_by_ticker)
        if above_floor >= 250 and targets:
            lines.append(
                f"🟢 Deploy {_fmt_k(above_floor)} → {'·'.join(targets)} "
                f"(cv55+ underweight)"
            )
    # Basket to rank by thesis quality when conviction spread is wide
    for theme_name, tks in THEMES.items():
        in_theme = [r for r in roth_rows if r.ticker in tks]
        if len(in_theme) >= 3:
            cvs = [conviction_by_ticker.get(r.ticker, 0) for r in in_theme]
            if max(cvs) - min(cvs) >= 15:
                lines.append(
                    f"🔴 Rank {SECTOR_META[theme_name][1]} by thesis "
                    f"(cv spread {min(cvs):.0f}–{max(cvs):.0f})"
                )
                break

    watch = _watch_line(roth_rows)
    if watch:
        lines.append(watch)

    lines.append(f"{next_run} ›")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        tg(text[i : i + 4000])


# ── Main alert dispatcher ─────────────────────────────────────────────────────


def _build_brief_data(brief: dict, slot_label: str) -> dict:
    """Load snapshots, cash, live prices and derived metrics for a brief render.

    Returns everything the slot renderers need except ``tg``, ``now`` and
    ``next_run`` (supplied by the caller). Shared by the live Telegram path and
    the ``--preview`` renderer so both show identical numbers.
    """
    from sqlalchemy import desc
    from sqlalchemy import func as sa_func

    from maverick_mcp.data.models import SessionLocal
    from maverick_mcp.vc_loop.models import PositionSnapshot, ThesisLedger

    screen = brief.get("screen", {})

    # ── Load snapshots ─────────────────────────────────────────────────────────
    #   1. latest       — this run (just written)
    #   2. prev_snap     — most recent snapshot before this one (intraday delta)
    #   3. prior_close   — most recent snapshot from a PREVIOUS calendar day (P&L)
    with SessionLocal() as session:

        def _max_snap_at(extra_filters=None):
            q = session.query(sa_func.max(PositionSnapshot.snapshot_at)).filter(
                PositionSnapshot.position_closed == False  # noqa: E712
            )
            for f in extra_filters or []:
                q = q.filter(f)
            return q.scalar()

        latest_at = _max_snap_at()
        prev_snap_at = (
            _max_snap_at([PositionSnapshot.snapshot_at < latest_at])
            if latest_at
            else None
        )
        # Daily-P&L baseline: last snapshot from a calendar day strictly before the
        # latest snapshot. Use sa_func.date(), NOT cast(col, Date): in SQLite
        # CAST(ts AS DATE) has numeric affinity and returns the integer YEAR, so the
        # filter is true for every row and prior_close collapses onto today's snapshot.
        latest_date_str = str(latest_at)[:10] if latest_at else ""
        prior_close_at = (
            _max_snap_at(
                [
                    sa_func.date(PositionSnapshot.snapshot_at) < latest_date_str,
                ]
            )
            if latest_at
            else None
        )

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
        prev_snap_rows = _load_snaps(prev_snap_at)
        prior_close_rows = _load_snaps(prior_close_at)

        prev_value_by_ticker = {r.ticker: r.market_value or 0 for r in prev_snap_rows}
        prior_close_value_by_ticker = {
            r.ticker: r.market_value or 0 for r in prior_close_rows
        }

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

    # Cash from Schwab account summary — sum across ALL accounts (IRA accounts are
    # not typed "CASH"; filtering by type silently drops the balance).
    cash = 0.0
    cash_valid = False
    try:
        from maverick_mcp.api.routers.schwab import _load_schwab
        from maverick_mcp.providers.schwab.client import SchwabClient
        from maverick_mcp.providers.schwab.sync import summarize_accounts

        cfg, store = _load_schwab()
        client = SchwabClient(cfg, store)
        summaries = summarize_accounts(client.accounts())
        cash = sum(float(s.cash_balance or 0) for s in summaries)
        cash_valid = True
    except Exception as exc:
        logger.warning("Cash balance unavailable: %s", exc)

    # Split Roth / long-term account
    roth_rows = [r for r in snap_rows if r.ticker not in TRADITIONAL_TICKERS]
    roth_equity = sum(r.market_value or 0 for r in roth_rows)
    roth_total = roth_equity + cash

    off_screen = [
        t for t in (screen.get("held_off_screen") or []) if t not in TRADITIONAL_TICKERS
    ]
    has_prior_close = bool(prior_close_value_by_ticker)

    day_delta = sum(
        (r.market_value or 0) - prior_close_value_by_ticker[r.ticker]
        for r in roth_rows
        if r.ticker in prior_close_value_by_ticker
    )

    # ── Live intraday prices (Tiingo) — freshness for midday/close ───────────────
    # Morning uses the authoritative Schwab snapshot; midday/close prefer live
    # last-trade prices, falling back gracefully to the snapshot if Tiingo is empty.
    live_val_by_ticker: dict[str, float] = {}
    live_fresh = False
    if slot_label in ("Midday", "Close"):
        try:
            from maverick_mcp.providers.tiingo_data import get_live_prices

            tickers = [r.ticker for r in roth_rows]
            prices = get_live_prices(tickers)
            for r in roth_rows:
                p = prices.get(r.ticker)
                if p and r.shares:
                    live_val_by_ticker[r.ticker] = float(r.shares) * float(p)
            live_fresh = bool(live_val_by_ticker)
        except Exception as exc:
            logger.warning("Live prices unavailable, using snapshot: %s", exc)

    benchmark_returns: dict[str, float | None] = _fetch_benchmark_return()

    return {
        "roth_rows": roth_rows,
        "conviction_by_ticker": conviction_by_ticker,
        "prev_value_by_ticker": prev_value_by_ticker,
        "prior_close_value_by_ticker": prior_close_value_by_ticker,
        "live_val_by_ticker": live_val_by_ticker,
        "live_fresh": live_fresh,
        "day_delta": day_delta,
        "has_prior_close": has_prior_close,
        "cash": cash,
        "cash_valid": cash_valid,
        "roth_equity": roth_equity,
        "roth_total": roth_total,
        "off_screen": off_screen,
        "benchmark_returns": benchmark_returns,
    }


_SLOT_RENDERERS = {
    "Morning": _send_morning_command_brief,
    "Midday": _send_midday_exception_report,
    "Close": _send_close_decision_memo,
}


def send_run_alert(brief: dict) -> None:
    """Send Telegram push — exactly 3 messages per day: Morning, Midday, Close."""
    import requests

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return

    now = datetime.now()
    slot_label, _, next_run, _ = _slot_meta(now)

    # Data-only runs (9:45, 14:30) — snapshot already saved; nothing to send.
    if slot_label is None:
        logger.info(
            "Data-only run at %s — snapshot saved, no Telegram message",
            now.strftime("%H:%M"),
        )
        return

    def _tg(msg: str) -> None:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
            if not resp.ok:
                logger.warning(
                    "Telegram send failed: %s %s", resp.status_code, resp.text[:200]
                )
        except Exception as exc:
            logger.warning("Telegram error: %s", exc)

    data = _build_brief_data(brief, slot_label)
    _SLOT_RENDERERS[slot_label](tg=_tg, now=now, next_run=next_run, **data)
    logger.info("%s brief sent (1 message)", slot_label)


def render_previews() -> None:
    """Render all 3 briefs to stdout WITHOUT sending Telegram (verification).

    Uses the same _build_brief_data + renderers as the live path, so previews
    match exactly what would be pushed. Reads current DB snapshots; no writes.
    """
    now = datetime.now()
    # Try to reuse the most recent saved brief for off-screen context.
    brief: dict = {}
    try:
        from maverick_mcp.api.routers.investment_ops import _get_screen_sets

        _, _, off_screen = _get_screen_sets()
        held = _get_prev_snapshot_tickers()
        brief = {"screen": {"held_off_screen": sorted(off_screen & held)}}
    except Exception:
        brief = {}

    slots = [
        ("Morning", now.replace(hour=8, minute=1)),
        ("Midday", now.replace(hour=12, minute=31)),
        ("Close", now.replace(hour=16, minute=16)),
    ]

    for slot_label, slot_now in slots:
        _, _, next_run, _ = _slot_meta(slot_now)
        data = _build_brief_data(brief, slot_label)

        captured: list[str] = []
        _SLOT_RENDERERS[slot_label](
            tg=captured.append, now=slot_now, next_run=next_run, **data
        )
        body = "\n".join(captured)
        print(f"\n{'═' * 60}")
        print(
            f"  {slot_label.upper()} PREVIEW  ·  {len(body)} chars  ·  "
            f"{'live' if data['live_fresh'] else 'snapshot'} prices"
        )
        print("═" * 60)
        print(body)
    print(f"\n{'═' * 60}\n(preview only — no Telegram sent)")


async def main() -> None:
    logger.info(
        "=== Maverick daily intelligence run — %s ===",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

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
    if "--preview" in sys.argv:
        render_previews()
    else:
        asyncio.run(main())
