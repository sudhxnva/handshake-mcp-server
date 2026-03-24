"""Handshake event scraping tools."""

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import Depends

from handshake_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from handshake_mcp_server.dependencies import get_extractor
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.scraping import HandshakeExtractor

logger = logging.getLogger(__name__)


def register_event_tools(mcp: FastMCP) -> None:
    """Register all event-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Event Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"event", "scraping"},
    )
    async def get_event_details(
        event_id: str,
        ctx: Context,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Get details for a Handshake event (career fair, info session, workshop, etc.).

        Args:
            event_id: Handshake numeric event ID (e.g., "654321")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract event details.
        """
        try:
            logger.info("Scraping event: %s", event_id)

            await ctx.report_progress(
                progress=0, total=100, message="Starting event scrape"
            )

            result = await extractor.scrape_event(event_id)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "get_event_details")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Events",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"event", "search"},
    )
    async def search_events(
        keywords: str,
        ctx: Context,
        max_pages: int = 2,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Search for upcoming events on Handshake.

        Returns event_ids that can be passed to get_event_details for full information.

        Args:
            keywords: Search keywords (e.g., "career fair", "Google info session")
            ctx: FastMCP context for progress reporting
            max_pages: Maximum number of result pages to load (1-5, default 2)

        Returns:
            Dict with url, sections (name -> raw text), event_ids (list of
            numeric event ID strings usable with get_event_details), and
            optional references.
        """
        try:
            logger.info("Searching events: keywords='%s'", keywords)

            await ctx.report_progress(
                progress=0, total=100, message="Starting event search"
            )

            result = await extractor.search_events(keywords, max_pages=max_pages)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "search_events")  # NoReturn
