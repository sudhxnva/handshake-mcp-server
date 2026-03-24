"""Core extraction engine for Handshake using innerText instead of DOM selectors."""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from patchright.async_api import Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from handshake_mcp_server.core.auth import detect_auth_barrier, detect_auth_barrier_quick
from handshake_mcp_server.core.exceptions import (
    AuthenticationError,
    HandshakeScraperException,
    RateLimitError,
)
from handshake_mcp_server.core.utils import (
    detect_rate_limit,
    handle_modal_close,
    scroll_to_bottom,
    wait_for_cf_challenge,
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
_RATE_LIMITED_MSG = (
    "[Rate limited] Handshake blocked this section. Try again later or request fewer sections."
)

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
    re.compile(
        r"^(?:Home|Jobs|Events|Messages|Profile)\n+(?:Home|Jobs|Events|Messages|Profile)",
        re.MULTILINE,
    ),
    # Cloudflare bot challenge page — Patchright resolves these but as a fallback,
    # strip if the challenge text somehow ends up in the extracted content.
    re.compile(
        r"(?:Performing security verification|Please wait while we verify"
        r"|Just a moment|Checking your browser)",
        re.IGNORECASE,
    ),
]

_NOISE_LINES: list[re.Pattern[str]] = [
    re.compile(r"^(?:Play|Pause|Playback speed|Turn fullscreen on|Fullscreen)$"),
]

# GraphQL query constants — all live at module level, only extractor.py knows this endpoint
JOB_DETAILS_QUERY = """
query GetJobDetails($id: ID!) {
  job(id: $id) {
    id
    title
    description
    hybrid
    remote
    onSite
    startDate
    endDate
    expirationDate
    createdAt
    employer {
      id
      name
      industry { name }
    }
    salaryType { behaviorIdentifier }
    salaryRange {
      min
      max
      paySchedule { behaviorIdentifier }
    }
    locations { city state }
    jobType { behaviorIdentifier }
    employmentType { behaviorIdentifier }
    studentScreen {
      workAuthRequired
      acceptsOptCandidates
      acceptsCptCandidates
      willingToSponsorCandidate
    }
    jobApplySetting { externalUrl }
  }
}
"""

JOB_SEARCH_QUERY = """
query JobSearch($first: Int!, $after: String!, $input: JobSearchInput!) {
  jobSearch(first: $first, after: $after, input: $input) {
    edges {
      node {
        job {
          id
          title
          employer { name }
          jobType { behaviorIdentifier }
          employmentType { behaviorIdentifier }
          salaryType { behaviorIdentifier }
          salaryRange {
            min
            max
            paySchedule { behaviorIdentifier }
          }
          locations { city state }
        }
      }
    }
  }
}
"""

FILTERS_QUERY = """
query JobSearchInitialFilterValues(
  $includeJobTypes: Boolean!
  $includeEmploymentTypes: Boolean!
  $includeEducationLevels: Boolean!
  $includeSalaryTypes: Boolean!
  $includePaySchedules: Boolean!
  $includeRemunerations: Boolean!
  $includeIndustries: Boolean!
  $includeJobRoleGroups: Boolean!
) {
  jobTypes @include(if: $includeJobTypes) {
    id
    name
    behaviorIdentifier
  }
  employmentTypes @include(if: $includeEmploymentTypes) {
    id
    name
    behaviorIdentifier
  }
  educationLevels @include(if: $includeEducationLevels) {
    id
    name
  }
  salaryTypes @include(if: $includeSalaryTypes) {
    id
    name
  }
  paySchedules @include(if: $includePaySchedules) {
    id
    name
  }
  remunerations @include(if: $includeRemunerations) {
    id
    name
  }
  industries @include(if: $includeIndustries) {
    id
    name
  }
  jobRoleGroups @include(if: $includeJobRoleGroups) {
    id
    name
  }
  currentUser {
    institution {
      curations {
        nodes {
          id
          name
        }
      }
    }
  }
}
"""

_FILTERS_VARIABLES = {
    "includeJobTypes": True,
    "includeEmploymentTypes": True,
    "includeEducationLevels": True,
    "includeSalaryTypes": True,
    "includePaySchedules": True,
    "includeRemunerations": True,
    "includeIndustries": True,
    "includeJobRoleGroups": True,
}

