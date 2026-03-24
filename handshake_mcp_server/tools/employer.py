"""Handshake employer profile scraping tools."""

import logging
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import Depends

from handshake_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from handshake_mcp_server.dependencies import get_extractor
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.scraping import HandshakeExtractor, parse_employer_sections

logger = logging.getLogger(__name__)


def register_employer_tools(mcp: FastMCP) -> None:
    """Register all employer-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Employer Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"employer", "scraping"},
    )
    async def get_employer_profile(
        employer_id: str,
        ctx: Context,
        sections: str | None = None,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Get an employer's Handshake profile.

        Args:
            employer_id: Handshake numeric employer ID (e.g., "123456")
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of sections to scrape.
                The overview is always included.
                Available sections: overview, jobs, posts
                Examples: "jobs", "overview,posts", "jobs,posts"
                Default (None) scrapes only the overview.

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text in each section.
        """
        try:
            requested, unknown = parse_employer_sections(sections)

            logger.info("Scraping employer profile: %s (sections=%s)", employer_id, sections)

            await ctx.report_progress(
                progress=0, total=100, message="Starting employer profile scrape"
            )

            result = await extractor.scrape_employer(employer_id, requested)

            if unknown:
                result["unknown_sections"] = unknown

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "get_employer_profile")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Employers",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"employer", "search"},
    )
    async def search_employers(
        keywords: str,
        ctx: Context,
        max_pages: int = 2,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Search for employers on Handshake.

        Args:
            keywords: Search keywords (e.g., "Google", "fintech startup")
            ctx: FastMCP context for progress reporting
            max_pages: Maximum number of result pages to load (1-5, default 2)

        Returns:
            Dict with url, sections (name -> raw text), employer_ids (list of
            numeric employer ID strings usable with get_employer_profile), and
            optional references.
        """
        try:
            logger.info("Searching employers: keywords='%s'", keywords)

            await ctx.report_progress(progress=0, total=100, message="Starting employer search")

            result = await extractor.search_employers(keywords, max_pages=max_pages)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "search_employers")  # NoReturn
