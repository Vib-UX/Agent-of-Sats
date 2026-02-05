"""
Li.Fi cross-chain routing client for Agent of Sats.

Provides quote and route-building interfaces so deposits/withdrawals
from EVM chains into the BTC strategy universe can be designed cleanly.

"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("agent_of_sats.lifi")


@dataclass
class LifiConfig:
    api_url: str = ""
    api_key: str = ""

    def __post_init__(self):
        self.api_url = self.api_url or os.getenv(
            "LIFI_API_URL", "https://li.quest/v1"
        )
        self.api_key = self.api_key or os.getenv("LIFI_API_KEY", "")


class LifiClient:
    """
    Li.Fi cross-chain routing interface.

    Methods:
        get_quote(chain_from, chain_to, token, amount)
        build_route(chain_from, chain_to, token, amount, slippage_bps)
    """

    def __init__(self, config: LifiConfig | None = None):
        self.cfg = config or LifiConfig()
        self._http = httpx.AsyncClient(
            base_url=self.cfg.api_url,
            timeout=15.0,
            headers=(
                {"x-lifi-api-key": self.cfg.api_key} if self.cfg.api_key else {}
            ),
        )
        self._mock = not self.cfg.api_key
        if self._mock:
            logger.warning(
                "LIFI_API_KEY not set – running in mock/stub mode"
            )

    # ── public API ──────────────────────────────────────────────────────

    async def get_quote(
        self,
        chain_from: str,
        chain_to: str,
        token: str,
        amount: float,
    ) -> dict[str, Any]:
        """
        Get a cross-chain swap/bridge quote from Li.Fi.

        Parameters:
            chain_from  – source chain identifier (e.g. "ethereum", "arbitrum")
            chain_to    – destination chain identifier
            token       – token symbol (e.g. "USDC", "WBTC")
            amount      – amount in human-readable units

        Returns a dict with estimated output, fees, route steps, etc.
        """
        if self._mock:
            return self._mock_quote(chain_from, chain_to, token, amount)

        # TODO: Wire to real Li.Fi /quote endpoint
        # https://docs.li.fi/li.fi-api/li.fi-api/requesting-a-quote
        try:
            resp = await self._http.get(
                "/quote",
                params={
                    "fromChain": chain_from,
                    "toChain": chain_to,
                    "fromToken": token,
                    "toToken": token,
                    "fromAmount": str(int(amount * 1e6)),  # assumes 6 decimals
                    "fromAddress": "0x0000000000000000000000000000000000000000",
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Li.Fi get_quote failed: %s", exc)
            raise

    async def build_route(
        self,
        chain_from: str,
        chain_to: str,
        token: str,
        amount: float,
        slippage_bps: int = 50,
    ) -> dict[str, Any]:
        """
        Build a detailed route for a cross-chain transfer.

        Returns step-by-step route info (bridges, DEXs, estimated time).
        """
        if self._mock:
            return self._mock_route(chain_from, chain_to, token, amount, slippage_bps)

        # TODO: Wire to real Li.Fi /routes endpoint
        try:
            resp = await self._http.post(
                "/routes",
                json={
                    "fromChainId": chain_from,
                    "toChainId": chain_to,
                    "fromTokenAddress": token,
                    "toTokenAddress": token,
                    "fromAmount": str(int(amount * 1e6)),
                    "options": {"slippage": slippage_bps / 10_000},
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Li.Fi build_route failed: %s", exc)
            raise

    # ── cleanup ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()

    # ── mock helpers ────────────────────────────────────────────────────

    @staticmethod
    def _mock_quote(
        chain_from: str, chain_to: str, token: str, amount: float
    ) -> dict[str, Any]:
        fee_pct = 0.15  # simulated 15 bps
        output = amount * (1 - fee_pct / 100)
        return {
            "from_chain": chain_from,
            "to_chain": chain_to,
            "token": token,
            "input_amount": amount,
            "estimated_output": round(output, 6),
            "fee_usd": round(amount * fee_pct / 100, 4),
            "estimated_time_seconds": 180,
            "route_summary": f"{chain_from} → bridge → {chain_to}",
            "mock": True,
        }

    @staticmethod
    def _mock_route(
        chain_from: str,
        chain_to: str,
        token: str,
        amount: float,
        slippage_bps: int,
    ) -> dict[str, Any]:
        return {
            "from_chain": chain_from,
            "to_chain": chain_to,
            "token": token,
            "input_amount": amount,
            "slippage_bps": slippage_bps,
            "steps": [
                {
                    "type": "swap",
                    "protocol": "Uniswap V3",
                    "chain": chain_from,
                    "estimated_gas_usd": 2.50,
                },
                {
                    "type": "bridge",
                    "protocol": "Stargate",
                    "from_chain": chain_from,
                    "to_chain": chain_to,
                    "estimated_time_seconds": 120,
                },
            ],
            "total_estimated_time_seconds": 180,
            "mock": True,
        }
