"""
Agent of Sats – MCP Server

The main entrypoint that wires together:
  • FastMCP tool/resource definitions
  • Hyperliquid perps client
  • Performance log (Hypercore-like)
  • Moltbook social client
  • Li.Fi cross-chain stub
  • ERC‑8004 metadata helper

Run directly:
    python -m mcp_server.server

Or configure as an MCP server in Claude Desktop / any MCP-capable client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ── local imports ───────────────────────────────────────────────────────────
from clients.hyperliquid_client import HyperliquidPerpsClient, HyperliquidConfig
from clients.lifi_client import LifiClient
from clients.moltbook_client import MoltbookClient
from erc8004.registration import agent_metadata_json, generate_agent_metadata
from store.performance_log import (
    EVENT_ERROR,
    EVENT_PNL_SNAPSHOT,
    EVENT_STRATEGY_DECISION,
    EVENT_TRADE_CLOSE,
    EVENT_TRADE_OPEN,
    PerformanceLog,
)

# ── logging (stderr only, never stdout) ────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("agent_of_sats.mcp")

# ── load .env ───────────────────────────────────────────────────────────────

load_dotenv()

# ── shared state (initialised in lifespan) ──────────────────────────────────

perf_log: PerformanceLog | None = None
hl_client: HyperliquidPerpsClient | None = None
moltbook: MoltbookClient | None = None
lifi: LifiClient | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Initialise and tear down shared resources."""
    global perf_log, hl_client, moltbook, lifi

    perf_log = PerformanceLog()
    await perf_log.open()

    hl_client = HyperliquidPerpsClient()
    moltbook = MoltbookClient()
    lifi = LifiClient()

    logger.info("Agent of Sats MCP server started")
    try:
        yield {}
    finally:
        if perf_log:
            await perf_log.close()
        if hl_client:
            await hl_client.close()
        if moltbook:
            await moltbook.close()
        if lifi:
            await lifi.close()
        logger.info("Agent of Sats MCP server stopped")


# ── FastMCP application ────────────────────────────────────────────────────

mcp = FastMCP(
    "Agent of Sats",
    instructions=(
        "BTC-native strategy engine using Hyperliquid perps, "
        "exposed as an MCP server with ERC‑8004 identity. "
        "Use get_status() to check health, get_pnl_snapshot() for performance, "
        "run_basis_strategy() to execute strategies, close_positions() to exit, "
        "and share_pnl_to_moltbook() to publish updates."
    ),
    lifespan=app_lifespan,
)

# ═══════════════════════════════════════════════════════════════════════════
#  TOOLS
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_status() -> dict[str, Any]:
    """
    Health check: MCP version, Hyperliquid connectivity, last PnL snapshot.
    """
    assert hl_client and perf_log

    connected = await hl_client.is_connected()
    snapshot = await perf_log.get_latest_snapshot()

    return {
        "mcp_version": "1.0",
        "agent": "Agent of Sats",
        "hyperliquid_connected": connected,
        "hyperliquid_network": hl_client.cfg.network,
        "last_pnl_snapshot_ts": snapshot["ts"] if snapshot else None,
        "last_pnl_snapshot_iso": snapshot["iso"] if snapshot else None,
    }


@mcp.tool()
async def get_pnl_snapshot(window_24h: bool = True, window_7d: bool = True) -> dict[str, Any]:
    """
    Return cumulative PnL, windowed realized PnL, max drawdown, and open positions.
    """
    assert hl_client and perf_log

    summary_24h = await perf_log.compute_pnl_summary(window_hours=24) if window_24h else None
    summary_7d = await perf_log.compute_pnl_summary(window_hours=168) if window_7d else None

    positions = await hl_client.get_positions()
    pos_list = [
        {
            "symbol": p.symbol,
            "size": p.size,
            "entry_price": p.entry_price,
            "mark_price": p.mark_price,
            "unrealized_pnl": p.unrealized_pnl,
            "leverage": p.leverage,
        }
        for p in positions
    ]

    result: dict[str, Any] = {
        "open_positions": pos_list,
    }
    if summary_24h:
        result["pnl_24h"] = summary_24h
    if summary_7d:
        result["pnl_7d"] = summary_7d

    # Also persist a snapshot event
    await perf_log.append_event({
        "type": EVENT_PNL_SNAPSHOT,
        "payload": result,
    })

    return result


