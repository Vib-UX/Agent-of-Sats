# Agent of Sats

> **BTC-native strategy engine** exposed as an MCP server, with ERC‑8004 identity, Hyperliquid perps, Li.Fi cross-chain routing, and Moltbook social reputation.

---

## Architecture Overview

**Agent of Sats** is an off-chain BTC strategy engine that trades Hyperliquid BTC perpetuals to capture basis/funding yield. Every decision, trade, and PnL snapshot is recorded in an append-only log (modelled after Hypercore) so that the full history is auditable and streamable.

The engine is exposed as an **MCP (Model Context Protocol) server**, allowing any AI agent or MCP-capable client (Claude Desktop, Cursor, custom agents) to query performance, trigger strategies, and manage risk—all through a standardised tool interface.

**ERC‑8004** provides the on-chain identity and reputation layer. The agent registers its metadata (MCP endpoint, Moltbook profile, log URL, BTC public key) as an ERC‑8004 token. Over time, signed performance snapshots can be anchored into the on-chain reputation registry, creating a verifiable track record without moving strategy logic on-chain.

**Moltbook AI** serves as the social surface where the agent publishes PnL updates and trade signals via EIP-191 wallet authentication, making it discoverable and followable by other agents and humans.

**Li.Fi** is integrated as a cross-chain routing interface — live quotes and route discovery across 30+ EVM chains and 15+ bridges for deposits/withdrawals into the strategy universe.

---

## Live Integration Results

All three external integrations are **live on mainnet** — no mocks, no stubs.

### Hyperliquid — BTC Perps (Mainnet)

```
Address:     0x7623f00fa06A6Cf6fD084F99925557ad5416Ff01
Network:     mainnet
BTC Mark:    $71,343.00
BTC Oracle:  $71,385.00
Funding:     -0.000291% / 8h
Open Int:    18,767 BTC
24h Volume:  $2,047,913,564
```

Uses the official [`hyperliquid-python-sdk`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) — `Info` for market data and account state, `Exchange` for order management.

### Moltbook AI — Social Reputation

```
Agent:       0x2cac89ABf06DbE5d3a059517053B7144074e1CE5
Platform:    moltbookai.net
Auth:        EIP-191 personal_sign
First Post:  m/crypto — "Agent of Sats has entered the chat"
```

Authenticated via Ethereum wallet signing. Posts PnL updates, trade signals, and strategy summaries to submolts.

### Li.Fi — Cross-Chain Routing

```
Quote:       1 USDC from Ethereum → Arbitrum
Bridge:      Across (0xsocket)
Est. Output: 0.975 USDC
Gas Cost:    $0.025
Exec Time:   ~5 seconds
Chains:      30+ EVM chains supported
```

Live quotes from the Li.Fi REST API. No API key required (10 req/s public rate limit).

---

## Project Structure

```
Agent-of-Sats/
├── mcp_server/
│   ├── __init__.py
│   ├── __main__.py            # python -m mcp_server
│   └── server.py              # FastMCP app, tool & resource definitions
├── clients/
│   ├── __init__.py
│   ├── hyperliquid_client.py  # Hyperliquid SDK wrapper (Info + Exchange)
│   ├── moltbook_client.py     # Moltbook AI client (EIP-191 auth)
│   └── lifi_client.py         # Li.Fi REST API client
├── store/
│   ├── __init__.py
│   └── performance_log.py     # Hypercore-like append-only log (SQLite)
├── erc8004/
│   ├── __init__.py
│   └── registration.py        # ERC-8004 metadata & Solidity sketch
├── run_server.py               # Convenience entrypoint
├── requirements.txt
└── README.md
```

---

## Quick Start

### 1. Install dependencies

```bash
cd Agent-of-Sats
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```bash
# ── Hyperliquid ──────────────────────────────────────────
HYPERLIQUID_WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS
HYPERLIQUID_PRIVATE_KEY=0xYOUR_PRIVATE_KEY
HYPERLIQUID_NETWORK=mainnet

# ── Moltbook AI ─────────────────────────────────────────
MOLTBOOK_BASE_URL=https://moltbookai.net
MOLTBOOK_AGENT_PRIVATE_KEY=0xYOUR_ETH_PRIVATE_KEY

# ── Li.Fi ───────────────────────────────────────────────
LIFI_API_URL=https://li.quest/v1
LIFI_API_KEY=

# ── Performance Log ─────────────────────────────────────
PERFORMANCE_LOG_DB=data/performance_log.db

# ── ERC-8004 ────────────────────────────────────────────
ERC8004_BTC_PUBKEY=
ERC8004_MCP_ENDPOINT=https://agent-of-sats.example.com/.well-known/agent.json
```

### 3. Run the MCP server

```bash
# Stdio transport (default, for Claude Desktop / MCP clients):
python run_server.py

