"""Handshake job scraping tools with search and detail extraction."""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.dependencies import Depends
from pydantic import Field

from handshake_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from handshake_mcp_server.dependencies import get_extractor
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.scraping import HandshakeExtractor

logger = logging.getLogger(__name__)


def register_job_tools(mcp: FastMCP) -> None:
    """Register all job-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Job Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job", "scraping"},
    )
    async def get_job_details(
        job_id: str,
        ctx: Context,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Get full job or internship details from Handshake.

        Args:
            job_id: Handshake numeric job ID (e.g., "9876543")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract job details.
        """
        try:
            logger.info("Scraping job: %s", job_id)

            await ctx.report_progress(progress=0, total=100, message="Starting job scrape")

            result = await extractor.scrape_job(job_id)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "get_job_details")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Jobs",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job", "search"},
    )
    async def search_jobs(
        keywords: str,
        ctx: Context,
        location: str | None = None,
        job_type: str | None = None,
        employment_type: str | None = None,
        sort_by: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Search for jobs and internships on Handshake.

        Returns job_ids that can be passed to get_job_details for full information.

        Args:
            keywords: Search keywords (e.g., "software engineer", "data analyst")
            ctx: FastMCP context for progress reporting
            location: Optional location filter (e.g., "San Francisco", "New York")
            job_type: Filter by job type (full_time, part_time, internship, on_campus)
            employment_type: Filter by work location (on_site, remote, hybrid)
            sort_by: Sort results (date, relevance)
            max_pages: Maximum number of result pages to load (1-10, default 3)

        Returns:
            Dict with url, sections (name -> raw text), job_ids (list of
            numeric job ID strings usable with get_job_details), and optional references.
        """
        try:
            logger.info(
                "Searching jobs: keywords='%s', location='%s', max_pages=%d",
                keywords,
                location,
                max_pages,
            )

            await ctx.report_progress(progress=0, total=100, message="Starting job search")

            result = await extractor.search_jobs(
                keywords,
                location=location,
                job_type=job_type,
                employment_type=employment_type,
                sort_by=sort_by,
                max_pages=max_pages,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "search_jobs")  # NoReturn
