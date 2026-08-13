# Repo Independence Plan

Status: proposal. Nothing in this document has been executed.

## Goal

Turn this repository into your own project — with its own name, identity, and
release cadence — without losing any of the ~6,200 lines of investment system
you have built on top of it, and without giving up the ability to pull fixes
and features from upstream MaverickMCP.

## What you actually own

Verified by walking every non-dependency commit in this repo's history and
checking file authorship. This is your work, not upstream's:

| Area | Files | LOC | Notes |
| --- | --- | --- | --- |
| `maverick_mcp/vc_loop/` | 10 | 2,405 | Whole package. Conviction scoring, calibration, decision layer, Dalio diversification, thesis ledger, Obsidian bridge, orchestrator. |
| `maverick_mcp/api/routers/investment_ops.py` | 1 | 919 | Rebalance engine, growth screener, brief tooling. |
| `maverick_mcp/api/routers/schwab.py` | 1 | 238 | Read-only Schwab portfolio surface. |
| `maverick_mcp/api/routers/vc_loop.py` | 1 | 113 | VC loop MCP tools. |
| `maverick_mcp/providers/schwab/` | 4 | ~600 | OAuth, client, mapper, sync. |
| `scripts/` | 10 | ~1,400 | Daily intelligence run, intraday watch, Telegram briefs, launchd scheduling, Schwab auth/reconnect, decision-layer backtest. |
| `alembic/versions/` | 4 | — | brief_snapshots, position_snapshots, learned_weights, diversification. |
| `.claude/commands/` | 8 | — | brief, screen, rebalance, diff, brain-status, investment-brain, obsidian-brain, vc-stack. |

Four database tables are yours and — importantly — they are all defined in
**your own file**, `maverick_mcp/vc_loop/models.py`, not mixed into upstream's
models:

- `vc_thesis_ledger`
- `brief_snapshots`
- `position_snapshots`
- `learned_weights`

Not yours, despite living nearby: `api/routers/performance.py`,
`api/routers/screening_pipeline.py`, and `providers/adanos_sentiment.py` all
arrived from upstream.

## The key finding: the seam is small

The raw import count looks alarming — 16 references from your code into
`maverick_mcp.data.models`. In practice those collapse to almost nothing:

- `SessionLocal` — 8 uses. A database session factory.
- `Base` — 1 use. The SQLAlchemy declarative base.
- `Stock`, `PriceCache` — 1 use each. Read-only market data.

Everything else your code reaches for is a handful of function calls into
upstream routers and providers: `routers.screening`, `routers.technical`,
`routers.portfolio`, `providers.tiingo_data`, `providers.stock_data`, plus
`config.settings` and `validation.base` for plumbing.

That is roughly **ten symbols**. Your investment logic is not entangled with
MaverickMCP's internals; it sits on top of them through a narrow, stable
surface. That is what makes independence cheap.

## Recommended architecture

Do **not** convert the relationship into MCP-server-to-MCP-server calls. Your
code calls upstream Python functions directly; routing those through MCP would
add network round-trips and lose typed returns for no benefit. MCP is the right
boundary between *you and a client*, not between your own modules.

Instead, introduce one adapter module — the only file in your project allowed
to say `import maverick_mcp`:

```text
your code  ──►  cio/ports.py  ──►  maverick_mcp.*
                (~5 functions)
```

Five port functions cover the entire surface:

| Port | Wraps |
| --- | --- |
| `session()` | `data.models.SessionLocal` |
| `prices(ticker, start, end)` | `providers.tiingo_data`, `providers.stock_data` |
| `screen(kind)` | `api.routers.screening` |
| `technicals(ticker)` | `api.routers.technical` |
| `positions()` | `api.routers.portfolio` |

Once that file exists, MaverickMCP becomes swappable. You can vendor it, pin it
as a git dependency, replace a port with your own implementation, or drop
upstream entirely for one capability — all without touching your business
logic.

## Target skeleton

