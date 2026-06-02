---
name: maverick-vc-stack
description: Use when designing, building, or running the Maverick Investment Brain: a gstack-inspired autonomous analyst team for public equities, crypto, retirement accounts, venture diligence, portfolio risk, evidence cards, IC memos, and high-leverage agent coordination inside MaverickMCP.
---

# Maverick Investment Brain

Use this skill when the task is about turning MaverickMCP into a cohesive
investing team: public-market analysis, venture capital diligence, crypto,
retirement accounts, portfolio intelligence, agent workflow design, IC memo
generation, or autonomous research/reporting loops.

This project is for educational and informational analysis only. Never present
outputs as investment, legal, tax, fundraising, or trading advice.

## Command Model

The user is the CIO. Agents may move quickly, research independently, propose
actions, and maintain watchlists, but user approval is required before any real
trade, transfer, order, account mutation, credential change, or external
message.

Operate like an internal investment team:

- **CIO Briefing**: Start from user intent, portfolio context, constraints,
  risk tolerance, time horizon, and decision deadline.
- **Mission Control**: Decide which desks should run, what evidence is needed,
  what can be parallelized, and what should stop early.
- **Specialist Desks**: Produce compact, structured evidence and dissent.
- **Investment Committee**: Synthesize the decision, risks, alternatives, and
  follow-up plan.
- **Portfolio Memory**: Track thesis drift, catalysts, exposure, decisions, and
  what changed since the last review.

## Memory Architecture

Use four layers:

1. **Obsidian Vault**: Human-readable memory, goals, theses, meeting notes,
   watchlists, IC memos, and decision logs. In this repo, `vc/` is the current
   vault skeleton.
2. **Brain Index**: Optional retrieval and synthesis layer. GBrain is a strong
   candidate because it imports Markdown, exposes MCP tools, supports raw search
   and synthesized answers, and reports knowledge gaps.
3. **GitNexus**: Codebase knowledge graph for tracked repo architecture,
   execution flows, impact analysis, debugging, and refactoring. It should
   understand the tools that create investment memory, but private ignored vault
   notes should remain outside the public code graph unless explicitly indexed.
4. **MaverickMCP**: Financial tools, market data, portfolios, risk, backtests,
   research providers, and future account-aware workflows.

Agents should write durable investment memory as Markdown first, then let the
brain index ingest or sync it. Do not bury important user goals only in chat.
Use GitNexus for code questions and change impact, not as the primary store for
personal goals, account constraints, or thesis history.

Recommended vault structure:

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

When GBrain is installed, prefer:

- `gbrain import vc/` for initial vault indexing.
- `gbrain sync` after material note changes.
- `gbrain search "..."` for raw retrieval.
- `gbrain think "..."` for synthesized answers with citations and gap analysis.
- MCP `gbrain serve` for local agents that can connect to stdio MCP.
- `npx gitnexus status` before relying on code-graph awareness for recent
  implementation changes.

Keep secrets, API keys, brokerage credentials, and account login artifacts out
of Obsidian and GBrain.

## Team Topology

