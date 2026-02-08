"""
Hyperliquid perps client for Agent of Sats.

Uses the official ``hyperliquid-python-sdk`` for all market data and
trading operations.

Components:
    Info     – read-only: market data, positions, orders, fills
    Exchange – write: place/cancel orders, set leverage, market open/close

Configuration via env vars:
    HYPERLIQUID_WALLET_ADDRESS – your 0x address (required for read ops)
    HYPERLIQUID_PRIVATE_KEY    – hex private key (required for write ops)
    HYPERLIQUID_NETWORK        – "mainnet" (default) or "testnet"
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from eth_account import Account as EthAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger("agent_of_sats.hyperliquid")

# ── Configuration ───────────────────────────────────────────────────────────

BTC_SYMBOL = "BTC"


@dataclass
class HyperliquidConfig:
    wallet_address: str = ""
    private_key: str = ""
    network: str = ""

    def __post_init__(self):
        self.wallet_address = self.wallet_address or os.getenv(
            "HYPERLIQUID_WALLET_ADDRESS", ""
        )
        self.private_key = self.private_key or os.getenv(
            "HYPERLIQUID_PRIVATE_KEY", ""
        )
        self.network = self.network or os.getenv(
            "HYPERLIQUID_NETWORK", "mainnet"
        )

        # Derive address from private key if address not provided
        if self.private_key and not self.wallet_address:
            key = self.private_key
            if not key.startswith("0x"):
                key = "0x" + key
            self.wallet_address = EthAccount.from_key(key).address

    @property
    def base_url(self) -> str:
        return (
            constants.MAINNET_API_URL
            if self.network == "mainnet"
            else constants.TESTNET_API_URL
        )

    @property
    def can_read(self) -> bool:
        return bool(self.wallet_address)

    @property
    def can_trade(self) -> bool:
        return bool(self.private_key)


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class MarketInfo:
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float  # current 8-hour rate
    open_interest: float
    day_ntl_vlm: float = 0.0
    premium: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    size: float  # positive = long, negative = short
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    margin_used: float = 0.0
    liquidation_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AccountSummary:
    account_value: float
    total_ntl_pos: float
    total_margin_used: float
    withdrawable: float
    positions: list[Position] = field(default_factory=list)
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


@dataclass
class Fill:
    symbol: str
    side: str
    size: float
    price: float
    fee: float
    time: str
    raw: dict = field(default_factory=dict)


# ── Client ──────────────────────────────────────────────────────────────────


class HyperliquidPerpsClient:
    """
    Wrapper around the official Hyperliquid Python SDK.

    Provides:
        - Market data (prices, funding, order book)
        - Account state (positions, margin, P&L)
        - Order management (market open/close, limit, cancel)
        - Leverage management
    """

    def __init__(self, config: HyperliquidConfig | None = None):
        self.cfg = config or HyperliquidConfig()

        # Info client (read-only, always available)
        self._info = Info(self.cfg.base_url, skip_ws=True)

        # Exchange client (write ops, needs private key)
        self._exchange: Exchange | None = None
        if self.cfg.can_trade:
            key = self.cfg.private_key
            if not key.startswith("0x"):
                key = "0x" + key
            wallet = EthAccount.from_key(key)
            self._exchange = Exchange(wallet, self.cfg.base_url)
            logger.info(
                "Hyperliquid client ready (read+write) on %s for %s",
                self.cfg.network,
                self.cfg.wallet_address,
            )
        elif self.cfg.can_read:
            logger.info(
                "Hyperliquid client ready (read-only) on %s for %s",
                self.cfg.network,
                self.cfg.wallet_address,
            )
        else:
            logger.warning(
                "Hyperliquid client has no wallet address — limited to public market data"
            )

        # Cache meta on init for name→asset mapping
        self._meta: dict | None = None

    # ─── Market Data ────────────────────────────────────────────────────

    async def get_btc_market_info(self) -> MarketInfo:
        """Fetch BTC perp mark/index prices and current funding rate."""
        return await self.get_market_info(BTC_SYMBOL)

    async def get_market_info(self, symbol: str = "BTC") -> MarketInfo:
        """Fetch perp market info for any symbol."""
        data = self._info.meta_and_asset_ctxs()
        meta = data[0]
        asset_ctxs = data[1]

        idx = None
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name") == symbol:
                idx = i
                break

        if idx is None or idx >= len(asset_ctxs):
            raise ValueError(f"{symbol} not found in Hyperliquid universe")

        ctx = asset_ctxs[idx]
        mark = float(ctx.get("markPx", 0))
        oracle = float(ctx.get("oraclePx", 0))

        return MarketInfo(
            symbol=symbol,
            mark_price=mark,
            index_price=oracle,
            funding_rate=float(ctx.get("funding", 0)),
            open_interest=float(ctx.get("openInterest", 0)),
            day_ntl_vlm=float(ctx.get("dayNtlVlm", 0)),
            premium=float(ctx.get("premium", 0)),
            raw=ctx,
        )

    async def get_all_mids(self) -> dict[str, float]:
        """Get mid prices for all perp markets."""
        mids = self._info.all_mids()
        return {k: float(v) for k, v in mids.items()}

    async def get_orderbook(self, symbol: str = "BTC") -> dict[str, Any]:
        """Get L2 order book snapshot for a symbol."""
        return self._info.l2_snapshot(symbol)

    # ─── Account / Positions ────────────────────────────────────────────

    async def get_account_summary(self) -> AccountSummary:
        """Get full account state: margin, positions, withdrawable."""
        self._require_address()
        state = self._info.user_state(self.cfg.wallet_address)

        margin = state.get("crossMarginSummary", state.get("marginSummary", {}))
        positions = []

        for ap in state.get("assetPositions", []):
            p = ap.get("position", {})
            sz = float(p.get("szi", 0))
            if sz == 0:
                continue
            positions.append(
                Position(
                    symbol=p.get("coin", ""),
                    size=sz,
                    entry_price=float(p.get("entryPx", 0)),
                    mark_price=float(p.get("markPx", 0)) if p.get("markPx") else 0,
                    unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                    leverage=float(
                        p.get("leverage", {}).get("value", 1)
                        if isinstance(p.get("leverage"), dict)
                        else p.get("leverage", 1)
                    ),
                    margin_used=float(p.get("marginUsed", 0)),
                    liquidation_price=(
                        float(p["liquidationPx"])
                        if p.get("liquidationPx")
                        else None
                    ),
                    raw=p,
                )
            )

        return AccountSummary(
            account_value=float(margin.get("accountValue", 0)),
            total_ntl_pos=float(margin.get("totalNtlPos", 0)),
            total_margin_used=float(margin.get("totalMarginUsed", 0)),
            withdrawable=float(state.get("withdrawable", 0)),
            positions=positions,
            raw=state,
        )

    async def get_positions(self) -> list[Position]:
        """Return open perp positions for the configured wallet."""
        summary = await self.get_account_summary()
        return summary.positions

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Return open orders for the configured wallet."""
        self._require_address()
        return self._info.open_orders(self.cfg.wallet_address)

    async def get_fills(self, limit: int = 50) -> list[Fill]:
        """Return recent fills for the configured wallet."""
        self._require_address()
        raw_fills = self._info.user_fills(self.cfg.wallet_address)
        fills = []
        for f in raw_fills[:limit]:
            fills.append(
                Fill(
                    symbol=f.get("coin", ""),
                    side=f.get("side", ""),
                    size=float(f.get("sz", 0)),
                    price=float(f.get("px", 0)),
                    fee=float(f.get("fee", 0)),
                    time=f.get("time", ""),
                    raw=f,
                )
            )
        return fills

    async def get_funding_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return funding payment history for the configured wallet."""
        self._require_address()
        return self._info.user_funding_history(
            self.cfg.wallet_address, 0, int(time.time() * 1000)
        )[:limit]

    # ─── Trading (requires private key) ─────────────────────────────────

    async def market_open(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        slippage: float = 0.05,
    ) -> OrderResult:
        """
        Open a position with a market order.

        Uses the SDK's ``market_open`` which handles slippage-adjusted
        limit price internally.
        """
        self._require_exchange()

        result = self._exchange.market_open(  # type: ignore
            name=symbol,
            is_buy=is_buy,
            sz=size,
            slippage=slippage,
        )

        return self._parse_order_result(result, symbol, is_buy, size, "market_open")

    async def market_close(
        self,
        symbol: str,
        size: float | None = None,
        slippage: float = 0.05,
    ) -> OrderResult:
        """
        Close a position (or partial) with a market order.

        If *size* is None, closes the entire position.
        """
        self._require_exchange()

        result = self._exchange.market_close(  # type: ignore
            coin=symbol,
            sz=size,
            slippage=slippage,
        )

        return self._parse_order_result(
            result, symbol, None, size or 0, "market_close"
        )

    async def limit_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
    ) -> OrderResult:
        """Place a limit order (GTC)."""
        self._require_exchange()

        order_type = {"limit": {"tif": "Gtc"}}
        result = self._exchange.order(  # type: ignore
            name=symbol,
            is_buy=is_buy,
            sz=size,
            limit_px=price,
            order_type=order_type,
            reduce_only=reduce_only,
        )

        return self._parse_order_result(result, symbol, is_buy, size, "limit")

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an open order by OID."""
        self._require_exchange()
        return self._exchange.cancel(name=symbol, oid=order_id)  # type: ignore

    async def set_leverage(
        self,
        symbol: str,
        leverage: int,
        is_cross: bool = True,
    ) -> dict[str, Any]:
        """Set leverage for a symbol (cross or isolated)."""
        self._require_exchange()
        return self._exchange.update_leverage(  # type: ignore
            leverage=leverage,
            name=symbol,
            is_cross=is_cross,
        )

    async def close_all_positions(self, symbol: str) -> list[OrderResult]:
        """Close all open positions for *symbol* with market orders."""
        positions = await self.get_positions()
        results: list[OrderResult] = []
        for pos in positions:
            if pos.symbol != symbol:
                continue
            result = await self.market_close(symbol=symbol, size=abs(pos.size))
            results.append(result)
        return results

    # ─── Connectivity ───────────────────────────────────────────────────

    async def is_connected(self) -> bool:
        """Quick health check – can we reach the Hyperliquid API?"""
        try:
            self._info.meta()
            return True
        except Exception:
            return False

    # ─── Cleanup ────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Disconnect any websocket connections."""
        try:
            self._info.disconnect_websocket()
        except Exception:
            pass

    # ─── Internal helpers ───────────────────────────────────────────────

    def _require_address(self) -> None:
        if not self.cfg.wallet_address:
            raise RuntimeError(
                "HYPERLIQUID_WALLET_ADDRESS is required for account queries. "
                "Set it in .env or pass it in HyperliquidConfig."
            )

    def _require_exchange(self) -> None:
        if self._exchange is None:
            raise RuntimeError(
                "HYPERLIQUID_PRIVATE_KEY is required for trading operations. "
                "Set it in .env or pass it in HyperliquidConfig."
            )

    @staticmethod
    def _parse_order_result(
        result: Any,
        symbol: str,
        is_buy: bool | None,
        size: float,
        order_type: str,
    ) -> OrderResult:
        """Parse the SDK's order response into our OrderResult dataclass."""
        status_data = result.get("response", result) if isinstance(result, dict) else {}
        data = status_data.get("data", {}) if isinstance(status_data, dict) else {}

        # The SDK returns nested structures — try to extract order info
        statuses = data.get("statuses", []) if isinstance(data, dict) else []
        order_id = ""
        order_status = "unknown"

        if statuses and isinstance(statuses[0], dict):
            filled = statuses[0].get("filled", statuses[0].get("resting", {}))
            if isinstance(filled, dict):
                order_id = str(filled.get("oid", ""))
            elif isinstance(statuses[0], dict) and "error" in statuses[0]:
                order_status = "error"
                order_id = statuses[0].get("error", "")
            else:
                order_id = str(statuses[0])
                order_status = "filled" if "filled" in str(statuses[0]).lower() else "submitted"

        if order_id and order_status == "unknown":
            order_status = "submitted"

        side = "unknown"
        if is_buy is True:
            side = "buy"
        elif is_buy is False:
            side = "sell"

        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=side,
            size=size,
            price=None,
            order_type=order_type,
            status=order_status,
            raw=result if isinstance(result, dict) else {"raw": str(result)},
        )
