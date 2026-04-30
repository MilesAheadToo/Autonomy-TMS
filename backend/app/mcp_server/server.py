"""
Autonomy MCP Server — Main Entry Point.

Exposes supply chain intelligence via MCP using FastMCP.
Runs as a sidecar service in Docker alongside the main backend.

Transport: Streamable HTTP on port 8002 (proxied via Nginx at /mcp)
Auth: Bearer token (same JWT as REST API) or API key

Usage:
    python -m app.mcp_server.server          # Production (HTTP)
    python -m app.mcp_server.server --stdio   # Development (STDIO for Claude Code)
"""

import logging
import os
import sys
from typing import Optional

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s [mcp-server] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# FastMCP import
try:
    from fastmcp import FastMCP
    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False
    logger.error("fastmcp not installed — run: pip install fastmcp")

# MCP SDK fallback
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False

if not FASTMCP_AVAILABLE and not MCP_SDK_AVAILABLE:
    logger.error("Neither fastmcp nor mcp SDK available. Install one: pip install fastmcp")
    sys.exit(1)


def create_server() -> "FastMCP":
    """Create and configure the Autonomy MCP server with all tools."""

    mcp = FastMCP(
        "Autonomy Supply Chain Platform",
        description=(
            "Supply chain intelligence service: agent decisions, ATP/CTP, "
            "forecasts, supply plans, scenario simulation, and governance. "
            "Powered by 11 TRM agents, conformal prediction, and causal AI."
        ),
    )

    # Register all tool modules
    from .tools import (
        ad11, atp, decision_stream, forecast, governance, kpi, network,
        override, reasoning,
    )

    decision_stream.register(mcp)
    atp.register(mcp)
    reasoning.register(mcp)
    network.register(mcp)
    kpi.register(mcp)
    forecast.register(mcp)
    governance.register(mcp)
    override.register(mcp)
    # AD-11 Supply×Transport intersection — read-only canonical-ID
    # tools that SCP agents call to inform their planning. See
    # Autonomy-Core CROSS_PLANE_COLLABORATION_ARCHITECTURE.md §5 + §7.
    ad11.register(mcp)

    logger.info("Autonomy MCP server configured with %d tools", len(mcp._tool_manager._tools) if hasattr(mcp, '_tool_manager') else 0)

    return mcp


def main():
    """Entry point for the MCP server."""
    mcp = create_server()

    use_stdio = "--stdio" in sys.argv

    if use_stdio:
        logger.info("Starting Autonomy MCP server (STDIO transport)")
        mcp.run(transport="stdio")
    else:
        host = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_SERVER_PORT", "8002"))
        logger.info("Starting Autonomy MCP server on %s:%d (HTTP transport)", host, port)
        mcp.run(
            transport="http",
            host=host,
            port=port,
        )


if __name__ == "__main__":
    main()
