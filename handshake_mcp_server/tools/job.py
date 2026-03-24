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
        job_type_ids: list[str] | None = None,
        employment_type_ids: list[str] | None = None,
        education_level_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
        industry_ids: list[str] | None = None,
        job_role_group_ids: list[str] | None = None,
        remuneration_ids: list[str] | None = None,
        salary_type_ids: list[str] | None = None,
        location: str | None = None,
        sort_by: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Search for jobs and internships on Handshake.

        Use get_job_search_filters first to discover valid IDs for all *_ids parameters.
        Work location filtering (remote/hybrid/on_site) is not supported.

        Returns job_ids and a structured jobs list. Pass job_ids to get_job_details
        for full job information.

        Args:
            keywords: Search keywords (e.g., "software engineer", "data analyst")
            ctx: FastMCP context for progress reporting
            job_type_ids: Filter by job type ID(s) (e.g., ["3"] for Internship)
            employment_type_ids: Filter by employment type ID(s) (e.g., ["1"] for Full-Time)
            education_level_ids: Filter by education level ID(s)
            collection_ids: School-specific curation ID(s) (e.g., on-campus employment)
            industry_ids: Filter by industry ID(s)
            job_role_group_ids: Filter by job role group ID(s)
            remuneration_ids: Filter by benefits/remuneration ID(s)
            salary_type_ids: Filter by salary type ID(s) (e.g., ["1"] for Paid)
            location: Optional location hint appended to the URL query string
            sort_by: Sort results: "relevance" (default) or "date"
            max_pages: Maximum number of result pages to load (1-10, default 3)

        Returns:
            Dict with url, sections (name -> raw text), job_ids (list of numeric
            job ID strings usable with get_job_details), and jobs (structured list
            with title, company, salary, job_type, employment_type, locations).
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
                job_type_ids=job_type_ids,
                employment_type_ids=employment_type_ids,
                education_level_ids=education_level_ids,
                collection_ids=collection_ids,
                industry_ids=industry_ids,
                job_role_group_ids=job_role_group_ids,
                remuneration_ids=remuneration_ids,
                salary_type_ids=salary_type_ids,
                location=location,
                sort_by=sort_by,
                max_pages=max_pages,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            raise_tool_error(e, "search_jobs")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Job Search Filters",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"job", "search"},
    )
    async def get_job_search_filters(
        ctx: Context,
        extractor: HandshakeExtractor = Depends(get_extractor),
    ) -> dict[str, Any]:
        """
        Get all available job search filter options for the current user.

        Call this before search_jobs to discover which IDs to use for
        job_type_ids, collection_ids, industry_ids, and other filter params.

        The collections list is school-specific — it shows your institution's
        curated job lists (e.g., on-campus employment, career hub collections).

        Returns:
            Dict with keys: job_types, employment_types, education_levels,
            salary_types, pay_schedules, remunerations, collections,
            industries, job_role_groups. Each value is a list of
            {id, name} dicts (job_types and employment_types also have slug).
            Returns empty dict if the filter API is unavailable.
        """
        try:
            await ctx.report_progress(progress=0, total=100, message="Fetching filter options")
            result = await extractor.get_job_search_filters()
            await ctx.report_progress(progress=100, total=100, message="Complete")
            return result
        except Exception as e:
            raise_tool_error(e, "get_job_search_filters")  # NoReturn
