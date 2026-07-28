# Task queue — maverick-mcp
# Agents read this at session start. Format: - [ ] queued | - [x] done | - [~] in progress
# Keep sorted by priority (top = most urgent). Delete done items weekly.

## Now

- [ ] Finish/commit `maverick_mcp/providers/tiingo_data.py` (new Tiingo data provider)
- [ ] Finish/commit `maverick_mcp/vc_loop/decision.py` (new VC loop decision module)
- [ ] Finish/commit `scripts/intraday_watch.py` (new intraday watch script)
- [ ] Finish/commit `scripts/schwab_reconnect.py` (new Schwab reconnect script — Makefile already has a `schwab-reconnect` target pointing at it)
- [ ] Finish/commit `scripts/verify_features.py` (new feature verification script)

## Next

- [ ] Triage the 50-CVE `pip-audit` backlog (see docs/vault-context.md) via Dependabot PRs once past the 7-day cooldown
- [ ] Decide whether to gate `bandit`/`pip-audit` in CI once their backlogs are burned down

## Someday

## Done (clear weekly)
