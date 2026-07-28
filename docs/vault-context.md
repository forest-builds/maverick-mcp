# Vault context — maverick-mcp

<!-- Agents load this at the start of every session (see AGENTS.md).
     Keep it current. Stale context is worse than none.
     Update whenever project state meaningfully changes. -->

## Currently working on

Closing the gaps found by a `/setup` agent-readiness audit: commit-time/CI secret
scanning, gated SAST/dependency-vuln checks, a Dependabot cooling window, and
Claude Code hooks (format-after-edit, protected-path guard). See
`_bmad-output/implementation-artifacts/spec-setup-agent-ready.md`.

## Open hypotheses / unresolved questions

- A deep `.gitignore` audit is still pending (deferred — see
  `_bmad-output/implementation-artifacts/deferred-work.md`). Note:
  `.claude/settings.local.json` was checked in detail during this work and is
  correctly gitignored/untracked — no issue there, despite an earlier (wrong)
  note in this file claiming otherwise.
- `pip-audit` found 50 known CVEs across 14 dependencies (aiohttp, starlette, mcp,
  langchain, cryptography, pillow, and others), all with fix versions already
  published. Not yet triaged or scheduled.
- The repo isn't ruff-clean: `pre-commit run --all-files` wants to touch 52 files
  (45 reformatted, 12 auto-fixed, 5 left with unfixed lint errors). Not applied —
  needs a human decision on whether/when to take that diff.
- `.pre-commit-config.yaml`'s gitleaks hook uses the `gitleaks` id, which builds
  from source and needs a local Go toolchain. `gitleaks-system` (pre-installed
  binary) avoids that but wasn't switched — needs a decision.

## Key decisions (last 30 days)

- This repo is actively edited by more than one agent CLI at once (this file exists
  specifically to give them shared session context, since each CLI's private agent
  memory doesn't cross to the others).
- CI treats `bandit`/`pip-audit` as informational (not gated) until their existing
  backlogs (25 findings / 50 CVEs) are triaged — mirrors the existing `ty` typecheck
  pattern in `ci.yml`.
- Gitleaks' full-history CI scan found 5 pre-existing matches, all confirmed as
  false positives (a content hash, a docs placeholder, and two test fixtures —
  see `.gitleaksignore`, fingerprints only, no secret content). Suppressed there
  so the gate stays hard (no `continue-on-error`) while still catching
  genuinely new secrets.

## Blockers / waiting on

- None currently.
