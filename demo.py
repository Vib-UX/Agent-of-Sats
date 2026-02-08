#!/usr/bin/env python3
"""
Agent of Sats — Live Demo Script

Runs through every MCP tool in sequence with formatted output,
simulating the Cursor/Claude Desktop flow for a recording.

Usage:
    source .venv/bin/activate
    python demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

# ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

# ── formatting helpers ──────────────────────────────────────────────────────

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def banner(title: str, icon: str = "▸"):
    width = 64
    print()
    print(f"{CYAN}{'━' * width}{RESET}")
    print(f"{CYAN}{BOLD}  {icon}  {title}{RESET}")
    print(f"{CYAN}{'━' * width}{RESET}")
    print()


def step(label: str):
    print(f"  {DIM}→ {label}...{RESET}")


def result_json(data: dict, indent: int = 2):
    formatted = json.dumps(data, indent=indent, default=str)
    for line in formatted.split("\n"):
        print(f"  {GREEN}{line}{RESET}")
    print()


def narrate(text: str):
    print(f"  {YELLOW}{text}{RESET}")
    print()


def pause(seconds: float = 1.5):
    time.sleep(seconds)


# ── demo flow ───────────────────────────────────────────────────────────────


async def run_demo():
    from mcp_server.server import mcp, app_lifespan
    from mcp_server import server

    async with app_lifespan(mcp):
        print()
        print(f"{MAGENTA}{BOLD}")
        print("  ╔══════════════════════════════════════════════════════╗")
        print("  ║          AGENT OF SATS — LIVE DEMO                  ║")
        print("  ║   BTC-native strategy engine • MCP server           ║")
        print("  ║   Hyperliquid • ERC-8004 • Li.Fi • Moltbook         ║")
        print("  ╚══════════════════════════════════════════════════════╝")
        print(f"{RESET}")
        pause(2)

        # ── 1. get_status ───────────────────────────────────────────
        banner("Scene 1: Health Check", "🔌")
        narrate("Checking MCP server status and Hyperliquid connectivity...")
        step("Calling get_status()")
        pause()

        status = await server.get_status()
        result_json(status)

        narrate(
            f"Connected to Hyperliquid {status['hyperliquid_network']} "
            f"• Wallet: {status['hyperliquid_address']}"
        )
        pause(2)

        # ── 2. get_pnl_snapshot ─────────────────────────────────────
        banner("Scene 2: Live PnL Snapshot", "📊")
        narrate("Fetching account state and PnL from Hyperliquid mainnet...")
        step("Calling get_pnl_snapshot(window_24h=True, window_7d=True)")
        pause()

        snapshot = await server.get_pnl_snapshot()

        # Show account summary separately for clarity
        print(f"  {GREEN}{BOLD}Account Summary:{RESET}")
        acct = snapshot["account"]
        print(f"  {GREEN}  Account Value:  ${acct['value_usd']:,.2f}{RESET}")
        print(f"  {GREEN}  Notional Pos:   ${acct['total_notional']:,.2f}{RESET}")
        print(f"  {GREEN}  Margin Used:    ${acct['margin_used']:,.2f}{RESET}")
        print(f"  {GREEN}  Withdrawable:   ${acct['withdrawable']:,.2f}{RESET}")
        print()

        positions = snapshot.get("open_positions", [])
        if positions:
            print(f"  {GREEN}{BOLD}Open Positions:{RESET}")
            for p in positions:
                side = "LONG" if p["size"] > 0 else "SHORT"
                print(
                    f"  {GREEN}  {p['symbol']} {side} {abs(p['size'])} "
                    f"@ ${p['entry_price']:,.2f} → ${p['mark_price']:,.2f} "
                    f"uPnL: ${p['unrealized_pnl']:+,.2f}{RESET}"
                )
            print()
        else:
            print(f"  {GREEN}  No open positions{RESET}")
            print()

        if snapshot.get("pnl_7d"):
            print(f"  {GREEN}{BOLD}7-Day PnL:{RESET}")
            pnl = snapshot["pnl_7d"]
            print(f"  {GREEN}  Cumulative:     ${pnl['cumulative_pnl_usd']:+,.2f}{RESET}")
            print(f"  {GREEN}  7d Realized:    ${pnl['realized_pnl_window_usd']:+,.2f}{RESET}")
            print(f"  {GREEN}  Max Drawdown:   ${pnl['max_drawdown_usd']:,.2f}{RESET}")
            print(f"  {GREEN}  Closed Trades:  {pnl['total_closed_trades']}{RESET}")
            print()

        pause(2)

        # ── 3. run_basis_strategy ───────────────────────────────────
        banner("Scene 3: BTC Basis Strategy", "⚡")
        narrate("Running basis strategy — reading live funding rates from Hyperliquid...")
        step("Calling run_basis_strategy(is_long_basis=True, target_edge_bps=50, max_leverage=3.0)")
        pause()

        strategy = await server.run_basis_strategy(
            is_long_basis=True,
            target_edge_bps=50,
            max_leverage=3.0,
        )

        print(f"  {GREEN}{BOLD}Strategy Decision:{RESET}")
        print(f"  {GREEN}  BTC Mark:         ${strategy['mark_price']:,.2f}{RESET}")
        print(f"  {GREEN}  BTC Oracle:       ${strategy['index_price']:,.2f}{RESET}")
        print(f"  {GREEN}  Funding (8h):     {strategy['funding_8h']:.8f}{RESET}")
        print(f"  {GREEN}  Annual Edge:      {strategy['funding_annual_bps']:+.1f} bps{RESET}")
        print(f"  {GREEN}  Target Edge:      {strategy['target_edge_bps']} bps{RESET}")
        print(f"  {GREEN}  Action:           {strategy['action'].upper()}{RESET}")
        if strategy.get("reason"):
            print(f"  {GREEN}  Reason:           {strategy['reason']}{RESET}")
        if strategy.get("order_id"):
            print(f"  {GREEN}  Order ID:         {strategy['order_id']}{RESET}")
            print(f"  {GREEN}  Side:             {strategy['side']}{RESET}")
            print(f"  {GREEN}  Size:             {strategy['size_btc']} BTC{RESET}")
        print()
        pause(2)

        # ── 4. lifi_get_quote ───────────────────────────────────────
        banner("Scene 4: Li.Fi Cross-Chain Quote", "🌉")
        narrate("Getting a live bridge quote — 10 USDC from Ethereum to Arbitrum...")
        step("Calling lifi_get_quote(from_chain='ethereum', to_chain='arbitrum', from_amount='10000000')")
        pause()

        wallet = status.get("hyperliquid_address", "0x7623f00fa06A6Cf6fD084F99925557ad5416Ff01")
        quote = await server.lifi_get_quote(
            from_chain="ethereum",
            to_chain="arbitrum",
            from_token="USDC",
            to_token="USDC",
            from_amount="10000000",  # 10 USDC
            from_address=wallet,
        )

        if quote.get("action") == "quote_received":
            print(f"  {GREEN}{BOLD}Bridge Quote:{RESET}")
            print(f"  {GREEN}  Route:       {quote['from']} → {quote['to']}{RESET}")
            print(f"  {GREEN}  Bridge:      {quote['tool_used']}{RESET}")
            print(f"  {GREEN}  Output:      {quote['to_amount']} (min: {quote['to_amount_min']}){RESET}")
            print(f"  {GREEN}  Gas:         ${quote['gas_costs_usd']:.4f}{RESET}")
            print(f"  {GREEN}  Fees:        ${quote['fee_costs_usd']:.4f}{RESET}")
            print(f"  {GREEN}  ETA:         {quote['execution_duration_s']}s{RESET}")
            print(f"  {GREEN}  TX Ready:    {'✅' if quote['has_transaction_request'] else '❌'}{RESET}")
        else:
            print(f"  {GREEN}  Quote result:{RESET}")
            result_json(quote)

        print()
        narrate(
            "Proof: https://scan.li.fi/tx/0xc3b874343d018c72a38132aea5cbb1977179fc3d4d6118f9b394376cf8883da0"
        )
        pause(2)

        # ── 5. share_pnl_to_moltbook ───────────────────────────────
        banner("Scene 5: Post to Moltbook AI", "📡")
        narrate("Posting PnL update to Moltbook with EIP-191 wallet signature...")
        step("Calling share_pnl_to_moltbook(submolt_name='aithoughts')")
        pause()

        moltbook_result = await server.share_pnl_to_moltbook(submolt_name="aithoughts")

        print(f"  {GREEN}{BOLD}Moltbook Post:{RESET}")
        print(f"  {GREEN}  Action:  {moltbook_result['action']}{RESET}")
        print(f"  {GREEN}  Title:   {moltbook_result['title']}{RESET}")
        print()
        print(f"  {GREEN}{BOLD}Content:{RESET}")
        for line in moltbook_result["content"].split("\n"):
            print(f"  {GREEN}  {line}{RESET}")
        print()

        post_data = moltbook_result.get("moltbook_post", {})
        post_id = post_data.get("id", post_data.get("post_id", ""))
        if post_id:
            print(f"  {GREEN}  Post ID: {post_id}{RESET}")
            print(f"  {GREEN}  URL:     https://moltbookai.net/post/{post_id}{RESET}")

        print()
        narrate(
            "Live post: https://moltbookai.net/post/8189a12a-937d-467d-9917-99cdbbf97eee"
        )
        pause(2)

        # ── 6. get_erc8004_registration ─────────────────────────────
        banner("Scene 6: ERC-8004 On-Chain Identity", "🔗")
        narrate("Generating ERC-8004 agent registration metadata...")
        step("Calling get_erc8004_registration()")
        pause()

        erc = await server.get_erc8004_registration()

        print(f"  {GREEN}{BOLD}ERC-8004 Metadata:{RESET}")
        result_json(erc["metadata"])

        print(f"  {GREEN}{BOLD}Registration Steps:{RESET}")
        for line in erc["instructions"].split("\n"):
            print(f"  {GREEN}  {line}{RESET}")
        print()
        pause(2)

        # ── 7. Performance log ──────────────────────────────────────
        banner("Scene 7: Append-Only Performance Log", "📜")
        narrate("Reading the performance log — every action above was recorded...")
        step("Reading agent://performance-log resource")
        pause()

        from mcp_server.server import perf_log as _log
        assert _log
        events = await _log.iter_events(limit=10)

        print(f"  {GREEN}{BOLD}Last {len(events)} log events:{RESET}")
        for e in events:
            ts = e.get("iso", e.get("ts", ""))[:19]
            etype = e.get("type", "unknown")
            print(f"  {GREEN}  [{ts}] {etype}{RESET}")
        print()
        pause(2)

        # ── Outro ───────────────────────────────────────────────────
        print()
        print(f"{MAGENTA}{BOLD}")
        print("  ╔══════════════════════════════════════════════════════╗")
        print("  ║                    DEMO COMPLETE                    ║")
        print("  ╠══════════════════════════════════════════════════════╣")
        print("  ║                                                      ║")
        print("  ║  Hyperliquid   Live BTC perps on mainnet            ║")
        print("  ║  Li.Fi         Cross-chain routing across 30+ EVM   ║")
        print("  ║  Moltbook AI   Social reputation via EIP-191 auth   ║")
        print("  ║  ERC-8004      On-chain identity + reputation       ║")
        print("  ║  MCP           11 tools, any AI agent can call      ║")
        print("  ║                                                      ║")
        print("  ║  github.com/Vib-UX/Agent-of-Sats                   ║")
        print("  ╚══════════════════════════════════════════════════════╝")
        print(f"{RESET}")


# ── cleanup ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # clean up test DB after demo
    db_path = os.getenv("PERFORMANCE_LOG_DB", "data/performance_log.db")
    try:
        asyncio.run(run_demo())
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
