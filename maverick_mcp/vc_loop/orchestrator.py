"""VC loop orchestrator.

Ties the spine together: gather candidates -> score conviction (deterministic,
no LLM) -> persist each thesis to the ledger -> write Obsidian notes -> return a
ranked proposal. This is the *hybrid* loop: it proposes, it never executes a
trade.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from maverick_mcp.vc_loop import obsidian, scorer
from maverick_mcp.vc_loop.candidates import Candidate, gather_candidates
from maverick_mcp.vc_loop.ledger import ThesisService, to_dict

logger = logging.getLogger(__name__)


def _build_thesis_text(
    candidate: Candidate,
    conviction: float,
    action: str,
    strategy: str,
) -> str:
    """Compose a concise, deterministic rationale from the signals (no LLM)."""
    f = candidate.features
    raw = candidate.raw
    parts: list[str] = [
        f"{candidate.ticker} surfaced via {strategy} screening "
        f"(screening signal {f.get('screening_score', 0):.2f}, "
        f"momentum {f.get('momentum', 0):.2f})."
    ]

    trend = raw.get("trend")
    outlook = raw.get("outlook")
    if trend or outlook:
        parts.append(
            f"Technical strength {f.get('technical_strength', 0.5):.2f}"
            + (f" ({outlook})" if outlook else f" ({trend})" if trend else "")
            + "."
        )

    sentiment = raw.get("sentiment")
    if sentiment:
        parts.append(
            f"News sentiment {sentiment} "
            f"(feature {f.get('sentiment_confidence', 0.5):.2f})."
        )

    catalysts = raw.get("catalysts")
    if catalysts:
        parts.append(
            f"{len(catalysts)} upcoming catalyst(s) "
            f"(proximity {f.get('catalyst_proximity', 0.0):.2f})."
        )

    parts.append(f"Conviction {conviction:.1f} -> {action}.")
    return " ".join(parts)


async def run_vc_loop(
    *,
    vault_path: str,
    strategy: str = "maverick_bullish",
    limit: int = 20,
    top_n: int = 10,
    days_ahead: int = 30,
    dry_run: bool = True,
) -> dict:
    """Run one VC-loop pass and return the ranked proposal.

    Args:
        vault_path: Obsidian vault root to write notes into.
        strategy: ``maverick_bullish`` | ``maverick_bearish`` | ``supply_demand``.
        limit: Max candidates pulled from the screen.
        top_n: Top-N candidates enriched with technical/sentiment/catalysts.
        days_ahead: Catalyst look-ahead window in days.
        dry_run: Informational only in Phase 1 — the loop never trades.

    Returns:
        A dict with the ranked proposals, ledger rows, and the pipeline note path.
    """
    candidates = await gather_candidates(
        strategy=strategy,
        limit=limit,
        enrich_top_n=top_n,
        days_ahead=days_ahead,
    )

    if not candidates:
        return {
            "status": "ok",
            "dry_run": dry_run,
            "executed": False,
            "strategy": strategy,
            "count": 0,
            "proposals": [],
            "message": "No candidates returned by screening.",
            "as_of": datetime.now(UTC).isoformat(),
        }

    # Score and rank (highest conviction first).
    scored: list[tuple[Candidate, float, dict, str]] = []
    for cand in candidates:
        conviction, breakdown = scorer.score_candidate(cand.features)
        action = scorer.proposed_action(conviction)
        scored.append((cand, conviction, breakdown, action))
    scored.sort(key=lambda t: t[1], reverse=True)

    date = datetime.now(UTC).date().isoformat()
    obsidian.ensure_vault(vault_path)

    # Lazy import keeps module import side-effect-free for unit tests.
    from maverick_mcp.data.models import SessionLocal

    ranked: list[dict] = []
    rows: list[dict] = []
    with SessionLocal() as session:
        svc = ThesisService(session)
        for cand, conviction, breakdown, action in scored:
            thesis_text = _build_thesis_text(cand, conviction, action, strategy)
            catalysts = cand.raw.get("catalysts")

            obsidian.write_company_note(vault_path, cand.ticker, cand.sector)
            note_path = obsidian.write_thesis_note(
                vault_path,
                ticker=cand.ticker,
                date=date,
                conviction=conviction,
                action=action,
                thesis=thesis_text,
                breakdown=breakdown,
                features=cand.features,
                sector=cand.sector,
                catalysts=catalysts,
            )

            row = svc.add_thesis(
                ticker=cand.ticker,
                thesis=thesis_text,
                conviction=conviction,
                features=cand.features,
                score_breakdown=breakdown,
                proposed_action=action,
                sector=cand.sector,
                note_path=note_path,
            )

            ranked.append(
                {
                    "ticker": cand.ticker,
                    "conviction": conviction,
                    "action": action,
                    "thesis_path": note_path,
                }
            )
            rows.append(to_dict(row))

        pipeline_note = obsidian.write_pipeline_note(
            vault_path, date=date, ranked=ranked
        )
        obsidian.write_pipeline_base(vault_path)

    return {
        "status": "ok",
        "dry_run": dry_run,
        "executed": False,  # Phase 1 never trades.
        "strategy": strategy,
        "date": date,
        "vault_path": str(vault_path),
        "pipeline_note": pipeline_note,
        "count": len(rows),
        "proposals": rows,
        "as_of": datetime.now(UTC).isoformat(),
    }