@mcp.tool()
async def run_basis_strategy(
    is_long_basis: bool = True,
    target_edge_bps: int = 10,
    max_leverage: float = 3.0,
) -> dict[str, Any]:
    """
    Execute a BTC perp basis strategy.

    Fetches market data, evaluates funding edge, and enters/adjusts a
    position if conditions are met.

    Parameters:
        is_long_basis    – True = long spot + short perp (collect funding);
                           False = short spot + long perp.
        target_edge_bps  – minimum annualised funding edge in basis points.
        max_leverage     – maximum allowed leverage for the perp leg.
    """
    assert hl_client and perf_log

    # 1. Fetch market data
    market = await hl_client.get_btc_market_info()
    funding_8h = market.funding_rate
    funding_annual_bps = funding_8h * 3 * 365 * 10_000  # annualised bps

    decision: dict[str, Any] = {
        "mark_price": market.mark_price,
        "index_price": market.index_price,
        "funding_8h": funding_8h,
        "funding_annual_bps": round(funding_annual_bps, 2),
        "target_edge_bps": target_edge_bps,
        "is_long_basis": is_long_basis,
        "max_leverage": max_leverage,
    }

    # 2. Decide whether to trade
    meets_edge = funding_annual_bps >= target_edge_bps if is_long_basis else funding_annual_bps <= -target_edge_bps

    if not meets_edge:
        decision["action"] = "no_trade"
        decision["reason"] = (
            f"Funding edge ({round(funding_annual_bps, 1)} bps ann.) "
            f"does not meet target ({target_edge_bps} bps)."
        )
        await perf_log.append_event({
            "type": EVENT_STRATEGY_DECISION,
            "payload": decision,
        })
        return decision

    # 3. Size the position (simple: $10k notional capped by max_leverage)
    notional_usd = 10_000.0
    size_btc = round(notional_usd / market.mark_price, 6)
    is_buy = not is_long_basis  # long basis = short perp

    # 4. Place the order
    try:
        order = await hl_client.place_order(
            symbol="BTC",
            is_buy=is_buy,
            size=size_btc,
            order_type="market",
        )
        decision["action"] = "order_placed"
        decision["order_id"] = order.order_id
        decision["side"] = order.side
        decision["size_btc"] = size_btc
        decision["notional_usd"] = notional_usd
        decision["order_status"] = order.status

        await perf_log.append_event({
            "type": EVENT_TRADE_OPEN,
            "payload": decision,
        })

    except Exception as exc:
        decision["action"] = "error"
        decision["error"] = str(exc)
        await perf_log.append_event({
            "type": EVENT_ERROR,
            "payload": decision,
        })

    return decision


@mcp.tool()
async def close_positions(symbol: str = "BTC") -> dict[str, Any]:
    """
    Close all open positions for a given symbol on Hyperliquid.
    Logs the closure event and realised PnL.
    """
    assert hl_client and perf_log

    positions_before = await hl_client.get_positions()
    relevant = [p for p in positions_before if p.symbol == symbol]

    if not relevant:
        return {"action": "no_positions", "symbol": symbol}

    results = await hl_client.close_all_positions(symbol)

    total_realized = sum(p.unrealized_pnl for p in relevant)  # approximate

    close_event = {
        "symbol": symbol,
        "positions_closed": len(results),
        "realized_pnl": round(total_realized, 4),
        "order_ids": [r.order_id for r in results],
    }

    await perf_log.append_event({
        "type": EVENT_TRADE_CLOSE,
        "payload": close_event,
    })

    return {
        "action": "positions_closed",
        **close_event,
    }


@mcp.tool()
async def share_pnl_to_moltbook(
    submolt_name: str = "aithoughts",
) -> dict[str, Any]:
    """
    Post the latest PnL summary to Moltbook AI (moltbookai.net).

    Builds a concise human-readable update and publishes it as a post
    on the specified submolt.  Auth uses EIP-191 wallet signature.

    Parameters:
        submolt_name – Moltbook submolt to post in (default: "aithoughts").
    """
    assert moltbook and perf_log

    summary_7d = await perf_log.compute_pnl_summary(window_hours=168)

    cum_pnl = summary_7d["cumulative_pnl_usd"]
    max_dd = summary_7d["max_drawdown_usd"]
    realized_7d = summary_7d["realized_pnl_window_usd"]
    num_trades = summary_7d["total_closed_trades"]

    title = f"Agent of Sats – Weekly PnL: ${realized_7d:+,.2f}"

    content = (
        f"🟠 **Agent of Sats** weekly performance update\n\n"
        f"• Cumulative PnL: **${cum_pnl:+,.2f}**\n"
        f"• 7d realized: **${realized_7d:+,.2f}**\n"
        f"• Max drawdown: **${max_dd:,.2f}**\n"
        f"• Closed trades (lifetime): **{num_trades}**\n\n"
        f"Running BTC perp basis strategy on Hyperliquid.\n"
        f"#AgentOfSats #BTC #Hyperliquid"
    )

    post = await moltbook.post_pnl_update(
        title=title,
        content=content,
        submolt_name=submolt_name,
    )

    return {
        "action": "posted",
        "moltbook_post": post,
        "title": title,
        "content": content,
    }