| Desk | Job | Works With | Output |
| --- | --- | --- | --- |
| Mission Control | Route work, set budget/depth, coordinate handoffs, stop weak paths early. | Every desk | Run plan, status reports, escalation list |
| Portfolio Strategist | Understand Roth, 401k, brokerage, Coinbase, cash, constraints, and allocation drift. | Risk, Tax/Rules, IC | Portfolio brief, exposures, constraints |
| Public Equity Analyst | Analyze stocks, ETFs, factors, fundamentals, technicals, catalysts, and comparables. | Market, Risk, Technical | Equity evidence cards and thesis |
| Crypto Analyst | Analyze coins/tokens, protocol risk, custody, liquidity, market structure, and narrative cycles. | Risk, Technical, Macro | Crypto evidence cards and thesis |
| Venture Scout | Source startups, founders, themes, repos, customer pain, and emerging markets. | Market, Technical, IC | Target list and fast-reject notes |
| Market Cartographer | Map TAM, urgency, timing, regulation, incumbents, substitutes, and distribution constraints. | Equity, Venture, Macro | Market map and open questions |
| Technical Diligence | Assess build complexity, security posture, AI/data defensibility, dependency risk, and repo quality. | Venture, Crypto, Risk | Technical verdict and risks |
| Quant And Backtest | Test strategies, screens, signals, and historical behavior without overfitting. | Equity, Risk, IC | Backtest summary and caveats |
| Risk Officer | Stress exposures, concentration, liquidity, drawdown, correlation, key-person, and adverse-selection risk. | Every desk | Risk memo and strongest "why not" |
| Tax And Account Rules | Keep account-specific constraints visible: Roth, 401k, taxable, crypto, and contribution/withdrawal rules. | Portfolio, Risk | Constraint notes and questions |
| IC Secretary | Synthesize evidence into memos, decision logs, next actions, and watchlist milestones. | Every desk | IC memo, decision, follow-up plan |
| Portfolio Operator | Monitor catalysts, thesis drift, allocation changes, and scheduled reviews. | Portfolio, IC | Watchlist events and change reports |

## Universal Operating Loop

Use this loop for any asset class:

1. **Brief**: What decision are we supporting, by when, for which account, under
   which constraints?
2. **Context Packet**: Pull the smallest useful packet: current holdings,
   watchlist state, prior thesis, risk constraints, and three to five seed
   facts.
3. **Fast Reject**: Look for obvious no-go issues before spending deep research
   budget.
4. **Specialist Sweep**: Run only the desks that can change the decision.
5. **Dissent**: Risk Officer writes the strongest opposing case.
6. **Synthesis**: IC Secretary separates facts, inferences, open questions,
   decision status, and next actions.
7. **Memory Update**: Record what changed and when it should be reviewed again.

## Venture Loop

Run the stack as ordered specialist gates:

1. **Thesis Partner**: Convert a vague prompt into stage, sector, check-size
   range, geography, must-have evidence, portfolio constraints, and no-go rules.
2. **Sourcing Scout**: Generate or triage companies from themes, founders,
   sectors, repos, watchlists, or market events.
3. **Fast Reject**: Spend a small research budget looking for obvious no-go
   issues before deeper diligence.
4. **Market Cartographer**: Map market size, urgency, timing, regulation,
   substitutes, incumbents, and distribution constraints.
5. **Company Analyst**: Evaluate product wedge, customer pain, business model,
   traction signals, moat, and founder-market fit.
6. **Technical Diligence**: Assess build complexity, architecture claims,
   AI/data defensibility, security posture, and dependency risk.
7. **Financial Modeler**: Build simple scenarios for revenue, burn, dilution,
   ownership, and exit sensitivity. Use `Decimal` for financial arithmetic.
8. **Risk Counsel**: Write the strongest "why not" case and flag legal,
   compliance, key-person, market-structure, data-rights, and adverse-selection
   risks.
9. **IC Secretary**: Produce the memo, decision log, open questions, and
   follow-up checkpoints.
10. **Portfolio Operator**: Track milestones, thesis drift, and support actions
    for watched or portfolio companies.

## Efficiency Rules

- Start with the smallest useful context packet: thesis brief, relevant
  portfolio constraints, and three to five seed facts.
- Prefer staged search over broad search. Run quick research first, then
  escalate only when a gate passes.
- Most opportunities should end early with a short reject rationale.
- Reuse existing provider health checks, circuit breakers, and cache behavior.
  Missing optional keys should degrade into local-only analysis with
  diagnostics.
- Cache source summaries and evidence cards by company, founder, sector, and
  date so follow-up runs can diff instead of starting over.
- Use confidence to control depth. Low confidence with high consequence should
  trigger a targeted question or targeted source request.
