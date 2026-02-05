"""
ERC‑8004 agent registration helpers for Agent of Sats.

Generates the off-chain JSON metadata that can be pinned to IPFS and
referenced by the ERC‑8004 on-chain registry.  Also includes a sketch
of the Solidity interface for an AgentReputationRegistry.
"""

from __future__ import annotations

import json
import os
from typing import Any


def generate_agent_metadata(
    mcp_endpoint: str | None = None,
    moltbook_profile: str | None = None,
    log_url: str | None = None,
    btc_pubkey: str | None = None,
    image_uri: str = "ipfs://TODO",
) -> dict[str, Any]:
    """
    Build the ERC‑8004 agent registration JSON.

    The returned dict is suitable for JSON-serialisation, IPFS pinning,
    and passing to the ``registerAgent(tokenURI)`` contract call.
    """
    mcp_endpoint = mcp_endpoint or os.getenv(
        "ERC8004_MCP_ENDPOINT",
        "https://agent-of-sats.example.com/.well-known/agent.json",
    )
    moltbook_profile = moltbook_profile or os.getenv(
        "MOLTBOOK_AGENT_ID", "agent-of-sats"
    )
    log_url = log_url or "hyper://TODO-or-http://log-endpoint"
    btc_pubkey = btc_pubkey or os.getenv("ERC8004_BTC_PUBKEY", "TODO_BTC_PUBKEY")

    return {
        "name": "Agent of Sats",
        "description": (
            "ERC‑8004 BTC yield agent using Hyperliquid perps "
            "and an off-chain strategy engine."
        ),
        "image": image_uri,
        "services": [
            {
                "type": "mcp",
                "endpoint": mcp_endpoint,
            },
            {
                "type": "moltbook",
                "profile": f"https://moltbook.com/m/{moltbook_profile}",
            },
            {
                "type": "log",
                "url": log_url,
            },
        ],
        "externalKeys": {
            "btc_pubkey": btc_pubkey,
        },
    }


def agent_metadata_json(**kwargs: Any) -> str:
    """Return the registration metadata as a pretty-printed JSON string."""
    return json.dumps(generate_agent_metadata(**kwargs), indent=2)


# ── Solidity interface sketch ───────────────────────────────────────────────

SOLIDITY_INTERFACE_SKETCH = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IAgentReputationRegistry
/// @notice Sketch interface for an ERC‑8004 agent reputation registry.
///         Agents register with a tokenURI (IPFS JSON) and can later submit
///         signed performance snapshots from their BTC key for on-chain
///         reputation accrual.
interface IAgentReputationRegistry {

    /// @notice Emitted when a new agent is registered.
    event AgentRegistered(uint256 indexed agentId, address indexed owner, string tokenURI);

    /// @notice Emitted when a performance snapshot is anchored.
    event SnapshotAnchored(
        uint256 indexed agentId,
        bytes32 snapshotHash,
        uint256 timestamp
    );

    /// @notice Register a new agent.  Returns the assigned agent ID (token ID).
    /// @param tokenURI  IPFS URI pointing to the ERC‑8004 metadata JSON.
    function registerAgent(string calldata tokenURI) external returns (uint256 agentId);

    /// @notice Submit a BTC-key-signed performance snapshot.
    /// @param agentId       The agent's token ID.
    /// @param snapshotHash  keccak256 of the off-chain snapshot payload.
    /// @param btcSignature  Bitcoin message signature over snapshotHash.
    /// @dev   The registry verifies the BTC pubkey from the agent's metadata
    ///        matches the signer of btcSignature.  Full verification of
    ///        Bitcoin signatures on-chain requires a precompile or library
    ///        (e.g., btc-relay pattern).
    function anchorSnapshot(
        uint256 agentId,
        bytes32 snapshotHash,
        bytes calldata btcSignature
    ) external;

    /// @notice Look up agent metadata URI.
    function agentURI(uint256 agentId) external view returns (string memory);

    /// @notice Get the latest anchored snapshot hash for an agent.
    function latestSnapshot(uint256 agentId) external view returns (bytes32, uint256);
}
"""


def print_solidity_interface() -> str:
    """Return the Solidity interface sketch as a string."""
    return SOLIDITY_INTERFACE_SKETCH
