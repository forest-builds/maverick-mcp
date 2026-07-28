#!/usr/bin/env python
"""Watchlist + entry-radar for names you're eyeing but don't hold yet.

Generalizes the old "hold 1 share of SPCX so I don't forget" hack into a real
list. Nothing here is special-cased to any ticker — SPCX is just row 1.

The entry radar answers "is now a decent entry?" for each watched name by
locating today's price inside its recent range (this book trades mean-reverting
small-caps, so pullbacks toward the low end of the range are the setups worth
flagging; names pinned near their highs are 'extended', wait). It's a heuristic
timing aid, not a backtested alpha signal — treat it as "worth a look now",
not "buy".

Usage:
    uv run python scripts/watch.py                 # show the entry radar
    uv run python scripts/watch.py --add RKLB       # add a name (--note "...")
    uv run python scripts/watch.py --remove SPCX    # drop a name
    uv run python scripts/watch.py --list           # just list the watchlist
    (or: make watch / make watch-add SYM=RKLB)
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from maverick_mcp.providers import tiingo_data as td  # noqa: E402

DB = Path("maverick_mcp.db")
DEFAULT_LIST = "Radar"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB)


def _radar_list_id(con: sqlite3.Connection) -> int:
    cur = con.cursor()
    cur.execute("SELECT id FROM watchlists WHERE name=?", (DEFAULT_LIST,))
    row = cur.fetchone()
    if row:
        return row[0]
    now = datetime.now(UTC).isoformat()
    cur.execute(
        "INSERT INTO watchlists (name, description, created_at, updated_at) "
        "VALUES (?,?,?,?)",
        (DEFAULT_LIST, "Names being watched for an entry", now, now),
    )
    con.commit()
    return cur.lastrowid


def _symbols(con: sqlite3.Connection) -> list[tuple[str, str | None]]:
    cur = con.cursor()
    lid = _radar_list_id(con)
    cur.execute(
        "SELECT symbol, notes FROM watchlist_items WHERE watchlist_id=? ORDER BY symbol",
        (lid,),
    )
    return [(s.upper(), n) for s, n in cur.fetchall()]


def add(sym: str, note: str | None) -> None:
    sym = sym.upper().strip()
    con = _conn()
    lid = _radar_list_id(con)
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM watchlist_items WHERE watchlist_id=? AND symbol=?", (lid, sym)
    )
    if cur.fetchone():
        print(f"{sym} already on the {DEFAULT_LIST} watchlist.")
        con.close()
        return
    now = datetime.now(UTC).isoformat()
    cur.execute(
        "INSERT INTO watchlist_items "
        "(watchlist_id, symbol, added_at, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (lid, sym, now, note, now, now),
    )
    con.commit()
    con.close()
    print(
        f"✓ Added {sym} to the {DEFAULT_LIST} watchlist"
        + (f" — {note}" if note else "")
    )


def remove(sym: str) -> None:
    sym = sym.upper().strip()
    con = _conn()
    lid = _radar_list_id(con)
    cur = con.cursor()
    cur.execute(
        "DELETE FROM watchlist_items WHERE watchlist_id=? AND symbol=?", (lid, sym)
    )
    con.commit()
    changed = cur.rowcount
    con.close()
    print(f"✓ Removed {sym}" if changed else f"{sym} was not on the watchlist.")


def _entry_read(sym: str) -> dict | None:
    closes = td.get_daily_closes(sym, lookback_days=60)
    if len(closes) < 10:
        return None
    window = closes[-40:]
    hi, lo = max(window), min(window)
    live = td.get_live_prices([sym]).get(sym) or closes[-1]
    rng = hi - lo
    range_pos = (live - lo) / rng if rng > 0 else 0.5
    from_high = (live / hi - 1) * 100 if hi else 0.0
    return {
        "live": live,
        "range_pos": range_pos,
        "from_high": from_high,
        "hi": hi,
        "lo": lo,
    }


TRADITIONAL = {"NVDA", "TSLA", "PLTR", "IREN", "OKLO", "COIN"}


def _held_roth() -> dict[str, float | None]:
    """Held actionable (non-traditional) tickers → conviction, latest snapshot."""
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT MAX(snapshot_at) FROM position_snapshots WHERE position_closed=0"
    )
    at = cur.fetchone()[0]
    held: dict[str, float | None] = {}
    if at:
        cur.execute(
            "SELECT ticker, conviction_at_snapshot FROM position_snapshots "
            "WHERE snapshot_at=? AND position_closed=0",
            (at,),
        )
        held = {
            t.upper(): cv for t, cv in cur.fetchall() if t.upper() not in TRADITIONAL
        }
    con.close()
    return held


def radar() -> None:
    con = _conn()
    watch = dict(_symbols(con))
    con.close()
    held = _held_roth()

    # The universe you're "always looking" at: every actionable holding (where's
    # a good spot to ADD?) plus every watchlist name (where's a good spot to
    # START?). Watchlist label wins if a name is both (e.g. a reminder-share).
    universe: dict[str, tuple[str, str | None]] = {}
    for sym in held:
        universe[sym] = ("HOLD", None)
    for sym, note in watch.items():
        universe[sym] = ("WATCH", note)

    print("🎯 ENTRY RADAR — where every name sits in its 40d range")
    print(
        "   (heuristic timing for adds/entries, not a buy signal; best setups first)\n"
    )
    if not universe:
        print("  (nothing to scan — add names with:  make watch-add SYM=RKLB)")
        return

    reads = []
    for sym, (kind, note) in universe.items():
        reads.append((sym, kind, note, held.get(sym), _entry_read(sym)))
    reads.sort(key=lambda x: (x[4] is None, x[4]["range_pos"] if x[4] else 1.0))

    for sym, kind, note, cv, r in reads:
        if r is None:
            print(f"  {sym:6} [{kind:5}] — no price data")
            continue
        pos = r["range_pos"]
        if pos < 0.30:
            verdict = "🟢 near lows"
        elif pos < 0.55:
            verdict = "🟡 mid-range"
        else:
            verdict = "🔴 extended"
        bar = "▁▂▃▄▅▆▇█"[min(7, int(pos * 8))]
        cv_s = f" cv{cv:.0f}" if cv is not None else ""
        note_s = f" · {note}" if note else ""
        print(
            f"  {sym:6} [{kind:5}]{cv_s:>6} ${r['live']:>8.2f}  {bar} {pos * 100:>3.0f}% "
            f"({r['from_high']:+.0f}% vs hi)  {verdict}{note_s}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", metavar="SYM")
    ap.add_argument("--remove", metavar="SYM")
    ap.add_argument("--note", default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.add:
        add(args.add, args.note)
    elif args.remove:
        remove(args.remove)
    elif args.list:
        con = _conn()
        for s, n in _symbols(con):
            print(f"  {s}" + (f" — {n}" if n else ""))
        con.close()
    else:
        radar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