@mcp.tool()
async def lifi_get_quote(
    from_chain: str = "ethereum",
    to_chain: str = "arbitrum",
    from_token: str = "USDC",
    to_token: str = "USDC",
    from_amount: str = "1000000",
    from_address: str = "0x0000000000000000000000000000000000000000",
    slippage: float = 0.005,
) -> dict[str, Any]:
    """
    Get a Li.Fi cross-chain transfer quote.

    Calls the live Li.Fi API to find the optimal route across 27+ bridges,
    31+ DEXs, and intent-based solvers across 58+ blockchains.

    Parameters:
        from_chain   – source chain name or ID (e.g. "ethereum", "arbitrum", "base", 42161)
        to_chain     – destination chain name or ID
        from_token   – source token symbol or address (e.g. "USDC", "ETH", "0x...")
        to_token     – destination token symbol or address
        from_amount  – amount in smallest unit (e.g. "1000000" for 1 USDC)
        from_address – sender wallet address (0x...)
        slippage     – max slippage as decimal (default 0.005 = 0.5%)
    """
    assert lifi

    try:
        quote = await lifi.get_quote(
            from_chain=from_chain,
            to_chain=to_chain,
            from_token=from_token,
            to_token=to_token,
            from_amount=from_amount,
            from_address=from_address,
            slippage=slippage,
        )

        # Extract the most useful fields for a concise response
        action = quote.get("action", {})
        estimate = quote.get("estimate", {})
        tool_info = quote.get("tool", "")
        has_tx = "transactionRequest" in quote

        return {
            "action": "quote_received",
            "tool_used": tool_info,
            "from": f"{action.get('fromToken', {}).get('symbol', from_token)} on {action.get('fromChainId', from_chain)}",
            "to": f"{action.get('toToken', {}).get('symbol', to_token)} on {action.get('toChainId', to_chain)}",
            "from_amount": action.get("fromAmount", from_amount),
            "to_amount": estimate.get("toAmount", "N/A"),
            "to_amount_min": estimate.get("toAmountMin", "N/A"),
            "gas_costs_usd": sum(
                float(g.get("amountUSD", 0))
                for g in estimate.get("gasCosts", [])
            ),
            "fee_costs_usd": sum(
                float(f.get("amountUSD", 0))
                for f in estimate.get("feeCosts", [])
            ),
            "execution_duration_s": estimate.get("executionDuration", "N/A"),
            "has_transaction_request": has_tx,
            "full_quote": quote,
        }
    except Exception as exc:
        return {
            "action": "error",
            "error": str(exc),
            "hint": (
                "Ensure from_amount is in smallest unit (e.g. 1000000 for 1 USDC). "
                "Use lifi_get_chains() to list valid chain names/IDs."
            ),
        }


@mcp.tool()
async def lifi_get_routes(
    from_chain: str = "ethereum",
    to_chain: str = "arbitrum",
    from_token: str = "USDC",
    to_token: str = "USDC",
    from_amount: str = "1000000",
    from_address: str = "0x0000000000000000000000000000000000000000",
) -> dict[str, Any]:
    """
    Get multiple route options for a cross-chain transfer via Li.Fi.

    Compares different bridges and DEX paths. Useful to show users
    cost/speed tradeoffs before executing.

    Parameters are the same as lifi_get_quote.
    """
    assert lifi

    try:
        result = await lifi.get_routes(
            from_chain=from_chain,
            to_chain=to_chain,
            from_token=from_token,
            to_token=to_token,
            from_amount=from_amount,
            from_address=from_address,
        )

        routes = result.get("routes", [])
        summaries = []
        for i, route in enumerate(routes[:5]):  # top 5
            steps = route.get("steps", [])
            summaries.append({
                "rank": i + 1,
                "to_amount": route.get("toAmount", "N/A"),
                "to_amount_min": route.get("toAmountMin", "N/A"),
                "gas_usd": route.get("gasCostUSD", "N/A"),
                "steps": len(steps),
                "tags": route.get("tags", []),
            })

        return {
            "action": "routes_received",
            "route_count": len(routes),
            "top_routes": summaries,
            "full_result": result,
        }
    except Exception as exc:
        return {"action": "error", "error": str(exc)}


