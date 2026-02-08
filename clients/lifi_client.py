"""
Li.Fi cross-chain liquidity aggregation client for Agent of Sats.

Li.Fi aggregates 27+ bridges, 31+ DEXs, and intent-based solvers across
58+ blockchains.  A single ``GET /quote`` call returns an optimally-routed
transaction ready for signing.

Base URL : https://li.quest/v1
Auth     : Optional API key via ``x-lifi-api-key`` header (higher rate limits).
           The public API works at 10 req/s per IP without a key.
Docs     : https://docs.li.fi

This client is always "live" — it hits the real Li.Fi REST API.
If the API is unreachable, methods raise so the caller can handle gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("agent_of_sats.lifi")

# ── Well-known chain IDs ────────────────────────────────────────────────────

CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "optimism": 10,
    "op": 10,
    "bsc": 56,
    "polygon": 137,
    "matic": 137,
    "fantom": 250,
    "arbitrum": 42161,
    "arb": 42161,
    "avalanche": 43114,
    "avax": 43114,
    "base": 8453,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324,
    "mantle": 5000,
    "blast": 81457,
    "mode": 34443,
    "gnosis": 100,
    "celo": 42220,
    "solana": 1151111081099710,
}

# ── Well-known token addresses (by chain ID) ───────────────────────────────

USDC_ADDRESSES: dict[int, str] = {
    1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",      # Ethereum
    10: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",      # Optimism
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",   # Arbitrum
    8453: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",    # Base
    137: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",     # Polygon
    56: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",      # BSC
    43114: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",   # Avalanche
}

NATIVE_TOKEN = "0x0000000000000000000000000000000000000000"

# Token symbol shortcuts → address resolver
TOKEN_SYMBOLS: dict[str, str] = {
    "ETH": NATIVE_TOKEN,
    "MATIC": NATIVE_TOKEN,
    "BNB": NATIVE_TOKEN,
    "AVAX": NATIVE_TOKEN,
}

# ── Configuration ───────────────────────────────────────────────────────────


@dataclass
class LifiConfig:
    api_url: str = ""
    api_key: str = ""
    integrator: str = "agent-of-sats"

    def __post_init__(self):
        self.api_url = self.api_url or os.getenv(
            "LIFI_API_URL", "https://li.quest/v1"
        )
        self.api_key = self.api_key or os.getenv("LIFI_API_KEY", "")


# ── Helpers ─────────────────────────────────────────────────────────────────


def resolve_chain_id(chain: str | int) -> int:
    """Resolve a chain name or ID to a numeric chain ID."""
    if isinstance(chain, int):
        return chain
    if chain.isdigit():
        return int(chain)
    key = chain.lower().strip()
    if key in CHAIN_IDS:
        return CHAIN_IDS[key]
    raise ValueError(
        f"Unknown chain '{chain}'. Use a numeric chain ID or one of: "
        f"{', '.join(sorted(CHAIN_IDS.keys()))}"
    )


def resolve_token(symbol_or_address: str, chain_id: int | None = None) -> str:
    """Resolve a token symbol or address. Symbols like 'USDC' are mapped to
    chain-specific addresses; raw 0x addresses pass through unchanged."""
    if symbol_or_address.startswith("0x") and len(symbol_or_address) == 42:
        return symbol_or_address

    upper = symbol_or_address.upper().strip()

    # Native token aliases
    if upper in TOKEN_SYMBOLS:
        return TOKEN_SYMBOLS[upper]

    # USDC has chain-specific addresses
    if upper == "USDC" and chain_id and chain_id in USDC_ADDRESSES:
        return USDC_ADDRESSES[chain_id]

    # Li.Fi also accepts symbol strings directly in the quote endpoint
    return upper


# ── Client ──────────────────────────────────────────────────────────────────


class LifiClient:
    """
    Li.Fi REST API client for cross-chain quotes, routing, status tracking,
    and chain/token discovery.

    All methods hit the live Li.Fi API (https://li.quest/v1).
    No API key required — public rate limit is 10 req/s per IP.
    Set ``LIFI_API_KEY`` env var for higher limits.
    """

    def __init__(self, config: LifiConfig | None = None):
        self.cfg = config or LifiConfig()
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.cfg.api_key:
            headers["x-lifi-api-key"] = self.cfg.api_key
            logger.info("Li.Fi client initialised with API key")
        else:
            logger.info(
                "Li.Fi client initialised (public rate limit, no API key)"
            )
        self._http = httpx.AsyncClient(
            base_url=self.cfg.api_url,
            timeout=20.0,
            headers=headers,
        )

    # ─── 1. GET /quote — Primary endpoint ───────────────────────────────

    async def get_quote(
        self,
        from_chain: str | int,
        to_chain: str | int,
        from_token: str,
        to_token: str,
        from_amount: str | int,
        from_address: str,
        slippage: float = 0.005,
        allow_bridges: list[str] | None = None,
        deny_bridges: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Get a cross-chain or same-chain transfer quote.

        Returns the optimal route with a ``transactionRequest`` object
        ready for signing and broadcasting.

        Parameters:
            from_chain    – source chain name or ID (e.g. "ethereum", 1)
            to_chain      – destination chain name or ID
            from_token    – source token address or symbol (e.g. "USDC", "0x...")
            to_token      – destination token address or symbol
            from_amount   – amount in smallest unit (wei / satoshi / etc.)
            from_address  – sender wallet address (0x...)
            slippage      – max slippage as decimal (default 0.005 = 0.5%)
            allow_bridges – optional list of bridge keys to prefer
            deny_bridges  – optional list of bridge keys to exclude
        """
        fc = resolve_chain_id(from_chain)
        tc = resolve_chain_id(to_chain)

        params: dict[str, Any] = {
            "fromChain": fc,
            "toChain": tc,
            "fromToken": resolve_token(from_token, fc),
            "toToken": resolve_token(to_token, tc),
            "fromAmount": str(from_amount),
            "fromAddress": from_address,
            "slippage": slippage,
            "integrator": self.cfg.integrator,
        }
        if allow_bridges:
            params["allowBridges"] = ",".join(allow_bridges)
        if deny_bridges:
            params["denyBridges"] = ",".join(deny_bridges)

        resp = await self._request("GET", "/quote", params=params)
        return resp

    # ─── 2. POST /advanced/routes — Multiple route options ──────────────

    async def get_routes(
        self,
        from_chain: str | int,
        to_chain: str | int,
        from_token: str,
        to_token: str,
        from_amount: str | int,
        from_address: str,
        slippage: float = 0.005,
    ) -> dict[str, Any]:
        """
        Get multiple route options for a transfer (compare bridges / DEXs).

        Returns an array of routes sorted by best output.
        """
        fc = resolve_chain_id(from_chain)
        tc = resolve_chain_id(to_chain)

        body = {
            "fromChainId": fc,
            "toChainId": tc,
            "fromTokenAddress": resolve_token(from_token, fc),
            "toTokenAddress": resolve_token(to_token, tc),
            "fromAmount": str(from_amount),
            "fromAddress": from_address,
            "options": {
                "slippage": slippage,
                "integrator": self.cfg.integrator,
            },
        }

        resp = await self._request("POST", "/advanced/routes", json=body)
        return resp

    # ─── 3. GET /status — Track transfer status ────────────────────────

    async def get_status(
        self,
        tx_hash: str,
        bridge: str | None = None,
        from_chain: str | int | None = None,
        to_chain: str | int | None = None,
    ) -> dict[str, Any]:
        """
        Check the status of a cross-chain transfer.

        Status values:
            NOT_FOUND → tx not indexed yet (retry)
            PENDING   → in progress, keep polling
            DONE      → completed (check substatus: COMPLETED, PARTIAL, REFUNDED)
            FAILED    → failed, check error

        Recommended polling interval: 10–30 seconds.
        """
        params: dict[str, Any] = {"txHash": tx_hash}
        if bridge:
            params["bridge"] = bridge
        if from_chain:
            params["fromChain"] = resolve_chain_id(from_chain)
        if to_chain:
            params["toChain"] = resolve_chain_id(to_chain)

        resp = await self._request("GET", "/status", params=params)
        return resp

    async def poll_status(
        self,
        tx_hash: str,
        bridge: str | None = None,
        from_chain: str | int | None = None,
        to_chain: str | int | None = None,
        interval: float = 15.0,
        max_polls: int = 40,
    ) -> dict[str, Any]:
        """
        Poll transfer status until terminal state (DONE or FAILED).

        Returns the final status dict. Raises TimeoutError if max_polls exceeded.
        """
        for i in range(max_polls):
            status = await self.get_status(tx_hash, bridge, from_chain, to_chain)
            state = status.get("status", "UNKNOWN")
            logger.info(
                "Li.Fi status poll %d/%d for %s: %s",
                i + 1, max_polls, tx_hash[:10], state,
            )
            if state in ("DONE", "FAILED"):
                return status
            await asyncio.sleep(interval)

        raise TimeoutError(
            f"Transfer {tx_hash} still {state} after {max_polls} polls"
        )

    # ─── 4. GET /chains — List supported chains ────────────────────────

    async def get_chains(
        self, chain_types: str = "EVM"
    ) -> dict[str, Any]:
        """
        List all supported chains.

        Parameters:
            chain_types – filter by type: "EVM", "SVM" (Solana), "UTXO" (Bitcoin)
        """
        params: dict[str, Any] = {}
        if chain_types:
            params["chainTypes"] = chain_types
        resp = await self._request("GET", "/chains", params=params)
        return resp

    # ─── 5. GET /tokens — List supported tokens ────────────────────────

    async def get_tokens(
        self, chains: list[int | str] | None = None
    ) -> dict[str, Any]:
        """
        List supported tokens, optionally filtered by chain IDs.

        Returns a map of ``chainId → [token, ...]``.
        """
        params: dict[str, Any] = {}
        if chains:
            chain_ids = [str(resolve_chain_id(c)) for c in chains]
            params["chains"] = ",".join(chain_ids)
        resp = await self._request("GET", "/tokens", params=params)
        return resp

    # ─── 6. GET /tools — List bridges and DEXs ─────────────────────────

    async def get_tools(self) -> dict[str, Any]:
        """
        List available bridges and exchanges.

        Returns ``{bridges: [...], exchanges: [...]}``.
        """
        resp = await self._request("GET", "/tools")
        return resp

    # ─── 7. GET /connections — Possible transfer connections ────────────

    async def get_connections(
        self,
        from_chain: str | int | None = None,
        to_chain: str | int | None = None,
        from_token: str | None = None,
        to_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Get possible token transfer connections between chains.
        """
        params: dict[str, Any] = {}
        if from_chain:
            params["fromChain"] = resolve_chain_id(from_chain)
        if to_chain:
            params["toChain"] = resolve_chain_id(to_chain)
        if from_token:
            fc = resolve_chain_id(from_chain) if from_chain else None
            params["fromToken"] = resolve_token(from_token, fc)
        if to_token:
            tc = resolve_chain_id(to_chain) if to_chain else None
            params["toToken"] = resolve_token(to_token, tc)
        resp = await self._request("GET", "/connections", params=params)
        return resp

    # ─── 8. GET /gas — Gas prices and suggestions ──────────────────────

    async def get_gas_prices(self) -> dict[str, Any]:
        """Get current gas prices for all supported chains."""
        return await self._request("GET", "/gas/prices")

    async def get_gas_suggestion(self, chain: str | int) -> dict[str, Any]:
        """Get gas suggestion for a specific chain."""
        cid = resolve_chain_id(chain)
        return await self._request("GET", f"/gas/suggestion/{cid}")

    # ─── Connectivity check ─────────────────────────────────────────────

    async def is_connected(self) -> bool:
        """Quick health check – can we reach Li.Fi?"""
        try:
            resp = await self._http.get("/chains", params={"chainTypes": "EVM"})
            return resp.status_code == 200
        except Exception:
            return False

    # ─── Cleanup ────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()

    # ─── Internal request helper ────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make a request to the Li.Fi API with error handling.

        Handles:
            - 429 rate limit → log warning
            - 4xx/5xx → raise with detail
        """
        try:
            resp = await self._http.request(
                method, path, params=params, json=json
            )
            if resp.status_code == 429:
                logger.warning(
                    "Li.Fi rate limit hit on %s %s — back off and retry",
                    method, path,
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            logger.error(
                "Li.Fi %s %s → HTTP %s: %s",
                method, path, exc.response.status_code, body,
            )
            raise
        except Exception as exc:
            logger.error("Li.Fi %s %s failed: %s", method, path, exc)
            raise