# Or as a module:
python -m mcp_server
```

### 4. Configure in Claude Desktop

Add to your Claude Desktop MCP config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agent-of-sats": {
      "command": "python",
      "args": ["/absolute/path/to/Agent-of-Sats/run_server.py"],
      "env": {
        "HYPERLIQUID_WALLET_ADDRESS": "0x7623f00fa06A6Cf6fD084F99925557ad5416Ff01",
        "HYPERLIQUID_NETWORK": "mainnet"
      }
    }
  }
}
```

### 5. Configure in Cursor

Add to your Cursor MCP settings (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agent-of-sats": {
      "command": "python",
      "args": ["/absolute/path/to/Agent-of-Sats/run_server.py"],
      "env": {
        "HYPERLIQUID_WALLET_ADDRESS": "0x7623f00fa06A6Cf6fD084F99925557ad5416Ff01",
        "HYPERLIQUID_NETWORK": "mainnet"
      }
    }
  }
}
```

---

## MCP Tools

### Hyperliquid Trading

| Tool | Description |
|------|-------------|
| `get_status()` | Health check: connectivity, wallet address, network, last PnL timestamp |
| `get_pnl_snapshot(window_24h, window_7d)` | Account value, margin, open positions, cumulative PnL, drawdown |
| `run_basis_strategy(is_long_basis, target_edge_bps, max_leverage)` | Execute BTC basis/funding strategy using live funding rates |
| `close_positions(symbol)` | Close all positions for a symbol via market order |

### Moltbook Social

| Tool | Description |
|------|-------------|
| `share_pnl_to_moltbook(submolt_name)` | Post PnL summary to Moltbook AI with EIP-191 signed auth |

### Li.Fi Cross-Chain

| Tool | Description |
|------|-------------|
| `lifi_get_quote(from_chain, to_chain, amount, token)` | Get a live bridge/swap quote with transaction data |
| `lifi_get_routes(from_chain, to_chain, amount, token)` | Discover all available routes across bridges and DEXs |
| `lifi_check_status(tx_hash, bridge, from_chain, to_chain)` | Track a cross-chain transaction status |
| `lifi_get_chains()` | List all 30+ supported EVM chains |
| `lifi_get_tools()` | List all bridges and DEX aggregators |

### ERC-8004 Identity

| Tool | Description |
|------|-------------|
| `get_erc8004_registration()` | Generate ERC‑8004 agent metadata JSON |

## MCP Resources

| Resource URI | Description |
|-------------|-------------|
| `agent://performance-log` | Last 100 log events (NDJSON) |
| `agent://erc8004-metadata` | ERC‑8004 registration JSON |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `HYPERLIQUID_WALLET_ADDRESS` | Your 0x wallet address — enables market data + account queries |
| `HYPERLIQUID_PRIVATE_KEY` | Wallet private key — enables live trading (order placement) |
| `HYPERLIQUID_NETWORK` | `mainnet` (default) or `testnet` |
| `MOLTBOOK_BASE_URL` | Moltbook API base URL (default: `https://moltbookai.net`) |
| `MOLTBOOK_AGENT_PRIVATE_KEY` | Ethereum private key for EIP-191 wallet auth on Moltbook |
| `LIFI_API_URL` | Li.Fi API base URL (default: `https://li.quest/v1`) |
| `LIFI_API_KEY` | Li.Fi API key — optional, public rate limit is 10 req/s |
| `PERFORMANCE_LOG_DB` | Path to SQLite DB (default: `data/performance_log.db`) |
| `ERC8004_BTC_PUBKEY` | BTC public key for ERC‑8004 metadata |
| `ERC8004_MCP_ENDPOINT` | MCP endpoint URL for ERC‑8004 metadata |

---

## ERC‑8004 Registration JSON

Generated by `get_erc8004_registration()`:

```json
{
  "name": "Agent of Sats",
  "description": "ERC‑8004 BTC yield agent using Hyperliquid perps and an off-chain strategy engine.",
  "image": "ipfs://TODO",
  "services": [
    {
      "type": "mcp",
      "endpoint": "https://agent-of-sats.example.com/.well-known/agent.json"
    },
    {
      "type": "moltbook",
      "profile": "https://moltbookai.net/u/0x2cac89ABf06DbE5d3a059517053B7144074e1CE5"
    },
    {
      "type": "log",
      "url": "hyper://TODO-or-http://log-endpoint"
    }
  ],
  "externalKeys": {
    "btc_pubkey": "TODO_BTC_PUBKEY"
  }
}
```

**To register on-chain:**
1. Pin the JSON to IPFS.
2. Call `registerAgent(ipfs://...)` on the ERC‑8004 registry contract.
3. The agent's on-chain identity now links to its MCP endpoint, Moltbook profile, and performance log.

---

## License

MIT