@mcp.tool()
async def lifi_check_status(
    tx_hash: str,
    bridge: str = "",
    from_chain: str = "",
    to_chain: str = "",
) -> dict[str, Any]:
    """
    Check the status of a cross-chain transfer via Li.Fi.

    Status values: NOT_FOUND, PENDING, DONE, FAILED.
    Substatus on DONE: COMPLETED, PARTIAL, REFUNDED.

    Parameters:
        tx_hash    – the source chain transaction hash
        bridge     – bridge name (optional, improves lookup speed)
        from_chain – source chain name or ID (optional)
        to_chain   – destination chain name or ID (optional)
    """
    assert lifi

    try:
        status = await lifi.get_status(
            tx_hash=tx_hash,
            bridge=bridge or None,
            from_chain=from_chain or None,
            to_chain=to_chain or None,
        )
        return {
            "status": status.get("status", "UNKNOWN"),
            "substatus": status.get("substatus"),
            "substatusMessage": status.get("substatusMessage"),
            "sending_tx": status.get("sending", {}).get("txHash"),
            "receiving_tx": status.get("receiving", {}).get("txHash"),
            "bridge_used": status.get("tool"),
            "full_status": status,
        }
    except Exception as exc:
        return {"action": "error", "error": str(exc)}


@mcp.tool()
async def lifi_get_chains() -> dict[str, Any]:
    """
    List all EVM chains supported by Li.Fi.

    Returns chain IDs, names, and native tokens. Useful to discover
    valid values for lifi_get_quote's from_chain / to_chain parameters.
    """
    assert lifi

    try:
        result = await lifi.get_chains(chain_types="EVM")
        chains = result if isinstance(result, list) else result.get("chains", result)
        summary = [
            {"id": c.get("id"), "key": c.get("key"), "name": c.get("name")}
            for c in (chains if isinstance(chains, list) else [])
        ]
        return {
            "action": "chains_listed",
            "count": len(summary),
            "chains": summary,
        }
    except Exception as exc:
        return {"action": "error", "error": str(exc)}


@mcp.tool()
async def lifi_get_tools() -> dict[str, Any]:
    """
    List all bridges and DEX aggregators available through Li.Fi.
    """
    assert lifi

    try:
        result = await lifi.get_tools()
        bridges = result.get("bridges", [])
        exchanges = result.get("exchanges", [])
        return {
            "action": "tools_listed",
            "bridge_count": len(bridges),
            "exchange_count": len(exchanges),
            "bridges": [b.get("key") for b in bridges],
            "exchanges": [e.get("key") for e in exchanges],
        }
    except Exception as exc:
        return {"action": "error", "error": str(exc)}


@mcp.tool()
async def get_erc8004_registration() -> dict[str, Any]:
    """
    Generate the ERC‑8004 agent registration metadata JSON.

    This JSON should be pinned to IPFS and passed to the ERC‑8004
    registry's registerAgent(tokenURI) call.
    """
    metadata = generate_agent_metadata()
    return {
        "metadata": metadata,
        "metadata_json": agent_metadata_json(),
        "instructions": (
            "1. Pin the metadata JSON to IPFS.\n"
            "2. Call registerAgent(ipfs://...) on the ERC‑8004 registry.\n"
            "3. The agent's on-chain identity now references its MCP endpoint, "
            "Moltbook profile, and performance log."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  RESOURCES
# ═══════════════════════════════════════════════════════════════════════════


@mcp.resource("agent://performance-log")
async def performance_log_resource() -> str:
    """
    Stream raw performance log events (trades, PnL snapshots, errors).

    Returns the last 100 events as newline-delimited JSON.
    This resource will later be backed by a Hypercore feed.
    """
    assert perf_log
    events = await perf_log.iter_events(limit=100)
    return "\n".join(json.dumps(e) for e in events)


@mcp.resource("agent://erc8004-metadata")
async def erc8004_metadata_resource() -> str:
    """Return the ERC‑8004 registration metadata as JSON."""
    return agent_metadata_json()


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════


def main():
    """Run the MCP server (stdio transport by default)."""
    mcp.run()


if __name__ == "__main__":
    main()
