#!/usr/bin/env python
"""Event-driven intraday watcher — the "as live as possible" layer.

Runs every ~10 min during market hours (launchd). Pulls live Tiingo quotes
(static key, no Schwab), compares each position to its prior close, and pings
Telegram ONLY when something actually moves — a position >=5%, or the book
>=2%. Silent when quiet, instant when real. De-duped so a name hovering at a
threshold doesn't spam you.

This is deliberately lightweight (no vc_loop scoring, no LLM) so a 10-minute
cron is cheap. The 3 daily briefs remain the structured digests.

Run:   uv run python scripts/intraday_watch.py         (real; sends if triggered)
       uv run python scripts/intraday_watch.py --dry    (print, never send)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from maverick_mcp.providers import tiingo_data as td  # noqa: E402

DB = Path("maverick_mcp.db")
STATE_DIR = Path(".local")
POSITION_ALERT_PCT = 5.0     # per-name intraday move that fires an alert
PORTFOLIO_ALERT_PCT = 2.0    # whole-book move that fires an alert
TRADITIONAL = {"NVDA", "PLTR", "IREN", "OKLO", "COIN", "TSLA"}


def _is_market_hours(now: datetime) -> bool:
    """Weekday 9:30–16:00 ET. Host runs in ET; keep it simple."""
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


def _latest_positions() -> tuple[dict[str, float], dict[str, float]]:
    """Return ({ticker: shares}, {ticker: prior_close_price}) from snapshots."""
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT MAX(snapshot_at) FROM position_snapshots WHERE position_closed=0")
    latest = cur.fetchone()[0]
    if not latest:
        con.close()
        return {}, {}
    cur.execute(
        "SELECT ticker, shares FROM position_snapshots "
        "WHERE snapshot_at=? AND position_closed=0",
        (latest,),
    )
    shares = {t: float(s or 0) for t, s in cur.fetchall()}
    # Prior close = last snapshot on a calendar day before the latest one.
    cur.execute(
        "SELECT MAX(snapshot_at) FROM position_snapshots "
        "WHERE position_closed=0 AND date(snapshot_at) < ?",
        (str(latest)[:10],),
    )
    prior_at = cur.fetchone()[0]
    prior: dict[str, float] = {}
    if prior_at:
        cur.execute(
            "SELECT ticker, market_price FROM position_snapshots "
            "WHERE snapshot_at=? AND position_closed=0",
            (prior_at,),
        )
        prior = {t: float(p) for t, p in cur.fetchall() if p is not None}
    con.close()
    return shares, prior


def _state_path(now: datetime) -> Path:
    return STATE_DIR / f"intraday_state_{now:%Y-%m-%d}.json"


def _load_state(now: datetime) -> dict:
    p = _state_path(now)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"tickers": {}, "portfolio_buckets": []}


def _save_state(now: datetime, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(now).write_text(json.dumps(state))


def _bucket(pct: float, step: float) -> int:
    """Threshold bucket so we re-alert only when a move deepens (5→10→15…)."""
    return int(abs(pct) // step) * int(step)


def _send_telegram(msg: str) -> None:
    import requests

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    dry = "--dry" in sys.argv
    now = datetime.now()
    if not dry and not _is_market_hours(now):
        return 0  # silent no-op outside market hours

    shares, prior = _latest_positions()
    tickers = [t for t in shares if t in prior]
    if not tickers:
        if dry:
            print("no positions with a prior-close baseline")
        return 0

    live = td.get_live_prices(tickers)
    if not live:
        if dry:
            print("no live prices returned")
        return 0

    state = _load_state(now)
    alerts: list[str] = []

    # Per-position moves.
    movers = []
    for t in tickers:
        if t not in live or prior[t] <= 0:
            continue
        pct = (live[t] / prior[t] - 1.0) * 100
        movers.append((t, pct))
        if abs(pct) >= POSITION_ALERT_PCT:
            b = _bucket(pct, POSITION_ALERT_PCT)
            key = f"{t}:{'+' if pct >= 0 else '-'}"
            if state["tickers"].get(key, 0) < b:
                state["tickers"][key] = b
                arrow = "▲" if pct >= 0 else "▼"
                tag = " (long-term)" if t in TRADITIONAL else ""
                alerts.append(f"{arrow} *{t}* {pct:+.1f}% today{tag} · ${live[t]:.2f}")

    # Whole-book move.
    mv_now = sum(shares[t] * live[t] for t in tickers if t in live)
    mv_prior = sum(shares[t] * prior[t] for t in tickers if t in live)
    if mv_prior > 0:
        book_pct = (mv_now / mv_prior - 1.0) * 100
        if abs(book_pct) >= PORTFOLIO_ALERT_PCT:
            b = _bucket(book_pct, PORTFOLIO_ALERT_PCT)
            sign = "+" if book_pct >= 0 else "-"
            marker = f"book:{sign}{b}"
            if marker not in state["portfolio_buckets"]:
                state["portfolio_buckets"].append(marker)
                arrow = "▲" if book_pct >= 0 else "▼"
                alerts.insert(0, f"{arrow} *Book {book_pct:+.1f}%* today (${mv_now/1000:.1f}k)")

    if not alerts:
        if dry:
            top = sorted(movers, key=lambda x: -abs(x[1]))[:5]
            print(f"[{now:%H:%M}] no threshold breaches. biggest: " +
                  ", ".join(f"{t} {p:+.1f}%" for t, p in top))
        return 0

    header = f"⚡ *MAVERICK LIVE* · {now:%-I:%M%p}"
    msg = header + "\n\n" + "\n".join(alerts) + "\n\n_live intraday · Tiingo_"
    if dry:
        print(msg)
    else:
        _send_telegram(msg)
        _save_state(now, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
