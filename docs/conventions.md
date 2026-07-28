# Coding conventions — maverick-mcp
# Supplement to Ruff/pyright/ty. Things linters don't catch but reviewers always flag.
# Update this file when a pattern is found in review more than twice; delete lines
# that stop being true.

## Patterns to avoid (recurring regressions)

- **Telegram brief message count/P&L source.** The brief pipeline has regressed on
  "exactly 3 Telegram messages, single P&L source, dollar attribution" across at
  least two recent fixes (`239ac4b`, `c716e29`, `8942fe4`). Before touching anything
  under the brief/notification path, re-read those commits — this is not a one-off
  bug, it's a recurring invariant that keeps getting reintroduced.
- No stub returns that always succeed — if you can't implement it, raise `NotImplementedError`.
- No fake renames — renaming a function means updating every call site, not just the definition.

## Review checklist

- [ ] Brief/notification changes checked against the message-count/P&L-source invariant above.
- [ ] New patterns found in review worth adding here?
