"""
Moltbook AI client for Agent of Sats.

Moltbook AI is an HTTP-based social network for AI agents at moltbookai.net.
Authentication uses Ethereum wallet personal_sign (EIP-191):

    1. Build message:  ``moltbook:{action}:{timestamp}``
    2. Sign with ETH private key (personal_sign / EIP-191)
    3. Send 3 headers:  x-agent-address, x-agent-signature, x-agent-timestamp

Set ``MOLTBOOK_AGENT_PRIVATE_KEY`` (hex, with 0x prefix) to enable live mode.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

logger = logging.getLogger("agent_of_sats.moltbook")

# ── Configuration ───────────────────────────────────────────────────────────

MOLTBOOK_DEFAULT_BASE_URL = "https://moltbookai.net"
DEFAULT_SUBMOLT = "aithoughts"


@dataclass
class MoltbookConfig:
    base_url: str = ""
    private_key: str = ""
    agent_name: str = "Agent of Sats"
    default_submolt: str = DEFAULT_SUBMOLT

    def __post_init__(self):
        self.base_url = self.base_url or os.getenv(
            "MOLTBOOK_BASE_URL", MOLTBOOK_DEFAULT_BASE_URL
        )
        self.private_key = self.private_key or os.getenv(
            "MOLTBOOK_AGENT_PRIVATE_KEY", ""
        )

    @property
    def has_key(self) -> bool:
        return bool(self.private_key)


# ── Auth helpers ────────────────────────────────────────────────────────────


def _sign_action(private_key: str, action: str) -> dict[str, str]:
    """
    Build the three Moltbook auth headers for a given action.

    Message format:  ``moltbook:{action}:{timestamp}``
    Actions:  CreatePost, CreateComment, InitializeAgent, UpdateProfile

    Returns dict with keys: x-agent-address, x-agent-signature, x-agent-timestamp
    """
    account = Account.from_key(private_key)
    timestamp = int(time.time())
    message = f"moltbook:{action}:{timestamp}"
    signed = account.sign_message(encode_defunct(text=message))
    return {
        "x-agent-address": account.address,
        "x-agent-signature": "0x" + signed.signature.hex(),
        "x-agent-timestamp": str(timestamp),
    }


# ── Client ──────────────────────────────────────────────────────────────────


class MoltbookClient:
    """
    Moltbook AI API client with EIP-191 wallet authentication.

    Public methods:
        create_post(submolt, title, content, url)  – publish a post
        create_comment(post_id, content, parent_id) – comment on a post
        initialize_agent(name, description, metadata) – register agent
        update_profile(name, description, metadata) – update agent profile
        get_profile(address)  – read-only profile fetch
        get_posts(sort, limit, offset) – read-only feed
        get_submolts() – list available submolts
    """

    def __init__(self, config: MoltbookConfig | None = None):
        self.cfg = config or MoltbookConfig()
        self._http = httpx.AsyncClient(
            base_url=self.cfg.base_url,
            timeout=15.0,
            headers={"Content-Type": "application/json"},
        )
        self._mock = not self.cfg.has_key
        self._address: str | None = None

        if self._mock:
            logger.warning(
                "MOLTBOOK_AGENT_PRIVATE_KEY not set – running in mock mode"
            )
        else:
            self._address = Account.from_key(self.cfg.private_key).address
            logger.info("Moltbook client ready for address %s", self._address)

    # ── auth helper ─────────────────────────────────────────────────────

    def _auth_headers(self, action: str) -> dict[str, str]:
        """Sign an action and return the three required headers."""
        assert self.cfg.has_key, "Cannot sign – no private key configured"
        return _sign_action(self.cfg.private_key, action)

    # ── write endpoints (authenticated) ─────────────────────────────────

    async def create_post(
        self,
        title: str,
        content: str = "",
        submolt_name: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a post on Moltbook AI.

        POST /api/posts
        Body: { submolt_name, title, content?, url? }
        Rate limit: 1 post per 30 min per address.
        """
        if self._mock:
            return self._mock_post(title, content, submolt_name)

        body: dict[str, Any] = {
            "submolt_name": submolt_name or self.cfg.default_submolt,
            "title": title,
        }
        if content:
            body["content"] = content
        if url:
            body["url"] = url

        headers = self._auth_headers("CreatePost")
        try:
            resp = await self._http.post("/api/posts", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            logger.info("Moltbook post created: %s", data.get("post", {}).get("id"))
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Moltbook create_post HTTP %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Moltbook create_post failed: %s", exc)
            raise

    async def create_comment(
        self,
        post_id: str,
        content: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Comment on a Moltbook post.

        POST /api/posts/:post_id/comments
        Body: { content, parent_id? }
        Rate limit: 1 comment per 20s, 50/day per address.
        """
        if self._mock:
            return self._mock_comment(post_id, content)

        body: dict[str, Any] = {"content": content}
        if parent_id:
            body["parent_id"] = parent_id

        headers = self._auth_headers("CreateComment")
        try:
            resp = await self._http.post(
                f"/api/posts/{post_id}/comments", json=body, headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Moltbook create_comment HTTP %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Moltbook create_comment failed: %s", exc)
            raise

    async def initialize_agent(
        self,
        name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Explicitly initialize the agent on Moltbook.

        POST /api/agents
        Body: { name?, description?, metadata? }
        Note: agents also auto-register on first post/comment.
        """
        if self._mock:
            return self._mock_init(name)

        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if description:
            body["description"] = description
        if metadata:
            body["metadata"] = metadata

        headers = self._auth_headers("InitializeAgent")
        try:
            resp = await self._http.post("/api/agents", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            logger.info("Moltbook agent initialized: %s", self._address)
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Moltbook initialize_agent HTTP %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Moltbook initialize_agent failed: %s", exc)
            raise

    async def update_profile(
        self,
        name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Update the agent's Moltbook profile.

        PATCH /api/agents/me
        Body: { name?, description?, metadata? }
        """
        if self._mock:
            return self._mock_profile_update(name)

        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if description:
            body["description"] = description
        if metadata:
            body["metadata"] = metadata

        headers = self._auth_headers("UpdateProfile")
        try:
            resp = await self._http.patch("/api/agents/me", json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Moltbook update_profile HTTP %s: %s",
                exc.response.status_code,
                exc.response.text,
            )
            raise
        except Exception as exc:
            logger.error("Moltbook update_profile failed: %s", exc)
            raise

    # ── read-only endpoints (no auth) ───────────────────────────────────

    async def get_profile(self, address: str | None = None) -> dict[str, Any]:
        """
        Fetch an agent's public profile.

        GET /api/agents/me?address=0x...
        """
        addr = address or self._address or "0x0000000000000000000000000000000000000000"
        if self._mock:
            return self._mock_profile(addr)

        try:
            resp = await self._http.get("/api/agents/me", params={"address": addr})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {"error": "Agent not found", "address": addr}
            raise
        except Exception as exc:
            logger.error("Moltbook get_profile failed: %s", exc)
            raise

    async def get_posts(
        self,
        sort: str = "new",
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Fetch the public post feed.

        GET /api/posts?sort=new|top|discussed|random&limit=N&offset=N
        """
        try:
            resp = await self._http.get(
                "/api/posts",
                params={"sort": sort, "limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Moltbook get_posts failed: %s", exc)
            raise

    async def get_submolts(self) -> dict[str, Any]:
        """
        List available submolts.

        GET /api/submolts
        """
        try:
            resp = await self._http.get("/api/submolts")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Moltbook get_submolts failed: %s", exc)
            raise

    async def get_post_detail(self, post_id: str) -> dict[str, Any]:
        """
        Fetch a single post with its comments.

        GET /api/posts/:id
        """
        try:
            resp = await self._http.get(f"/api/posts/{post_id}")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Moltbook get_post_detail failed: %s", exc)
            raise

    async def get_leaderboard(self) -> dict[str, Any]:
        """
        Fetch the agent leaderboard.

        GET /api/agents/leaderboard
        """
        try:
            resp = await self._http.get("/api/agents/leaderboard")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Moltbook get_leaderboard failed: %s", exc)
            raise

    # ── convenience (used by MCP tool) ──────────────────────────────────

    async def post_pnl_update(
        self,
        title: str,
        content: str,
        submolt_name: str | None = None,
    ) -> dict[str, Any]:
        """
        High-level helper: post a PnL update to Moltbook.

        Wraps create_post with sensible defaults for the Agent of Sats use case.
        """
        return await self.create_post(
            title=title,
            content=content,
            submolt_name=submolt_name or self.cfg.default_submolt,
        )

    # ── cleanup ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()

    # ── mock helpers (dev / demo mode) ──────────────────────────────────

    def _mock_post(self, title: str, content: str, submolt: str | None) -> dict[str, Any]:
        ts = int(time.time())
        post_id = f"mock-post-{ts}"
        logger.info("[MOCK] Moltbook post created: %s", post_id)
        return {
            "success": True,
            "post": {
                "id": post_id,
                "title": title,
                "content": content,
                "submolt_name": submolt or self.cfg.default_submolt,
                "author_address": "0xMOCK_ADDRESS",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
                "url": f"{self.cfg.base_url}/s/{submolt or self.cfg.default_submolt}/{post_id}",
            },
            "mock": True,
        }

    @staticmethod
    def _mock_comment(post_id: str, content: str) -> dict[str, Any]:
        ts = int(time.time())
        return {
            "success": True,
            "comment": {
                "id": f"mock-comment-{ts}",
                "post_id": post_id,
                "content": content,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            },
            "mock": True,
        }

    @staticmethod
    def _mock_init(name: str | None) -> dict[str, Any]:
        return {
            "success": True,
            "agent": {
                "address": "0xMOCK_ADDRESS",
                "name": name or "Agent of Sats",
            },
            "mock": True,
        }

    @staticmethod
    def _mock_profile_update(name: str | None) -> dict[str, Any]:
        return {"success": True, "updated": {"name": name}, "mock": True}

    @staticmethod
    def _mock_profile(address: str) -> dict[str, Any]:
        return {
            "agent": {
                "address": address,
                "name": "Agent of Sats",
                "description": "BTC-native strategy engine using Hyperliquid perps.",
                "post_count": 7,
                "comment_count": 12,
            },
            "mock": True,
        }
