# Task queue — maverick-mcp
# Agents read this at session start. Format: - [ ] queued | - [x] done | - [~] in progress
# Keep sorted by priority (top = most urgent). Delete done items weekly.

## Now

- [ ] Decide on a repo-wide `ruff format`/`ruff --fix` cleanup — the new pre-commit hook found 52 files needing changes on first run (tree isn't ruff-clean yet; see docs/vault-context.md)
- [ ] Decide whether to switch `.pre-commit-config.yaml`'s gitleaks hook from `gitleaks` (builds from source, needs a local Go toolchain) to `gitleaks-system` (uses a pre-installed `gitleaks` binary — already on this machine via Homebrew)

## Next

- [ ] Triage the 50-CVE `pip-audit` backlog (see docs/vault-context.md) via Dependabot PRs once past the 7-day cooldown
- [ ] Decide whether to gate `bandit`/`pip-audit` in CI once their backlogs are burned down

## Someday

## Done (clear weekly)

- [x] Revive dead VC-loop scoring features, live Tiingo layer, redesigned Telegram briefs (`3b194b1`)
- [x] Deep `.gitignore` audit — no fixes needed, file is already sound (see `_bmad-output/implementation-artifacts/deferred-work.md`)
