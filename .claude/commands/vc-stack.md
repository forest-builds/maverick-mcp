# VC Stack

Use this command for gstack-inspired venture capital workflows in MaverickMCP:
thesis briefs, sourcing, fast rejection, specialist diligence, evidence cards,
IC memos, and venture watchlist follow-up.

Follow the canonical agent guidance in `AGENTS.md`. For the VC workflow itself,
use `.codex/skills/maverick-vc-stack/SKILL.md` as the shared stack definition.
Do not treat `.claude/` as the repository source of truth.

Default flow:

1. Clarify the thesis and no-go criteria.
2. Run a low-cost fast reject screen.
3. Escalate only passing opportunities to market, company, technical,
   financial, and risk diligence.
4. Require evidence cards for material claims.
5. Produce one of `pass`, `watch`, `request_more_data`, or
   `conviction_candidate`.
6. Add follow-up checkpoints for watched or stronger opportunities.

Outputs are educational and informational only, never investment, legal, tax,
fundraising, or trading advice.
