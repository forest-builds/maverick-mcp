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
  `_bmad-output/implementation-artifacts/deferred-work.md`).
- `pip-audit` found 50 known CVEs across 14 dependencies (aiohttp, starlette, mcp,
  langchain, cryptography, pillow, and others), all with fix versions already
  published. Not yet triaged or scheduled.

## Key decisions (last 30 days)

- This repo is actively edited by more than one agent CLI at once (this file exists
  specifically to give them shared session context, since each CLI's private agent
  memory doesn't cross to the others).
- CI treats `bandit`/`pip-audit` as informational (not gated) until their existing
  backlogs (25 findings / 50 CVEs) are triaged — mirrors the existing `ty` typecheck
  pattern in `ci.yml`.

## Blockers / waiting on

- None currently.
