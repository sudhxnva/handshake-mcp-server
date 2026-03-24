"""Handshake student profile scraping tools."""

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import Depends

from handshake_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from handshake_mcp_server.dependencies import get_extractor
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.scraping import HandshakeExtractor, parse_student_sections

logger = logging.getLogger(__name__)


def register_student_tools(mcp: FastMCP) -> None:
    """Register all student-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Student Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"student", "scraping"},
    )
    async def get_student_profile(
        user_id: str,
        ctx: Context,
        sections: str | None = None,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Get a student's Handshake profile.

        Args:
            user_id: Handshake numeric user ID (e.g., "12345678")
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of sections to scrape.
                The main profile page is always included.
                Available sections: main_profile
                Default (None) scrapes only the main profile page.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text in each section.
        """
        try:
            requested, unknown = parse_student_sections(sections)

            logger.info("Scraping student profile: %s (sections=%s)", user_id, sections)

            await ctx.report_progress(
                progress=0, total=100, message="Starting student profile scrape"
            )

            result = await extractor.scrape_student(user_id, requested)

            if unknown:
                result["unknown_sections"] = unknown

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "get_student_profile")  # NoReturn
