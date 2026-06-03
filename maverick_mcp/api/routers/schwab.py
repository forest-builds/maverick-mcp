"""Schwab Trader API MCP tools."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _load_schwab() -> tuple[Any, Any]:
    from maverick_mcp.providers.schwab import SchwabAuthConfig, SchwabTokenStore

    config = SchwabAuthConfig.from_env()
    return config, SchwabTokenStore(config.token_file)


def _safe_account_numbers(account_numbers: Any) -> list[dict[str, Any]]:
    safe = []
    for item in account_numbers or []:
        account_number = str(item.get("accountNumber", ""))
        hash_value = str(item.get("hashValue", ""))
        safe.append(
            {
                "account_label": f"...{account_number[-4:]}"
                if account_number
                else "unknown",
                "hash_value": hash_value,
                "hash_preview": f"{hash_value[:6]}...{hash_value[-6:]}"
                if len(hash_value) > 12
                else hash_value,
            }
        )
    return safe


def _extract_code(code_or_url: str) -> str:
    """Accept either a raw code or a full callback URL."""
    value = code_or_url.strip()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        if not code:
            raise ValueError("Callback URL does not contain a code parameter")
        return code
    return value


def register_schwab_tools(mcp: FastMCP) -> None:
    """Register Schwab tools on the given FastMCP instance."""

    @mcp.tool(
        name="broker_link_start",
        description=(
            "Start linking your brokerage (Schwab): generate the OAuth login URL. "
            "Open it in a browser, approve account access, then pass the returned "
            "callback URL to broker_link_finish."
        ),
    )
    def broker_link_start() -> dict[str, Any]:
        try:
            config, store = _load_schwab()
            return {
                "status": "ok",
                "authorization_url": config.authorization_url(),
                "redirect_uri": config.redirect_uri,
                "token_file": str(store.token_file),
            }
        except Exception as e:
            logger.error("broker_link_start error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="broker_link_finish",
        description=(
            "Finish linking your brokerage (Schwab): exchange an authorization code "
            "or full callback URL for local tokens. Stores tokens in "
            "SCHWAB_TOKEN_FILE."
        ),
    )
    def broker_link_finish(code_or_callback_url: str) -> dict[str, Any]:
        try:
            from maverick_mcp.providers.schwab.auth import exchange_authorization_code

            config, store = _load_schwab()
            code = _extract_code(code_or_callback_url)
            token = exchange_authorization_code(config, code)
            store.write(token)
            return {"status": "ok", "auth": store.status()}
        except Exception as e:
            logger.error("broker_link_finish error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="broker_link_status",
        description="Return safe brokerage link/token status without secrets (Schwab).",
    )
    def broker_link_status() -> dict[str, Any]:
        try:
            _, store = _load_schwab()
            return {"status": "ok", "auth": store.status()}
        except Exception as e:
            logger.error("broker_link_status error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="broker_list_accounts",
        description=(
            "List linked brokerage accounts (Schwab) by label and hash value; "
            "doubles as a connectivity smoke-test. Full account numbers are not "
            "returned."
        ),
    )
    def broker_list_accounts() -> dict[str, Any]:
        try:
            from maverick_mcp.providers.schwab import SchwabClient

            config, store = _load_schwab()
            client = SchwabClient(config, store)
            account_numbers = client.account_numbers()
            return {
                "status": "ok",
                "accounts": _safe_account_numbers(account_numbers),
            }
        except Exception as e:
            logger.error("broker_list_accounts error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="broker_positions",
        description=(
            "Fetch live brokerage positions (Schwab): normalized ticker, shares, "
            "average price, market value, and asset type."
        ),
    )
    def broker_positions() -> dict[str, Any]:
        try:
            from maverick_mcp.providers.schwab import SchwabClient
            from maverick_mcp.providers.schwab.sync import fetch_schwab_positions

            config, store = _load_schwab()
            client = SchwabClient(config, store)
            positions = fetch_schwab_positions(client)
            return {
                "status": "ok",
                "count": len(positions),
                "positions": [
                    {
                        "ticker": p.ticker,
                        "shares": float(p.shares),
                        "average_price": float(p.average_price),
                        "total_cost": float(p.total_cost),
                        "market_value": float(p.market_value)
                        if p.market_value is not None
                        else None,
                        "asset_type": p.asset_type,
                    }
                    for p in positions
                ],
                "as_of": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            logger.error("broker_positions error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="broker_account_summary",
        description=(
            "Fetch a scrubbed brokerage account summary (Schwab): account type, "
            "position count, liquidation value, and available cash when provided."
        ),
    )
    def broker_account_summary() -> dict[str, Any]:
        try:
            from maverick_mcp.providers.schwab import SchwabClient
            from maverick_mcp.providers.schwab.sync import summarize_accounts

            config, store = _load_schwab()
            client = SchwabClient(config, store)
            summaries = summarize_accounts(client.accounts(fields="positions") or [])
            total_liquidation_value = sum(
                (s.liquidation_value for s in summaries if s.liquidation_value),
                start=0,
            )
            return {
                "status": "ok",
                "account_count": len(summaries),
                "total_liquidation_value": float(total_liquidation_value),
                "accounts": [
                    {
                        "account_type": s.account_type,
                        "positions_count": s.positions_count,
                        "liquidation_value": float(s.liquidation_value)
                        if s.liquidation_value is not None
                        else None,
                        "cash_balance": float(s.cash_balance)
                        if s.cash_balance is not None
                        else None,
                    }
                    for s in summaries
                ],
                "as_of": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            logger.error("broker_account_summary error: %s", e)
            return {"status": "error", "error": str(e)}

    @mcp.tool(
        name="maverick_sync_portfolio",
        description=(
            "Import live brokerage positions (Schwab) into Maverick's local "
            "portfolio storage so they can be analyzed. Existing positions in the "
            "selected portfolio are replaced."
        ),
    )
    def maverick_sync_portfolio(
        portfolio_name: str = "Schwab",
        user_id: str = "default",
    ) -> dict[str, Any]:
        try:
            from maverick_mcp.providers.schwab import SchwabClient
            from maverick_mcp.providers.schwab.sync import sync_schwab_portfolio

            config, store = _load_schwab()
            client = SchwabClient(config, store)
            return sync_schwab_portfolio(
                client,
                portfolio_name=portfolio_name,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("maverick_sync_portfolio error: %s", e)
            return {"status": "error", "error": str(e)}