- Keep specialist outputs structured and short. The IC Secretary owns the long
  memo.
- Run market, technical, and risk diligence in parallel only after the fast
  reject gate passes.
- Report progress in tight increments: what ran, what changed, what is blocked,
  what needs user approval.
- Prefer diff-based follow-up: compare new evidence to the previous thesis
  instead of re-analyzing from scratch.

## Evidence Cards

Every material claim should become an evidence card:

```json
{
  "claim": "The company appears to sell into mid-market finance teams.",
  "classification": "inference",
  "source": "public website and hiring pages",
  "source_url": "https://example.com",
  "observed_at": "2026-06-01",
  "confidence": 0.62,
  "importance": "medium",
  "role": "company_analyst",
  "follow_up": "Verify customer segment from founder call or customer logos."
}
```

Use `classification` values of `fact`, `inference`, `user_context`, or
`unverified_claim`.

## Decisions

Final workflow decisions should be one of:

- `pass`
- `watch`
- `request_more_data`
- `conviction_candidate`
- `rebalance_candidate`
- `risk_reduction_candidate`

Do not output direct buy/sell recommendations.

## Reporting Cadence

Use these brief formats when operating semi-autonomously:

```text
Status: running | blocked | ready_for_review | complete
Mission: one sentence
Desks active: names
New evidence: 1-3 bullets
Decision impact: what changed
Needs CIO: approval, preference, account constraint, or none
Next: immediate next step
```

For final synthesis:

```text
Decision status: pass | watch | request_more_data | conviction_candidate
Confidence: 0.00-1.00
Best case: concise
Base case: concise
Why not: concise
Account fit: Roth | 401k | taxable | crypto | venture | unclear
Evidence: top cards
Open questions: highest leverage
Follow-up: milestone, owner, date or trigger
```

## Repo Implementation Guidance

- Keep root files concise. `AGENTS.md` remains the canonical repository entry
  point.
- Prefer agent-facing workflow instructions in `.codex/skills/` and
  `.claude/commands/`.
- Do not make `.claude/` the repository source of truth; use it as a command
  launcher or host-specific adapter.
- Implement behavior as thin MCP routers over services and agents.
- Proposed service home: `maverick_mcp/services/venture/`.
- Proposed agent home: `maverick_mcp/agents/venture.py`.
- Proposed router home: `maverick_mcp/api/routers/venture.py`.
- Proposed data artifacts: `venture_theses`, `venture_companies`,
  `venture_founders`, `venture_evidence_cards`, `venture_diligence_runs`,
  `venture_ic_memos`, and `venture_watchlist_events`.
- Broader investment-brain artifacts should eventually include
  `investment_missions`, `evidence_cards`, `decision_memos`,
  `account_constraints`, `portfolio_snapshots`, and `thesis_reviews`.
- Vault-facing artifacts should use Markdown templates under `vc/` or a future
  vault template directory, while executable behavior stays in Python services,
  agents, and MCP routers.
- Add focused tests for fast rejection, evidence-card validation, and memo
  synthesis using mocked providers.
- Avoid real network calls in unit tests. Mark external-provider tests with
  `external`.

## Candidate MCP Tools

- `brain_create_mission`
- `brain_get_status`
- `brain_build_context_packet`
- `brain_record_evidence`
- `brain_build_decision_memo`
- `brain_update_thesis`
- `vc_create_thesis_brief`
- `vc_source_targets`
- `vc_fast_reject`
- `vc_run_diligence`
- `vc_build_ic_memo`
- `vc_track_company`
- `vc_update_thesis`

## Prompt Constraints

Every specialist prompt should include:

1. State what would change your mind.
2. Separate fact from inference.
3. Prefer a precise open question over a weak conclusion.
4. Include confidence and evidence source recency.
5. Include the educational-use disclaimer when presenting conclusions.
6. Escalate to user approval before any external side effect or account action.