`cio` is a placeholder drawn from your own framing ("You are the CIO"). Swap the
token; the structure is the point.

```text
your-repo/
├─ cio/                          # everything that is yours
│  ├─ __init__.py
│  ├─ ports.py                   # ◄── the ONLY file importing maverick_mcp
│  ├─ models.py                  # your 4 tables (moved from vc_loop/models.py)
│  ├─ conviction/                # scorer, calibration, candidates, decision
│  ├─ portfolio/                 # diversification, rebalance, ledger
│  ├─ memory/                    # obsidian bridge, brief snapshots, history
│  ├─ brokers/                   # schwab auth, client, mapper, sync
│  ├─ briefs/                    # telegram rendering, slot scheduling
│  └─ mcp/                       # your MCP tool surface
│     ├─ investment_ops.py
│     └─ vc_loop.py
├─ vendor/maverick_mcp/          # upstream, pulled — never hand-edited
├─ migrations/                   # your 4 alembic revisions
├─ scripts/                      # daily run, watch, launchd
├─ commands/                     # your 8 slash commands
├─ docs/
└─ pyproject.toml                # name = "cio"
```

The rule that keeps this healthy: **`vendor/maverick_mcp/` is read-only.** Any
change you need there becomes either a port in `cio/ports.py` or a patch you
send upstream. The moment you hand-edit vendored code, merges start hurting
again.

## Migration stages

### Stage 0 — Make it impossible to lose work

```bash
git tag pre-independence
git push -u origin pre-independence
git remote add upstream https://github.com/wshobson/maverick-mcp
git fetch upstream
```

The tag is your rollback point. The `upstream` remote is how you keep pulling
Maverick going forward — `git fetch upstream && git log upstream/main` shows
you what is new, and you cherry-pick what you want.

### Stage 1 — Claim the repo

No code moves. Identity only.

- `pyproject.toml`: rename the project, keep `maverick_mcp` as an internal package for now.
- `README.md`: your project, with a clear "built on MaverickMCP" credit.
- `AGENTS.md` / `CLAUDE.md`: describe your system, not upstream's.
- Keep the LICENSE and attribution — this is a fork, and the license travels with it.

Reversible, zero risk, and after this the repo reads as yours.

### Stage 2 — Draw the seam

The real work, and it is mostly mechanical.

1. Create `cio/ports.py` with the five functions above.
2. Rewrite your code's imports to go through it. Roughly 20 import sites.
3. Move `vc_loop/` → `cio/`, split into the subpackages in the skeleton.
4. Move your three routers to `cio/mcp/`.
5. Move `providers/schwab/` → `cio/brokers/`.
6. Point migrations at `cio.models`.

Table names do not change, so **your database keeps working untouched**. This
is the single most important property of the plan: no data migration, no
re-sync, no lost history.

Run `npx gitnexus analyze` after this stage — the graph will be substantially
different.

### Stage 3 — Optional, later

Only worth doing if upstream diverges enough to be annoying:

```toml
dependencies = ["maverick-mcp @ git+https://github.com/wshobson/maverick-mcp@<sha>"]
```

Then delete `vendor/`. Because Stage 2 already isolated the seam, this is a
dependency change and nothing more.

## What this buys you

- Your project has its own name, docs, and roadmap.
- Upstream stays available: `git fetch upstream`, cherry-pick, done.
- The blast radius of an upstream change is one file, `cio/ports.py`.
- Your database, schedules, and Obsidian vault keep working across the move.
- Nothing is deleted at any stage; Stage 0 makes every step reversible.

## Open questions for you

1. **Name.** `cio` is a placeholder. What is this project actually called?
2. **Vendor or depend?** Stage 2 (vendored, editable) or straight to Stage 3
   (pinned dependency)? Vendoring is friendlier while you are still changing
   things quickly.
3. **Trim upstream?** You are carrying backtesting, deep research agents,
   langgraph supervisors, and a large dependency tree you may not use. Worth
   auditing what you actually call before vendoring all of it.
