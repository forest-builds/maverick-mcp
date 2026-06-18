# CLAUDE.md

This file is a Claude-specific entry point only. The canonical agent guidance is
in `AGENTS.md`, and durable project documentation is in `docs/`.

## Start Here

- `AGENTS.md`: repository guidelines, commands, MCP transport defaults, and
  safety notes.
- `docs/INDEX.md`: documentation map.
- `docs/CATALOG.md`: status of current, historical, archived, and deleted docs.
- `docs/runbooks/claude-desktop.md`: Claude Desktop setup.

## Claude Desktop Default

Prefer STDIO for Claude Desktop:

```json
{
  "mcpServers": {
    "maverick-mcp": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "maverick_mcp.api.server",
        "--transport",
        "stdio"
      ],
      "cwd": "/path/to/maverick-mcp"
    }
  }
}
```

For bridge or remote workflows, run `make dev` and connect to
`http://localhost:8003/mcp/` with `mcp-remote`. SSE is legacy/debug only.

## Common Commands

```bash
uv sync --extra dev
make dev
make dev-stdio
make test
make lint
make typecheck
make docs-check
npx gitnexus analyze   # reindex after any significant code change
```

Run `npx gitnexus analyze` after adding files, modifying routers/services, or
merging branches. The PostToolUse hook detects staleness after `git commit` but
does not reindex automatically — do it explicitly to keep the graph current.

## Important Constraints

- This is a personal-use educational financial analysis server, not financial
  advice.
- **Local deployment only.** The only valid targets are `make dev-stdio`
  (Claude Desktop STDIO) and `make dev` (local HTTP on port 8003). Never
  introduce hosted/remote/cloud deployment, auth, billing, or multi-tenant
  scope without an explicit new plan.
- Do not reintroduce auth, billing, or hosted SaaS scope without an explicit
  plan.
- Keep documentation changes cataloged in `docs/CATALOG.md`.
- Do not use `.claude/` files as the repository source of truth.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **maverick-mcp** (21266 symbols, 38042 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/maverick-mcp/context` | Codebase overview, check index freshness |
| `gitnexus://repo/maverick-mcp/clusters` | All functional areas |
| `gitnexus://repo/maverick-mcp/processes` | All execution flows |
| `gitnexus://repo/maverick-mcp/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
