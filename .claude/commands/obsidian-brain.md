# Obsidian Brain

Use this command when coordinating MaverickMCP with the local Obsidian vault and
an optional GBrain memory index.

Architecture:

```text
Obsidian vault (`vc/`)
  -> durable human-readable goals, theses, memos, decisions
GBrain, if installed
  -> Markdown import/sync, retrieval, synthesis, citations, gap analysis
GitNexus
  -> tracked-code graph, execution flows, impact analysis, refactoring context
MaverickMCP
  -> market data, portfolio/risk tools, backtests, research agents
```

Default workflow:

1. Read the relevant vault notes before starting broad research.
2. Use goals and account constraints as the CIO brief.
3. Write new durable memory as Markdown in the vault.
4. If GBrain is available, sync/import the vault and query it for prior context.
5. Use GitNexus for code impact questions, not for private thesis memory.
6. Report what changed and what still needs CIO approval.

Suggested vault structure:

```text
vc/
  00-dashboard/
  01-goals/
  02-accounts/
  03-watchlists/
  04-theses/
  05-research/
  06-ic-memos/
  07-decisions/
  08-reviews/
  09-inbox/
```

Do not store secrets, API keys, brokerage credentials, or account login
artifacts in Obsidian or GBrain.

Because `vc/` is ignored for public-fork safety, agents should read vault notes
directly when asked. GitNexus awareness covers tracked code, not ignored private
vault contents.
