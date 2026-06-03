"""VC loop MCP tools.

A hybrid, human-in-the-loop investing loop for public equities: it sources
candidates, scores conviction deterministically, writes memos + a ranked
pipeline into an Obsidian vault, and records every thesis in a ledger. It
proposes; it never executes a trade.

DISCLAIMER: educational/research output only, not investment advice.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_vc_loop_tools(mcp: FastMCP) -> None:
    """Register VC loop tools on the given FastMCP instance."""

    @mcp.tool(
        name="vc_loop_run",
        description=(
            "Run one VC-loop pass: source candidates from a screen, score "
            "conviction (deterministic, no LLM), write thesis + pipeline notes "
            "into the Obsidian vault, and record each thesis in the ledger. "
            "Returns a ranked proposal. Proposes only — never trades."
        ),
    )
    async def vc_loop_run(
        strategy: str = "maverick_bullish",
        limit: int = 20,
        top_n: int = 10,
        days_ahead: int = 30,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        try:
            from maverick_mcp.config.settings import get_settings
            from maverick_mcp.vc_loop.orchestrator import run_vc_loop

            vault_path = get_settings().vc_loop.vault_path
            return await run_vc_loop(
                vault_path=vault_path,
                strategy=strategy,
                limit=limit,
                top_n=top_n,
                days_ahead=days_ahead,
                dry_run=dry_run,
            )
        except Exception as e:
            logger.error("vc_loop_run error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="vc_loop_pipeline",
        description=(
            "Return the most recent VC-loop proposals from the thesis ledger "
            "plus summary stats, for review before deciding."
        ),
    )
    def vc_loop_pipeline(limit: int = 20) -> dict[str, Any]:
        try:
            from maverick_mcp.data.models import SessionLocal
            from maverick_mcp.vc_loop.ledger import ThesisService, to_dict

            with SessionLocal() as session:
                svc = ThesisService(session)
                rows = svc.latest_run(limit=limit)
                stats = svc.thesis_stats()
                return {
                    "status": "ok",
                    "count": len(rows),
                    "stats": stats,
                    "theses": [to_dict(r) for r in rows],
                }
        except Exception as e:
            logger.error("vc_loop_pipeline error: %s", e)
            return {"status": "error", "error": str(e)}
