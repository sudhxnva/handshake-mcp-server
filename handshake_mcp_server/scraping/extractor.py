"""Core extraction engine for Handshake using innerText instead of DOM selectors."""

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Any
from urllib.parse import quote_plus

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from handshake_mcp_server.core.auth import detect_auth_barrier, detect_auth_barrier_quick
from handshake_mcp_server.core.exceptions import (
    AuthenticationError,
    HandshakeScraperException,
)
from handshake_mcp_server.core.utils import (
    detect_rate_limit,
    handle_modal_close,
    scroll_to_bottom,
)
from handshake_mcp_server.scraping.link_metadata import (
    Reference,
    build_references,
    dedupe_references,
)

from .fields import BASE_URL, EMPLOYER_SECTIONS, STUDENT_SECTIONS

logger = logging.getLogger(__name__)

# Delay between page navigations to avoid rate limiting
_NAV_DELAY = 2.0

# Backoff before retrying a rate-limited page
_RATE_LIMIT_RETRY_DELAY = 5.0

# Returned as section text when Handshake rate-limits the page
_RATE_LIMITED_MSG = "[Rate limited] Handshake blocked this section. Try again later or request fewer sections."

# Handshake shows 25 results per page
_PAGE_SIZE = 25

# Sort options for job search
_SORT_BY_MAP = {"date": "posted_date_desc", "relevance": "relevance"}

# Job type filter values (Handshake uses string slugs)
_JOB_TYPE_MAP = {
    "full_time": "full_time",
    "part_time": "part_time",
    "internship": "internship",
    "on_campus": "on_campus",
}

# Work location filter values
_WORK_LOCATION_MAP = {
    "on_site": "on_site",
    "remote": "remote",
    "hybrid": "hybrid",
}

# Noise patterns that indicate Handshake footer/sidebar chrome.
# Everything from the earliest match onwards is stripped.
_NOISE_MARKERS: list[re.Pattern[str]] = [
    # Footer navigation links
    re.compile(r"^(?:Privacy Policy|Terms of Service|Contact Us)\n", re.MULTILINE),
    # Cookie consent banner
    re.compile(r"^(?:We use cookies|Accept Cookies|Cookie Settings)\n", re.MULTILINE),
    # Bottom nav bar (mobile-style)
    re.compile(r"^(?:Home|Jobs|Events|Messages|Profile)\n+(?:Home|Jobs|Events|Messages|Profile)", re.MULTILINE),
]

_NOISE_LINES: list[re.Pattern[str]] = [
    re.compile(r"^(?:Play|Pause|Playback speed|Turn fullscreen on|Fullscreen)$"),
]


@dataclass
class ExtractedSection:
    """Text and compact references extracted from a loaded Handshake section."""

    text: str
    references: list[Reference]
    error: dict[str, Any] | None = None


def strip_handshake_noise(text: str) -> str:
    """Remove Handshake page chrome (footer, sidebar) from innerText."""
    cleaned = _truncate_noise(text)
    return _filter_noise_lines(cleaned)


def _filter_noise_lines(text: str) -> str:
    filtered_lines = [
        line
        for line in text.splitlines()
        if not any(pattern.match(line.strip()) for pattern in _NOISE_LINES)
    ]
    return "\n".join(filtered_lines).strip()


def _truncate_noise(text: str) -> str:
    earliest = len(text)
    for pattern in _NOISE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()
    return text[:earliest].strip()