# GraphQL sort mapping
_GQL_SORT_MAP = {
    "date": {"field": "POSTED_DATE", "direction": "DESC"},
    "relevance": {"field": "RELEVANCE", "direction": "ASC"},
}
_GQL_SORT_DEFAULT = {"field": "RELEVANCE", "direction": "ASC"}

_PAY_SCHEDULE_SUFFIX = {
    "HOURLY_WAGE": "/hr",
    "ANNUAL_SALARY": "/yr",
    "MONTHLY_STIPEND": "/mo",
}


def _format_salary(
    salary_type: dict | None,
    salary_range: dict | None,
    pay_schedule: dict | None,
) -> str | None:
    """Format GraphQL salary data into a human-readable string.

    Salary range values are in cents (3000 → $30). Returns None if the
    salary field should be omitted entirely.
    """
    if (salary_type or {}).get("behaviorIdentifier") == "UNPAID":
        return "Unpaid"

    if not salary_range:
        return None

    raw_min = salary_range.get("min") or 0
    raw_max = salary_range.get("max") or 0

    if not raw_min and not raw_max:
        return None

    min_val = raw_min // 100 if raw_min else 0
    max_val = raw_max // 100 if raw_max else 0
    suffix = _PAY_SCHEDULE_SUFFIX.get(
        (pay_schedule or {}).get("behaviorIdentifier", ""), ""
    )

    if min_val and max_val:
        if min_val == max_val:
            return f"${min_val}{suffix}"
        return f"${min_val}–{max_val}{suffix}"
    elif max_val:
        return f"Up to ${max_val}{suffix}"
    else:
        return f"${min_val}+{suffix}"


def _search_cursor(page_num: int) -> str:
    """Encode the GraphQL cursor for a given 1-indexed search page number."""
    return base64.b64encode(str((page_num - 1) * _PAGE_SIZE).encode()).decode()


def _build_job_metadata(job: dict) -> dict[str, Any]:
    """Build a structured metadata dict from a GraphQL job response."""
    meta: dict[str, Any] = {}

    for src, dst in [("id", "id"), ("title", "title")]:
        if val := job.get(src):
            meta[dst] = val

    employer = job.get("employer") or {}
    if name := employer.get("name"):
        meta["company"] = name
    if emp_id := employer.get("id"):
        meta["company_id"] = emp_id
    if industry_name := (employer.get("industry") or {}).get("name"):
        meta["industry"] = industry_name

    if job.get("hybrid"):
        meta["work_type"] = "hybrid"
    elif job.get("remote"):
        meta["work_type"] = "remote"
    elif job.get("onSite"):
        meta["work_type"] = "on_site"

    salary_range = job.get("salaryRange")
    pay_schedule = (salary_range or {}).get("paySchedule")
    salary_str = _format_salary(job.get("salaryType"), salary_range, pay_schedule)
    if salary_str is not None:
        meta["salary"] = salary_str
        if st := (job.get("salaryType") or {}).get("behaviorIdentifier"):
            meta["salary_type"] = st

    locations = [
        f"{loc['city']}, {loc['state']}"
        for loc in (job.get("locations") or [])
        if loc.get("city") and loc.get("state")
    ]
    if locations:
        meta["locations"] = locations

    if jt := (job.get("jobType") or {}).get("behaviorIdentifier"):
        meta["job_type"] = jt
    if et := (job.get("employmentType") or {}).get("behaviorIdentifier"):
        meta["employment_type"] = et

    for src, dst in [
        ("startDate", "start_date"),
        ("endDate", "end_date"),
        ("expirationDate", "deadline"),
        ("createdAt", "posted_at"),
    ]:
        if val := job.get(src):
            meta[dst] = val

    screen = job.get("studentScreen") or {}
    for src, dst in [
        ("workAuthRequired", "work_auth_required"),
        ("acceptsOptCandidates", "accepts_opt"),
        ("acceptsCptCandidates", "accepts_cpt"),
        ("willingToSponsorCandidate", "will_sponsor"),
    ]:
        if (val := screen.get(src)) is not None:
            meta[dst] = val

    if apply_url := (job.get("jobApplySetting") or {}).get("externalUrl"):
        meta["apply_url"] = apply_url

    return meta


