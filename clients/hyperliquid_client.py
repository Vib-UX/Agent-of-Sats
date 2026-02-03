"""
Hyperliquid perps client for Agent of Sats.

Wraps the Hyperliquid REST/SDK to provide:
  • BTC perp market info (price, funding)
  • Position queries
  • Order placement (market / limit)
  • Bulk position closure

"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("agent_of_sats.hyperliquid")

# ── Configuration ───────────────────────────────────────────────────────────

MAINNET_API = "https://api.hyperliquid.xyz"
TESTNET_API = "https://api.hyperliquid-testnet.xyz"

BTC_SYMBOL = "BTC"


@dataclass
class HyperliquidConfig:
    private_key: str = ""
    network: str = "testnet"  # "mainnet" | "testnet"
    api_url: str = ""

    def __post_init__(self):
        if not self.private_key:
            self.private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
        if not self.network:
            self.network = os.getenv("HYPERLIQUID_NETWORK", "testnet")
        if not self.api_url:
            self.api_url = (
                MAINNET_API if self.network == "mainnet" else TESTNET_API
            )


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class MarketInfo:
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float  # current 8-hour rate
    open_interest: float
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    size: float  # positive = long, negative = short
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    size: float
    price: float | None
    order_type: str
    status: str
    raw: dict = field(default_factory=dict)


# ── Client ──────────────────────────────────────────────────────────────────


class HyperliquidPerpsClient:
    """
    Thin wrapper around the Hyperliquid Info and Exchange APIs.

    For the hackathon build we use raw HTTP via httpx; a future iteration
    should swap to the official ``hyperliquid-python-sdk`` once auth is
    wired up.
    """

    def __init__(self, config: HyperliquidConfig | None = None):
        self.cfg = config or HyperliquidConfig()
        self._http = httpx.AsyncClient(
            base_url=self.cfg.api_url,
            timeout=15.0,
        )
        self._mock = not self.cfg.private_key
        if self._mock:
            logger.warning(
                "HYPERLIQUID_PRIVATE_KEY not set – running in read-only mock mode"
            )

    # ── public: market data ─────────────────────────────────────────────

    async def get_btc_market_info(self) -> MarketInfo:
        """Fetch BTC perp mark/index prices and current funding rate."""
        if self._mock:
            return self._mock_market_info()

        try:
            # Hyperliquid info endpoint
            resp = await self._http.post(
                "/info",
                json={"type": "metaAndAssetCtxs"},
            )
            resp.raise_for_status()
            data = resp.json()

            # data is [meta, [assetCtx, ...]]
            meta = data[0]
            asset_ctxs = data[1]

            # Find BTC index
            btc_idx = None
            for i, asset in enumerate(meta.get("universe", [])):
                if asset.get("name") == BTC_SYMBOL:
                    btc_idx = i
                    break

            if btc_idx is None or btc_idx >= len(asset_ctxs):
                raise ValueError("BTC not found in Hyperliquid universe")

            ctx = asset_ctxs[btc_idx]
            return MarketInfo(
                symbol=BTC_SYMBOL,
                mark_price=float(ctx.get("markPx", 0)),
                index_price=float(ctx.get("oraclePx", 0)),
                funding_rate=float(ctx.get("funding", 0)),
                open_interest=float(ctx.get("openInterest", 0)),
                raw=ctx,
            )
        except Exception as exc:
            logger.error("Failed to fetch BTC market info: %s", exc)
            raise

    async def get_positions(self, user_address: str | None = None) -> list[Position]:
        """Return open perp positions for the configured wallet."""
        if self._mock:
            return self._mock_positions()

        try:
            # TODO: derive address from private key if user_address not provided
            address = user_address or "0x0000000000000000000000000000000000000000"
            resp = await self._http.post(
                "/info",
                json={"type": "clearinghouseState", "user": address},
            )
            resp.raise_for_status()
            data = resp.json()

            positions: list[Position] = []
            for pos in data.get("assetPositions", []):
                p = pos.get("position", {})
                size = float(p.get("szi", 0))
                if size == 0:
                    continue
                positions.append(
                    Position(
                        symbol=p.get("coin", ""),
                        size=size,
                        entry_price=float(p.get("entryPx", 0)),
                        mark_price=float(p.get("markPx", 0)),  # may not be in this response
                        unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                        leverage=float(p.get("leverage", {}).get("value", 1)),
                        liquidation_price=float(p.get("liquidationPx", 0)) if p.get("liquidationPx") else None,
                        raw=p,
                    )
                )
            return positions
        except Exception as exc:
            logger.error("Failed to get positions: %s", exc)
            raise

    # ── public: trading ─────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None = None,
        order_type: str = "market",
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Place a market or limit order.

        TODO: implement real signing with HYPERLIQUID_PRIVATE_KEY via the SDK.
        """
        if self._mock:
            return self._mock_order(symbol, is_buy, size, price, order_type)

        # TODO: Real order placement using hyperliquid-python-sdk
        # from hyperliquid.exchange import Exchange
        # exchange = Exchange(wallet, base_url)
        # result = exchange.order(symbol, is_buy, size, price, ...)
        raise NotImplementedError(
            "Live order placement requires HYPERLIQUID_PRIVATE_KEY and SDK wiring. "
            "Set the key and implement the Exchange wrapper."
        )

    async def close_all_positions(self, symbol: str) -> list[OrderResult]:
        """Close all open positions for *symbol* with market orders."""
        positions = await self.get_positions()
        results: list[OrderResult] = []
        for pos in positions:
            if pos.symbol != symbol:
                continue
            # Close by reversing direction
            is_buy = pos.size < 0  # short → buy to close
            result = await self.place_order(
                symbol=symbol,
                is_buy=is_buy,
                size=abs(pos.size),
                order_type="market",
                reduce_only=True,
            )
            results.append(result)
        return results

    # ── connectivity check ──────────────────────────────────────────────

    async def is_connected(self) -> bool:
        """Quick health check – can we reach the API?"""
        try:
            resp = await self._http.post(
                "/info",
                json={"type": "meta"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ── cleanup ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()

    # ── mock helpers (dev / demo mode) ──────────────────────────────────

    @staticmethod
    def _mock_market_info() -> MarketInfo:
        return MarketInfo(
            symbol=BTC_SYMBOL,
            mark_price=97_250.50,
            index_price=97_245.00,
            funding_rate=0.0001,  # 0.01 % per 8h
            open_interest=125_000_000.0,
            raw={"mock": True},
        )

    @staticmethod
    def _mock_positions() -> list[Position]:
        return [
            Position(
                symbol=BTC_SYMBOL,
                size=-0.15,
                entry_price=97_100.00,
                mark_price=97_250.50,
                unrealized_pnl=-22.58,
                leverage=3.0,
                raw={"mock": True},
            )
        ]

    @staticmethod
    def _mock_order(
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None,
        order_type: str,
    ) -> OrderResult:
        return OrderResult(
            order_id=f"mock-{int(time.time()*1000)}",
            symbol=symbol,
            side="buy" if is_buy else "sell",
            size=size,
            price=price or 97_250.50,
            order_type=order_type,
            status="filled",
            raw={"mock": True},
        )