class HandshakeExtractor:
    """Extracts Handshake page content via navigate-scroll-innerText pattern."""

    def __init__(self, page: Page):
        self._page = page

    async def _raise_if_auth_barrier(
        self,
        url: str,
        *,
        navigation_error: Exception | None = None,
    ) -> None:
        """Raise an auth error when Handshake shows login UI."""
        barrier = await detect_auth_barrier(self._page)
        if not barrier:
            return

        logger.warning("Authentication barrier detected on %s: %s", url, barrier)
        message = (
            "Handshake requires re-authentication. "
            "Run with --login and complete the sign-in flow."
        )
        if navigation_error is not None:
            raise AuthenticationError(message) from navigation_error
        raise AuthenticationError(message)

    async def _goto_with_auth_checks(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
    ) -> None:
        """Navigate to a Handshake page and fail fast on auth barriers."""
        try:
            await self._page.goto(url, wait_until=wait_until, timeout=30000)
        except Exception as exc:
            await self._raise_if_auth_barrier(url, navigation_error=exc)
            raise

        barrier = await detect_auth_barrier_quick(self._page)
        if barrier:
            logger.warning("Authentication barrier detected on %s: %s", url, barrier)
            raise AuthenticationError(
                "Handshake requires re-authentication. "
                "Run with --login and complete the sign-in flow."
            )

    async def extract_page(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Navigate to a URL, scroll to load lazy content, and extract innerText.

        Retries once after a backoff when the page returns only noise.
        """
        try:
            result = await self._extract_page_once(url, section_name)
            if result.text != _RATE_LIMITED_MSG:
                return result

            # Retry once after backoff
            logger.info("Retrying %s after %.0fs backoff", url, _RATE_LIMIT_RETRY_DELAY)
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY)
            return await self._extract_page_once(url, section_name)

        except HandshakeScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract page %s: %s", url, e)
            return ExtractedSection(
                text="",
                references=[],
                error={"error_type": type(e).__name__, "error_message": str(e)},
            )

    async def _extract_page_once(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Single attempt to navigate, scroll, and extract innerText."""
        await self._goto_with_auth_checks(url)
        await detect_rate_limit(self._page)

        # Wait for main content to render
        try:
            await self._page.wait_for_selector("main", timeout=5000)
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)

        # Dismiss any modals blocking content
        await handle_modal_close(self._page)

        # Wait for content to hydrate (Handshake is a React SPA)
        try:
            await self._page.wait_for_function(
                """() => {
                    const main = document.querySelector('main');
                    if (!main) return false;
                    return main.innerText.length > 100;
                }""",
                timeout=8000,
            )
        except PlaywrightTimeoutError:
            logger.debug("Main content did not hydrate within timeout on %s", url)

        # Scroll to trigger lazy loading
        await scroll_to_bottom(self._page, pause_time=0.5, max_scrolls=5)

        # Extract text from main content area
        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]

        if not raw:
            return ExtractedSection(text="", references=[])

        truncated = _truncate_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Page %s returned only Handshake chrome (likely rate-limited)", url
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])

        cleaned = _filter_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=build_references(raw_result["references"], section_name),
        )

    async def scrape_student(self, user_id: str, requested: set[str]) -> dict[str, Any]:
        """Scrape a student profile with configurable sections.

        Returns:
            {url, sections: {name: text}}
        """
        requested = requested | {"main_profile"}
        base_url = f"{BASE_URL}/users/{user_id}"
        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}

        first = True
        for section_name, (suffix, _is_overlay) in STUDENT_SECTIONS.items():
            if section_name not in requested:
                continue

            if not first:
                await asyncio.sleep(_NAV_DELAY)
            first = False

            url = base_url + suffix
            try:
                extracted = await self.extract_page(url, section_name=section_name)

                if extracted.text and extracted.text != _RATE_LIMITED_MSG:
                    sections[section_name] = extracted.text
                    if extracted.references:
                        references[section_name] = extracted.references
                elif extracted.error:
                    section_errors[section_name] = extracted.error
            except HandshakeScraperException:
                raise
            except Exception as e:
                logger.warning("Error scraping section %s: %s", section_name, e)
                section_errors[section_name] = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }

        result: dict[str, Any] = {
            "url": base_url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def scrape_employer(
        self, employer_id: str, requested: set[str]
    ) -> dict[str, Any]:
        """Scrape an employer profile with configurable sections.

        Returns:
            {url, sections: {name: text}}
        """
        requested = requested | {"overview"}
        base_url = f"{BASE_URL}/stu/employers/{employer_id}"
        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}

        first = True
        for section_name, (suffix, _is_overlay) in EMPLOYER_SECTIONS.items():
            if section_name not in requested:
                continue

            if not first:
                await asyncio.sleep(_NAV_DELAY)
            first = False

            url = base_url + suffix
            try:
                extracted = await self.extract_page(url, section_name=section_name)

                if extracted.text and extracted.text != _RATE_LIMITED_MSG:
                    sections[section_name] = extracted.text
                    if extracted.references:
                        references[section_name] = extracted.references
                elif extracted.error:
                    section_errors[section_name] = extracted.error
            except HandshakeScraperException:
                raise
            except Exception as e:
                logger.warning("Error scraping section %s: %s", section_name, e)
                section_errors[section_name] = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }

        result: dict[str, Any] = {
            "url": base_url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def scrape_job(self, job_id: str) -> dict[str, Any]:
        """Scrape a single job posting.

        Returns:
            {url, sections: {name: text}}
        """
        url = f"{BASE_URL}/stu/jobs/{job_id}"
        extracted = await self.extract_page(url, section_name="job_posting")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["job_posting"] = extracted.text
            if extracted.references:
                references["job_posting"] = extracted.references
        elif extracted.error:
            section_errors["job_posting"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def scrape_event(self, event_id: str) -> dict[str, Any]:
        """Scrape a single event.

        Returns:
            {url, sections: {name: text}}
        """
        url = f"{BASE_URL}/stu/events/{event_id}"
        extracted = await self.extract_page(url, section_name="event_details")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["event_details"] = extracted.text
            if extracted.references:
                references["event_details"] = extracted.references
        elif extracted.error:
            section_errors["event_details"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def _extract_job_ids(self) -> list[str]:
        """Extract unique job IDs from job card links on the current page."""
        return await self._page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/stu/jobs/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/stu\\/jobs\\/(\\d+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        ids.push(match[1]);
                    }
                }
                return ids;
            }"""
        )

    async def _extract_employer_ids(self) -> list[str]:
        """Extract unique employer IDs from employer card links on the current page."""
        return await self._page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/stu/employers/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/stu\\/employers\\/(\\d+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        ids.push(match[1]);
                    }
                }
                return ids;
            }"""
        )

    async def _extract_event_ids(self) -> list[str]:
        """Extract unique event IDs from event card links on the current page."""
        return await self._page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/stu/events/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/stu\\/events\\/(\\d+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        ids.push(match[1]);
                    }
                }
                return ids;
            }"""
        )

    @staticmethod
    def _build_job_search_url(
        keywords: str,
        location: str | None = None,
        job_type: str | None = None,
        employment_type: str | None = None,
        sort_by: str | None = None,
        page: int = 1,
        per_page: int = _PAGE_SIZE,
    ) -> str:
        """Build a Handshake job search URL with optional filters."""
        params = f"query={quote_plus(keywords)}&page={page}&per_page={per_page}"
        if location:
            params += f"&location={quote_plus(location)}"
        if job_type:
            mapped = _JOB_TYPE_MAP.get(job_type.strip(), job_type)
            params += f"&job_type={quote_plus(mapped)}"
        if employment_type:
            mapped = _WORK_LOCATION_MAP.get(employment_type.strip(), employment_type)
            params += f"&employment_type={quote_plus(mapped)}"
        if sort_by:
            mapped = _SORT_BY_MAP.get(sort_by.strip(), sort_by)
            params += f"&sort_direction={quote_plus(mapped)}"
        return f"{BASE_URL}/stu/jobs?{params}"

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        job_type: str | None = None,
        employment_type: str | None = None,
        sort_by: str | None = None,
        max_pages: int = 3,
    ) -> dict[str, Any]:
        """Search for jobs with pagination and job ID extraction.

        Args:
            keywords: Search keywords
            location: Optional location filter
            job_type: Filter by job type (full_time, part_time, internship, on_campus)
            employment_type: Filter by work location (on_site, remote, hybrid)
            sort_by: Sort results (date, relevance)
            max_pages: Maximum pages to load (1-10, default 3)

        Returns:
            {url, sections: {search_results: text}, job_ids: [str]}
        """
        base_url = self._build_job_search_url(
            keywords,
            location=location,
            job_type=job_type,
            employment_type=employment_type,
            sort_by=sort_by,
            page=1,
        )

        all_job_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                await asyncio.sleep(_NAV_DELAY)

            url = self._build_job_search_url(
                keywords,
                location=location,
                job_type=job_type,
                employment_type=employment_type,
                sort_by=sort_by,
                page=page_num,
            )

            try:
                extracted = await self._extract_search_page(url, "search_results")

                if not extracted.text or extracted.text == _RATE_LIMITED_MSG:
                    if extracted.error:
                        section_errors["search_results"] = extracted.error
                    break

                page_ids = await self._extract_job_ids()
                new_ids = [jid for jid in page_ids if jid not in seen_ids]

                if not new_ids and page_num > 1:
                    page_texts.append(extracted.text)
                    if extracted.references:
                        page_references.extend(extracted.references)
                    logger.debug("No new job IDs on page %d, stopping", page_num)
                    break

                for jid in new_ids:
                    seen_ids.add(jid)
                    all_job_ids.append(jid)

                page_texts.append(extracted.text)
                if extracted.references:
                    page_references.extend(extracted.references)

            except HandshakeScraperException:
                raise
            except Exception as e:
                logger.warning("Error on search page %d: %s", page_num, e)
                section_errors["search_results"] = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
                break

        result: dict[str, Any] = {
            "url": base_url,
            "sections": {"search_results": "\n---\n".join(page_texts)}
            if page_texts
            else {},
            "job_ids": all_job_ids,
        }
        if page_references:
            result["references"] = {
                "search_results": dedupe_references(page_references, cap=15)
            }
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def search_employers(
        self,
        keywords: str,
        max_pages: int = 2,
    ) -> dict[str, Any]:
        """Search for employers.

        Returns:
            {url, sections: {search_results: text}, employer_ids: [str]}
        """
        base_url = f"{BASE_URL}/stu/employers?query={quote_plus(keywords)}"

        all_employer_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                await asyncio.sleep(_NAV_DELAY)

            url = (
                base_url
                if page_num == 1
                else f"{base_url}&page={page_num}"
            )

            try:
                extracted = await self._extract_search_page(url, "search_results")

                if not extracted.text or extracted.text == _RATE_LIMITED_MSG:
                    if extracted.error:
                        section_errors["search_results"] = extracted.error
                    break

                employer_ids = await self._extract_employer_ids()
                new_ids = [eid for eid in employer_ids if eid not in seen_ids]

                if not new_ids and page_num > 1:
                    break

                for eid in new_ids:
                    seen_ids.add(eid)
                    all_employer_ids.append(eid)

                page_texts.append(extracted.text)
                if extracted.references:
                    page_references.extend(extracted.references)

            except HandshakeScraperException:
                raise
            except Exception as e:
                logger.warning("Error on employer search page %d: %s", page_num, e)
                section_errors["search_results"] = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
                break

        result: dict[str, Any] = {
            "url": base_url,
            "sections": {"search_results": "\n---\n".join(page_texts)}
            if page_texts
            else {},
            "employer_ids": all_employer_ids,
        }
        if page_references:
            result["references"] = {
                "search_results": dedupe_references(page_references, cap=15)
            }
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def search_events(
        self,
        keywords: str,
        max_pages: int = 2,
    ) -> dict[str, Any]:
        """Search for events on Handshake.

        Returns:
            {url, sections: {search_results: text}, event_ids: [str]}
        """
        base_url = f"{BASE_URL}/stu/events?query={quote_plus(keywords)}"

        all_event_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                await asyncio.sleep(_NAV_DELAY)

            url = (
                base_url
                if page_num == 1
                else f"{base_url}&page={page_num}"
            )

            try:
                extracted = await self._extract_search_page(url, "search_results")

                if not extracted.text or extracted.text == _RATE_LIMITED_MSG:
                    if extracted.error:
                        section_errors["search_results"] = extracted.error
                    break

                event_ids = await self._extract_event_ids()
                new_ids = [eid for eid in event_ids if eid not in seen_ids]

                if not new_ids and page_num > 1:
                    break

                for eid in new_ids:
                    seen_ids.add(eid)
                    all_event_ids.append(eid)

                page_texts.append(extracted.text)
                if extracted.references:
                    page_references.extend(extracted.references)

            except HandshakeScraperException:
                raise
            except Exception as e:
                logger.warning("Error on event search page %d: %s", page_num, e)
                section_errors["search_results"] = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }
                break

        result: dict[str, Any] = {
            "url": base_url,
            "sections": {"search_results": "\n---\n".join(page_texts)}
            if page_texts
            else {},
            "event_ids": all_event_ids,
        }
        if page_references:
            result["references"] = {
                "search_results": dedupe_references(page_references, cap=15)
            }
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def _extract_search_page(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Extract innerText from a search page with soft rate-limit retry."""
        try:
            result = await self._extract_search_page_once(url, section_name)
            if result.text != _RATE_LIMITED_MSG:
                return result

            logger.info(
                "Retrying search page %s after %.0fs backoff",
                url,
                _RATE_LIMIT_RETRY_DELAY,
            )
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY)
            return await self._extract_search_page_once(url, section_name)

        except HandshakeScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract search page %s: %s", url, e)
            return ExtractedSection(
                text="",
                references=[],
                error={"error_type": type(e).__name__, "error_message": str(e)},
            )

    async def _extract_search_page_once(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Single attempt to navigate and extract innerText from a search page."""
        await self._goto_with_auth_checks(url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main", timeout=5000)
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)

        await handle_modal_close(self._page)

        # Wait for search results to load
        try:
            await self._page.wait_for_function(
                """() => {
                    const main = document.querySelector('main');
                    if (!main) return false;
                    return main.innerText.length > 100;
                }""",
                timeout=8000,
            )
        except PlaywrightTimeoutError:
            logger.debug("Search results did not appear on %s", url)

        await scroll_to_bottom(self._page, pause_time=0.5, max_scrolls=5)

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]

        if not raw:
            return ExtractedSection(text="", references=[])

        truncated = _truncate_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Search page %s returned only Handshake chrome (likely rate-limited)",
                url,
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])

        cleaned = _filter_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=build_references(raw_result["references"], section_name),
        )

    async def _extract_root_content(
        self,
        selectors: list[str],
    ) -> dict[str, Any]:
        """Extract innerText and raw anchor metadata from the first matching root."""
        result = await self._page.evaluate(
            """({ selectors }) => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const containerSelector = 'section, article, li, div';
                const headingSelector = 'h1, h2, h3';
                const directHeadingSelector = ':scope > h1, :scope > h2, :scope > h3';
                const MAX_HEADING_CONTAINERS = 300;
                const MAX_REFERENCE_ANCHORS = 500;

                const getHeadingText = element => {
                    if (!element) return '';
                    const heading =
                        element.matches && element.matches(headingSelector)
                            ? element
                            : element.querySelector
                              ? element.querySelector(directHeadingSelector)
                              : null;
                    return normalize(heading?.innerText || heading?.textContent);
                };

                const getPreviousHeading = node => {
                    let sibling = node?.previousElementSibling || null;
                    for (let index = 0; sibling && index < 3; index += 1) {
                        const heading = getHeadingText(sibling);
                        if (heading) return heading;
                        sibling = sibling.previousElementSibling;
                    }
                    return '';
                };

                const root = selectors
                    .map(selector => document.querySelector(selector))
                    .find(Boolean);
                const source = root ? 'root' : 'body';
                const container = root || document.body;
                const text = container ? (container.innerText || '').trim() : '';
                const headingMap = new WeakMap();

                const candidateContainers = [
                    container,
                    ...Array.from(container.querySelectorAll(containerSelector)).slice(
                        0, MAX_HEADING_CONTAINERS
                    ),
                ];
                candidateContainers.forEach(node => {
                    const ownHeading = getHeadingText(node);
                    const previousHeading = getPreviousHeading(node);
                    const heading = ownHeading || previousHeading;
                    if (heading) headingMap.set(node, heading);
                });

                const findHeading = element => {
                    let current = element.closest(containerSelector) || container;
                    for (let depth = 0; current && depth < 4; depth += 1) {
                        const heading = headingMap.get(current);
                        if (heading) return heading;
                        if (current === container) break;
                        current = current.parentElement?.closest(containerSelector) || null;
                    }
                    return '';
                };

                const references = Array.from(container.querySelectorAll('a[href]'))
                    .slice(0, MAX_REFERENCE_ANCHORS)
                    .map(anchor => {
                        const rawHref = (anchor.getAttribute('href') || '').trim();
                        if (!rawHref || rawHref === '#') return null;
                        const href = rawHref.startsWith('#')
                            ? rawHref
                            : (anchor.href || rawHref);
                        return {
                            href,
                            text: normalize(anchor.innerText || anchor.textContent),
                            aria_label: normalize(anchor.getAttribute('aria-label')),
                            title: normalize(anchor.getAttribute('title')),
                            heading: findHeading(anchor),
                            in_article: Boolean(anchor.closest('article')),
                            in_nav: Boolean(anchor.closest('nav')),
                            in_footer: Boolean(anchor.closest('footer')),
                        };
                    })
                    .filter(Boolean);

                return { source, text, references };
            }""",
            {"selectors": selectors},
        )
        return result