def _build_search_job_entry(job: dict) -> dict[str, Any]:
    """Build a structured job summary dict from a GraphQL search result node."""
    entry: dict[str, Any] = {}

    for src, dst in [("id", "id"), ("title", "title")]:
        if val := job.get(src):
            entry[dst] = val

    if name := (job.get("employer") or {}).get("name"):
        entry["company"] = name
    if jt := (job.get("jobType") or {}).get("behaviorIdentifier"):
        entry["job_type"] = jt
    if et := (job.get("employmentType") or {}).get("behaviorIdentifier"):
        entry["employment_type"] = et

    salary_range = job.get("salaryRange")
    pay_schedule = (salary_range or {}).get("paySchedule")
    salary_str = _format_salary(job.get("salaryType"), salary_range, pay_schedule)
    if salary_str is not None:
        entry["salary"] = salary_str

    locations = [
        f"{loc['city']}, {loc['state']}"
        for loc in (job.get("locations") or [])
        if loc.get("city") and loc.get("state")
    ]
    if locations:
        entry["locations"] = locations

    return entry


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

    async def _fetch_graphql(self, query: str, variables: dict) -> dict | None:
        """Execute a GraphQL query via fetch() in the browser page context.

        Uses the browser's existing session cookies — no CSRF token needed.
        Returns the data dict on success, None on any failure.
        """
        clean_vars = {k: v for k, v in variables.items() if v is not None}
        try:
            return await self._page.evaluate(
                """async ({ query, variables }) => {
                    const response = await fetch('/hs/graphql', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        body: JSON.stringify({ query, variables }),
                    });
                    if (!response.ok) return null;
                    const json = await response.json();
                    if (json.errors) return null;
                    return json.data || null;
                }""",
                {"query": query, "variables": clean_vars},
            )
        except Exception as e:
            logger.debug("GraphQL fetch failed: %s", e)
            return None

    async def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text using the browser's innerText rendering."""
        if not html:
            return ""
        return await self._page.evaluate(
            """(html) => {
                const div = document.createElement('div');
                div.innerHTML = html;
                return div.innerText || '';
            }""",
            html,
        )

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
            "Handshake requires re-authentication. Run with --login and complete the sign-in flow."
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

        # Let Patchright resolve any Cloudflare bot challenge before we read content.
        # CF challenges fire after domcontentloaded and resolve in-page via JS.
        cf_resolved = await wait_for_cf_challenge(self._page)
        if not cf_resolved and "cf_challenge" in self._page.url:
            raise RateLimitError(
                "Cloudflare challenge did not resolve. The page may be temporarily blocked. "
                "Try again in a few seconds.",
                suggested_wait_time=10,
            )

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
            logger.warning("Page %s returned only Handshake chrome (likely rate-limited)", url)
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

    async def scrape_employer(self, employer_id: str, requested: set[str]) -> dict[str, Any]:
        """Scrape an employer profile with configurable sections.

        Returns:
            {url, sections: {name: text}}
        """
        requested = requested | {"overview"}
        base_url = f"{BASE_URL}/e/{employer_id}"
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

    async def _extract_job_metadata(self) -> dict[str, Any]:
        """Extract structured metadata from a loaded job posting page.

        Uses only semantic selectors (h1, href patterns) to stay resilient
        against Handshake's React SPA layout changes.
        """
        return await self._page.evaluate(
            """() => {
                const main = document.querySelector('main');
                if (!main) return {};

                // Title: first <h1> inside main
                const title = main.querySelector('h1')?.innerText?.trim() || '';

                // Company: first employer profile link (/e/{id})
                const companyAnchor = main.querySelector('a[href*="/e/"]');
                const company = companyAnchor?.innerText?.trim() || '';
                const companyHref = companyAnchor?.getAttribute('href') || '';
                const companyIdMatch = companyHref.match(/\\/e\\/(\\d+)/);
                const company_id = companyIdMatch ? companyIdMatch[1] : '';

                // Job link: canonical /jobs/{id} href
                const jobHref = window.location.href;
                const jobIdMatch = jobHref.match(/\\/jobs\\/(\\d+)/);
                const job_id = jobIdMatch ? jobIdMatch[1] : '';

                // Apply link: look for an anchor with "apply" in text near the top of main
                const allAnchors = Array.from(main.querySelectorAll('a[href]'));
                const applyAnchor = allAnchors.find(
                    a => /^apply(\\s+now)?$/i.test((a.innerText || a.textContent || '').trim())
                );
                const apply_url = applyAnchor?.href || '';

                return { title, company, company_id, job_id, apply_url };
            }"""
        )

    async def scrape_job(self, job_id: str) -> dict[str, Any]:
        """Scrape a single job posting.

        Tries Handshake's internal GraphQL API first for rich structured data.
        Falls back to innerText scraping if GraphQL fails.

        Returns:
            {url, sections: {job_posting: text}, metadata: {...}}
            GraphQL path: metadata has full structured fields, no references key.
            Fallback path: metadata has basic fields, references key present if found.
        """
        url = f"{BASE_URL}/jobs/{job_id}"

        # Always navigate and scrape — provides session context for GraphQL
        # and fallback data if GraphQL fails.
        extracted = await self.extract_page(url, section_name="job_posting")

        # Attempt GraphQL path
        data = await self._fetch_graphql(JOB_DETAILS_QUERY, {"id": job_id})
        if data is not None:
            job = data.get("job")
            if job is None:
                raise HandshakeScraperException(
                    f"Job {job_id} not found on Handshake. "
                    "Verify the ID is correct and the job is still active."
                )
            description_text = await self._html_to_text(job.get("description") or "")
            return {
                "url": url,
                "sections": {"job_posting": description_text},
                "metadata": _build_job_metadata(job),
            }

        # Fallback: use the already-scraped innerText content
        logger.debug("GraphQL failed for job %s, using scraped content", job_id)
        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}

        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["job_posting"] = extracted.text
            if extracted.references:
                references["job_posting"] = extracted.references
        elif extracted.error:
            section_errors["job_posting"] = extracted.error

        metadata: dict[str, Any] = {}
        if sections:
            try:
                raw_meta = await self._extract_job_metadata()
                metadata = {k: v for k, v in raw_meta.items() if v}
            except Exception as e:
                logger.debug("Could not extract job metadata: %s", e)

        result: dict[str, Any] = {"url": url, "sections": sections}
        if metadata:
            result["metadata"] = metadata
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
                const links = document.querySelectorAll('a[href*="/jobs/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/jobs\\/(\\d+)/);
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
                const links = document.querySelectorAll('a[href*="/e/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/e\\/(\\d+)(?:\\/|\\?|$)/);
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
        return f"{BASE_URL}/job-search?{params}"

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
            "sections": {"search_results": "\n---\n".join(page_texts)} if page_texts else {},
            "job_ids": all_job_ids,
        }
        if page_references:
            result["references"] = {"search_results": dedupe_references(page_references, cap=15)}
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
        base_url = f"{BASE_URL}/employer-search?query={quote_plus(keywords)}&per_page={_PAGE_SIZE}"

        all_employer_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                await asyncio.sleep(_NAV_DELAY)

            url = f"{base_url}&page={page_num}"

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
            "sections": {"search_results": "\n---\n".join(page_texts)} if page_texts else {},
            "employer_ids": all_employer_ids,
        }
        if page_references:
            result["references"] = {"search_results": dedupe_references(page_references, cap=15)}
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
        # Handshake events search does not support URL-based text query params.
        # Navigate to the events page and extract what's listed.
        base_url = f"{BASE_URL}/stu/events"

        all_event_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}

        for page_num in range(1, min(max_pages, 1) + 1):
            if page_num > 1:
                await asyncio.sleep(_NAV_DELAY)

            url = base_url

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
            "sections": {"search_results": "\n---\n".join(page_texts)} if page_texts else {},
            "event_ids": all_event_ids,
        }
        if page_references:
            result["references"] = {"search_results": dedupe_references(page_references, cap=15)}
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
