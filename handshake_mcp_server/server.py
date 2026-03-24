"""FastMCP server implementation for Handshake integration."""

import logging
from typing import Any, AsyncIterator

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from handshake_mcp_server.authentication import get_authentication_source
from handshake_mcp_server.browser_manager import close_browser
from handshake_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.sequential_tool_middleware import SequentialToolExecutionMiddleware
from handshake_mcp_server.tools.employer import register_employer_tools
from handshake_mcp_server.tools.event import register_event_tools
from handshake_mcp_server.tools.job import register_job_tools
from handshake_mcp_server.tools.student import register_student_tools

logger = logging.getLogger(__name__)


@lifespan
async def browser_lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage browser lifecycle — cleanup on shutdown."""
    logger.info("Handshake MCP Server starting...")
    yield {}
    logger.info("Handshake MCP Server shutting down...")
    await close_browser()


@lifespan
async def auth_lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Validate authentication profile exists at startup."""
    logger.info("Validating Handshake authentication...")
    get_authentication_source()
    yield {}


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with all Handshake tools."""
    mcp = FastMCP(
        "handshake_scraper",
        lifespan=auth_lifespan | browser_lifespan,
        mask_error_details=True,
    )
    mcp.add_middleware(SequentialToolExecutionMiddleware())

    # Register all tools
    register_student_tools(mcp)
    register_employer_tools(mcp)
    register_job_tools(mcp)
    register_event_tools(mcp)

    # Register session management tool
    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Close Session",
        annotations={"destructiveHint": True},
        tags={"session"},
    )
    async def close_session() -> dict[str, Any]:
        """Close the current browser session and clean up resources."""
        try:
            await close_browser()
            return {
                "status": "success",
                "message": "Successfully closed the browser session and cleaned up resources",
            }
        except Exception as e:
            raise_tool_error(e, "close_session")  # NoReturn

    return mcp
