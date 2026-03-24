# GraphQL-First Job Details & Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HTML scraping in `get_job_details` and `search_jobs` with Handshake's internal GraphQL API, add a new `get_job_search_filters` tool, and return structured per-job data from search results.

**Architecture:** All GraphQL calls go through a new `_fetch_graphql()` method on `HandshakeExtractor` that fires `fetch('/hs/graphql', ...)` from within the browser context using the existing session cookies. Every tool falls back to the existing innerText scraping path if GraphQL fails. Pure helper functions handle salary formatting and metadata assembly — they are module-level so they can be unit-tested without a browser.

**Tech Stack:** Python 3.12, patchright (Playwright fork), pytest with `asyncio_mode = "auto"`, unittest.mock

---

## File Map

| File | Action | What changes |
|---|---|---|
| `handshake_mcp_server/scraping/extractor.py` | Modify | Add `_fetch_graphql`, `_html_to_text`, query constants, pure helpers; rewrite `scrape_job` and `search_jobs`; add `get_job_search_filters` |
| `handshake_mcp_server/tools/job.py` | Modify | Update `search_jobs` MCP signature; register `get_job_search_filters` tool |
| `CLAUDE.md` | Modify | Update tool return format docs |
| `tests/test_extractor.py` | Create | Unit tests for all new helpers and methods |

---

## Task 1: GraphQL helper methods

**Files:**
- Modify: `handshake_mcp_server/scraping/extractor.py`
- Create: `tests/test_extractor.py`

### Step 1a: Write failing tests for `_fetch_graphql` and `_html_to_text`

Create `tests/test_extractor.py`:

```python
"""Unit tests for HandshakeExtractor GraphQL helpers and pure functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from handshake_mcp_server.scraping.extractor import HandshakeExtractor


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.evaluate = AsyncMock()
    return page


@pytest.fixture
def extractor(mock_page):
    return HandshakeExtractor(mock_page)


class TestFetchGraphQL:
    async def test_returns_data_on_success(self, extractor, mock_page):
        mock_page.evaluate.return_value = {"job": {"id": "123", "title": "Engineer"}}
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result == {"job": {"id": "123", "title": "Engineer"}}

    async def test_returns_none_when_evaluate_returns_none(self, extractor, mock_page):
        mock_page.evaluate.return_value = None
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result is None

    async def test_returns_none_on_exception(self, extractor, mock_page):
        mock_page.evaluate.side_effect = Exception("network error")
        result = await extractor._fetch_graphql("query { job }", {"id": "123"})
        assert result is None

    async def test_omits_none_valued_variables(self, extractor, mock_page):
        mock_page.evaluate.return_value = {}
        await extractor._fetch_graphql("query", {"id": "123", "empty": None})
        call_kwargs = mock_page.evaluate.call_args[0][1]
        assert "empty" not in call_kwargs["variables"]
        assert call_kwargs["variables"]["id"] == "123"


class TestHtmlToText:
    async def test_returns_text_from_mock(self, extractor, mock_page):
        mock_page.evaluate.return_value = "Hello World"
        result = await extractor._html_to_text("<p>Hello <b>World</b></p>")
        assert result == "Hello World"

    async def test_empty_input_returns_empty_string(self, extractor, mock_page):
        result = await extractor._html_to_text("")
        assert result == ""
        mock_page.evaluate.assert_not_called()
```

- [ ] **Step 1b: Run tests to verify they fail**

```bash
uv run pytest tests/test_extractor.py -v
```

Expected: `AttributeError: 'HandshakeExtractor' object has no attribute '_fetch_graphql'`

- [ ] **Step 1c: Implement `_fetch_graphql` and `_html_to_text` in `extractor.py`**

Add these two methods to the `HandshakeExtractor` class, after the `__init__` method:

```python
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
```

- [ ] **Step 1d: Run tests to verify they pass**

```bash
uv run pytest tests/test_extractor.py::TestFetchGraphQL tests/test_extractor.py::TestHtmlToText -v
```

Expected: 6 PASSED

- [ ] **Step 1e: Commit**

```bash
git add tests/test_extractor.py handshake_mcp_server/scraping/extractor.py
git commit -m "feat: add _fetch_graphql and _html_to_text helpers"
```

---

## Task 2: Pure helper functions

**Files:**
- Modify: `handshake_mcp_server/scraping/extractor.py`
- Modify: `tests/test_extractor.py`

These are module-level functions (not methods) that do pure data transformation — no browser interaction, easy to test.

- [ ] **Step 2a: Write failing tests**

Add to `tests/test_extractor.py`:

```python
from handshake_mcp_server.scraping.extractor import (
    _format_salary,
    _search_cursor,
    _build_job_metadata,
    _build_search_job_entry,
)


class TestFormatSalary:
    """All cases from the spec salary formatting table."""

    def _sr(self, min_val, max_val, schedule="HOURLY_WAGE"):
        return {
            "min": min_val,
            "max": max_val,
            "paySchedule": {"behaviorIdentifier": schedule},
        }

    def test_unpaid_returns_unpaid(self):
        result = _format_salary({"behaviorIdentifier": "UNPAID"}, None, None)
        assert result == "Unpaid"

    def test_null_salary_range_returns_none(self):
        result = _format_salary({"behaviorIdentifier": "PAID"}, None, None)
        assert result is None

    def test_both_zero_returns_none(self):
        sr = self._sr(0, 0)
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result is None

    def test_min_equals_max_no_range(self):
        sr = self._sr(3000, 3000)
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$30/hr"

    def test_min_and_max_different(self):
        sr = self._sr(3000, 3500)
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$30–35/hr"

    def test_only_max_nonzero(self):
        sr = self._sr(0, 3500)
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "Up to $35/hr"

    def test_only_min_nonzero(self):
        sr = self._sr(3000, 0)
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$30+/hr"

    def test_annual_salary_suffix(self):
        sr = self._sr(10000000, 15000000, "ANNUAL_SALARY")
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$100000–150000/yr"

    def test_monthly_stipend_suffix(self):
        sr = self._sr(200000, 200000, "MONTHLY_STIPEND")
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$2000/mo"

    def test_unknown_schedule_no_suffix(self):
        sr = {"min": 3000, "max": 3000, "paySchedule": {"behaviorIdentifier": "UNKNOWN"}}
        result = _format_salary(None, sr, sr["paySchedule"])
        assert result == "$30"


class TestSearchCursor:
    def test_page_1(self):
        assert _search_cursor(1) == "MA=="  # base64("0")

    def test_page_2(self):
        assert _search_cursor(2) == "MjU="  # base64("25")

    def test_page_3(self):
        assert _search_cursor(3) == "NTA="  # base64("50")


class TestBuildJobMetadata:
    def _job(self, **overrides):
        base = {
            "id": "123",
            "title": "Software Engineer",
            "description": "<p>Do cool things</p>",
            "hybrid": False,
            "remote": True,
            "onSite": False,
            "startDate": "2026-06-01",
            "endDate": None,
            "expirationDate": "2026-04-01",
            "createdAt": "2026-03-01",
            "employer": {
                "id": "456",
                "name": "Acme Corp",
                "industry": {"name": "Internet & Software"},
            },
            "salaryType": {"behaviorIdentifier": "PAID"},
            "salaryRange": {
                "min": 4500,
                "max": 4500,
                "paySchedule": {"behaviorIdentifier": "HOURLY_WAGE"},
            },
            "locations": [{"city": "Denver", "state": "CO"}],
            "jobType": {"behaviorIdentifier": "INTERNSHIP"},
            "employmentType": {"behaviorIdentifier": "FULL_TIME"},
            "studentScreen": {
                "workAuthRequired": True,
                "acceptsOptCandidates": False,
                "acceptsCptCandidates": False,
                "willingToSponsorCandidate": True,
            },
            "jobApplySetting": {"externalUrl": "https://apply.example.com"},
        }
        base.update(overrides)
        return base

    def test_basic_fields(self):
        meta = _build_job_metadata(self._job())
        assert meta["id"] == "123"
        assert meta["title"] == "Software Engineer"
        assert meta["company"] == "Acme Corp"
        assert meta["company_id"] == "456"
        assert meta["industry"] == "Internet & Software"

    def test_work_type_remote(self):
        meta = _build_job_metadata(self._job())
        assert meta["work_type"] == "remote"

    def test_work_type_hybrid(self):
        meta = _build_job_metadata(self._job(hybrid=True, remote=False))
        assert meta["work_type"] == "hybrid"

    def test_salary_formatted(self):
        meta = _build_job_metadata(self._job())
        assert meta["salary"] == "$45/hr"

    def test_locations_list(self):
        meta = _build_job_metadata(self._job())
        assert meta["locations"] == ["Denver, CO"]

    def test_dates(self):
        meta = _build_job_metadata(self._job())
        assert meta["start_date"] == "2026-06-01"
        assert meta["deadline"] == "2026-04-01"
        assert meta["posted_at"] == "2026-03-01"
        assert "end_date" not in meta  # null end_date omitted

    def test_work_auth_fields(self):
        meta = _build_job_metadata(self._job())
        assert meta["work_auth_required"] is True
        assert meta["will_sponsor"] is True

    def test_apply_url(self):
        meta = _build_job_metadata(self._job())
        assert meta["apply_url"] == "https://apply.example.com"

    def test_null_salary_range_omitted(self):
        meta = _build_job_metadata(self._job(salaryRange=None))
        assert "salary" not in meta


class TestBuildSearchJobEntry:
    def test_builds_entry(self):
        job = {
            "id": "789",
            "title": "Data Analyst",
            "employer": {"name": "Big Corp"},
            "jobType": {"behaviorIdentifier": "JOB"},
            "employmentType": {"behaviorIdentifier": "FULL_TIME"},
            "salaryType": {"behaviorIdentifier": "PAID"},
            "salaryRange": {
                "min": 8000000,
                "max": 12000000,
                "paySchedule": {"behaviorIdentifier": "ANNUAL_SALARY"},
            },
            "locations": [{"city": "Austin", "state": "TX"}],
        }
        entry = _build_search_job_entry(job)
        assert entry["id"] == "789"
        assert entry["title"] == "Data Analyst"
        assert entry["company"] == "Big Corp"
        assert entry["job_type"] == "JOB"
        assert entry["employment_type"] == "FULL_TIME"
        assert entry["salary"] == "$80000–120000/yr"
        assert entry["locations"] == ["Austin, TX"]

    def test_missing_optional_fields_omitted(self):
        job = {"id": "1", "title": "Assistant", "employer": {"name": "Shop"}}
        entry = _build_search_job_entry(job)
        assert "salary" not in entry
        assert "locations" not in entry
        assert "job_type" not in entry
```

- [ ] **Step 2b: Run tests to verify they fail**

```bash
uv run pytest tests/test_extractor.py::TestFormatSalary tests/test_extractor.py::TestSearchCursor tests/test_extractor.py::TestBuildJobMetadata tests/test_extractor.py::TestBuildSearchJobEntry -v
```

Expected: `ImportError: cannot import name '_format_salary' from 'handshake_mcp_server.scraping.extractor'`

- [ ] **Step 2c: Implement the pure helpers in `extractor.py`**

Add these module-level constants and functions. Place them **after** the `_NOISE_LINES` block and **before** the `ExtractedSection` dataclass:

```python
import base64

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
```

Also add `import base64` at the top of `extractor.py` (alongside the other stdlib imports).

- [ ] **Step 2d: Run tests to verify they pass**

```bash
uv run pytest tests/test_extractor.py::TestFormatSalary tests/test_extractor.py::TestSearchCursor tests/test_extractor.py::TestBuildJobMetadata tests/test_extractor.py::TestBuildSearchJobEntry -v
```

Expected: All PASSED

- [ ] **Step 2e: Run full test suite to check for regressions**

```bash
uv run pytest --tb=short
```

Expected: All existing tests pass

- [ ] **Step 2f: Commit**

```bash
git add tests/test_extractor.py handshake_mcp_server/scraping/extractor.py
git commit -m "feat: add salary formatting, cursor, and metadata builder helpers"
```

---

## Task 3: Update `scrape_job` to use GraphQL

**Files:**
- Modify: `handshake_mcp_server/scraping/extractor.py`
- Modify: `tests/test_extractor.py`

- [ ] **Step 3a: Write failing tests for the GraphQL path of `scrape_job`**

Add to `tests/test_extractor.py`:

```python
from handshake_mcp_server.scraping.extractor import ExtractedSection
from handshake_mcp_server.core.exceptions import HandshakeScraperException


def _make_gql_job(**overrides):
    """Minimal valid GraphQL job dict for testing."""
    base = {
        "id": "123",
        "title": "SWE Intern",
        "description": "<p>Build things.</p>",
        "hybrid": False,
        "remote": True,
        "onSite": False,
        "startDate": None,
        "endDate": None,
        "expirationDate": "2026-05-01",
        "createdAt": "2026-03-01",
        "employer": {"id": "456", "name": "Acme", "industry": {"name": "Tech"}},
        "salaryType": {"behaviorIdentifier": "PAID"},
        "salaryRange": {"min": 4000, "max": 4000, "paySchedule": {"behaviorIdentifier": "HOURLY_WAGE"}},
        "locations": [{"city": "Boulder", "state": "CO"}],
        "jobType": {"behaviorIdentifier": "INTERNSHIP"},
        "employmentType": {"behaviorIdentifier": "FULL_TIME"},
        "studentScreen": {
            "workAuthRequired": False,
            "acceptsOptCandidates": True,
            "acceptsCptCandidates": True,
            "willingToSponsorCandidate": False,
        },
        "jobApplySetting": {"externalUrl": "https://apply.example.com"},
    }
    base.update(overrides)
    return base


class TestScrapeJobGraphQL:
    async def test_graphql_success_returns_rich_metadata(self, extractor):
        job_data = _make_gql_job()
        extracted = ExtractedSection(text="scraped text", references=[])

        with (
            patch.object(extractor, "extract_page", new=AsyncMock(return_value=extracted)),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value={"job": job_data})),
            patch.object(extractor, "_html_to_text", new=AsyncMock(return_value="Build things.")),
        ):
            result = await extractor.scrape_job("123")

        assert result["metadata"]["title"] == "SWE Intern"
        assert result["metadata"]["company"] == "Acme"
        assert result["metadata"]["salary"] == "$40/hr"
        assert result["metadata"]["work_type"] == "remote"
        assert result["sections"]["job_posting"] == "Build things."
        assert "references" not in result  # no DOM references in GraphQL path

    async def test_graphql_fallback_uses_scraped_text(self, extractor):
        extracted = ExtractedSection(text="Scraped job text", references=[])

        with (
            patch.object(extractor, "extract_page", new=AsyncMock(return_value=extracted)),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=None)),
            patch.object(extractor, "_extract_job_metadata", new=AsyncMock(return_value={"title": "SWE", "company_id": "456", "job_id": "123", "company": "", "apply_url": ""})),
        ):
            result = await extractor.scrape_job("123")

        assert result["sections"]["job_posting"] == "Scraped job text"

    async def test_null_job_raises_exception(self, extractor):
        extracted = ExtractedSection(text="some text", references=[])

        with (
            patch.object(extractor, "extract_page", new=AsyncMock(return_value=extracted)),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value={"job": None})),
        ):
            with pytest.raises(HandshakeScraperException, match="not found"):
                await extractor.scrape_job("999")
```

- [ ] **Step 3b: Run tests to verify they fail**

```bash
uv run pytest tests/test_extractor.py::TestScrapeJobGraphQL -v
```

Expected: FAILED — current `scrape_job` doesn't call `_fetch_graphql`

- [ ] **Step 3c: Rewrite `scrape_job` in `extractor.py`**

Replace the existing `scrape_job` method with:

```python
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
```

- [ ] **Step 3d: Run tests to verify they pass**

```bash
uv run pytest tests/test_extractor.py::TestScrapeJobGraphQL -v
```

Expected: 3 PASSED

- [ ] **Step 3e: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: All pass

- [ ] **Step 3f: Commit**

```bash
git add tests/test_extractor.py handshake_mcp_server/scraping/extractor.py
git commit -m "feat: use GraphQL in scrape_job with fallback to innerText"
```

---

## Task 4: Update `search_jobs` with GraphQL path and new signature

**Files:**
- Modify: `handshake_mcp_server/scraping/extractor.py`
- Modify: `tests/test_extractor.py`

- [ ] **Step 4a: Write failing tests for the new `search_jobs`**

Add to `tests/test_extractor.py`:

```python
def _make_search_edge(job_id, title="Engineer", company="Corp"):
    return {
        "node": {
            "job": {
                "id": job_id,
                "title": title,
                "employer": {"name": company},
                "jobType": {"behaviorIdentifier": "INTERNSHIP"},
                "employmentType": {"behaviorIdentifier": "FULL_TIME"},
                "salaryType": {"behaviorIdentifier": "PAID"},
                "salaryRange": {
                    "min": 4000,
                    "max": 4000,
                    "paySchedule": {"behaviorIdentifier": "HOURLY_WAGE"},
                },
                "locations": [{"city": "Boulder", "state": "CO"}],
            }
        }
    }


class TestSearchJobsGraphQL:
    async def test_returns_job_ids_and_jobs_list(self, extractor):
        edges = [_make_search_edge(str(i), f"Job {i}") for i in range(25)]
        gql_response = {"jobSearch": {"edges": edges}}

        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(side_effect=[gql_response, None])),
        ):
            result = await extractor.search_jobs("engineer", max_pages=2)

        assert len(result["job_ids"]) == 25
        assert result["job_ids"][0] == "0"
        assert len(result["jobs"]) == 25
        assert result["jobs"][0]["title"] == "Job 0"
        assert result["jobs"][0]["company"] == "Corp"

    async def test_stops_pagination_when_fewer_than_25_edges(self, extractor):
        # Page 1: 25 edges (full page), page 2: 10 edges (last page)
        page1 = {"jobSearch": {"edges": [_make_search_edge(str(i)) for i in range(25)]}}
        page2 = {"jobSearch": {"edges": [_make_search_edge(str(i + 25)) for i in range(10)]}}

        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(side_effect=[page1, page2])),
        ):
            result = await extractor.search_jobs("engineer", max_pages=5)

        assert len(result["job_ids"]) == 35

    async def test_fallback_on_page1_graphql_failure(self, extractor):
        fallback_extracted = ExtractedSection(text="Fallback text", references=[])

        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=None)),
            patch.object(extractor, "_extract_search_page", new=AsyncMock(return_value=fallback_extracted)),
            patch.object(extractor, "_extract_job_ids", new=AsyncMock(return_value=["111", "222"])),
        ):
            result = await extractor.search_jobs("engineer", max_pages=1)

        assert result["sections"]["search_results"] == "Fallback text"
        assert result["job_ids"] == ["111", "222"]
        assert result["jobs"] == []

    async def test_id_filter_params_passed_to_graphql(self, extractor):
        gql_response = {"jobSearch": {"edges": []}}

        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=gql_response)) as mock_gql,
        ):
            await extractor.search_jobs(
                "engineer",
                job_type_ids=["3"],
                collection_ids=["20902"],
                max_pages=1,
            )

        call_variables = mock_gql.call_args[0][1]
        assert call_variables["input"]["filter"]["jobTypeIds"] == ["3"]
        assert call_variables["input"]["filter"]["curationIds"] == ["20902"]
```

- [ ] **Step 4b: Run tests to verify they fail**

```bash
uv run pytest tests/test_extractor.py::TestSearchJobsGraphQL -v
```

Expected: FAILED — current `search_jobs` doesn't accept ID params or call `_fetch_graphql`

- [ ] **Step 4c: Rewrite `search_jobs` and simplify `_build_job_search_url` in `extractor.py`**

**Delete** `_JOB_TYPE_MAP`, `_WORK_LOCATION_MAP`, and `_SORT_BY_MAP` from the module-level constants.

**Replace** `_build_job_search_url` with this simpler version:

```python
@staticmethod
def _build_job_search_url(keywords: str, location: str | None = None) -> str:
    """Build a Handshake job search URL for session navigation."""
    params = f"query={quote_plus(keywords)}"
    if location:
        params += f"&location={quote_plus(location)}"
    return f"{BASE_URL}/job-search?{params}"
```

**Replace** `search_jobs` with:

```python
async def search_jobs(
    self,
    keywords: str,
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
    max_pages: int = 3,
) -> dict[str, Any]:
    """Search for jobs with pagination, job ID extraction, and structured results.

    Use get_job_search_filters first to discover valid IDs for all *_ids params.
    Work location (remote/hybrid/on_site) filtering is not supported — the
    GraphQL filter key could not be confirmed.

    Args:
        keywords: Search keywords (e.g., "software engineer")
        job_type_ids: Filter by job type (e.g., ["3"] for Internship)
        employment_type_ids: Filter by employment type (e.g., ["1"] for Full-Time)
        education_level_ids: Filter by education level
        collection_ids: School-specific curation IDs (e.g., on-campus jobs)
        industry_ids: Filter by industry
        job_role_group_ids: Filter by job role group
        remuneration_ids: Filter by benefits/remuneration
        salary_type_ids: Filter by salary type (e.g., ["1"] for Paid)
        location: Best-effort location hint appended to the URL query string
        sort_by: "relevance" (default) or "date"
        max_pages: Maximum pages to fetch (1-10, default 3, 25 results per page)

    Returns:
        {url, sections: {search_results: text}, job_ids: [str], jobs: [dict]}
    """
    base_url = self._build_job_search_url(keywords, location=location)

    # Navigate to establish session context for GraphQL
    await self._goto_with_auth_checks(base_url)

    # Build GraphQL filter — omit None lists
    gql_filter: dict[str, Any] = {"query": keywords}
    if job_type_ids:
        gql_filter["jobTypeIds"] = job_type_ids
    if employment_type_ids:
        gql_filter["employmentTypeIds"] = employment_type_ids
    if education_level_ids:
        gql_filter["educationLevelIds"] = education_level_ids
    if collection_ids:
        gql_filter["curationIds"] = collection_ids
    if industry_ids:
        gql_filter["industryIds"] = industry_ids
    if job_role_group_ids:
        gql_filter["jobRoleGroupIds"] = job_role_group_ids
    if remuneration_ids:
        gql_filter["remunerationIds"] = remuneration_ids
    if salary_type_ids:
        gql_filter["salaryTypeIds"] = salary_type_ids

    gql_sort = _GQL_SORT_MAP.get(sort_by or "", _GQL_SORT_DEFAULT)

    all_job_ids: list[str] = []
    all_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page_num in range(1, max_pages + 1):
        variables = {
            "first": _PAGE_SIZE,
            "after": _search_cursor(page_num),
            "input": {"filter": gql_filter, "sort": gql_sort},
        }
        data = await self._fetch_graphql(JOB_SEARCH_QUERY, variables)

        if data is None:
            if page_num == 1:
                # GraphQL failed on page 1 — fall back to scraping
                logger.debug("GraphQL failed for search, falling back to scraping")
                return await self._search_jobs_fallback(
                    base_url, keywords, location, max_pages
                )
            # Pages 2+: stop pagination silently, return what we have
            break

        edges = (data.get("jobSearch") or {}).get("edges") or []
        for edge in edges:
            job = (edge.get("node") or {}).get("job") or {}
            job_id = job.get("id")
            if job_id and job_id not in seen_ids:
                seen_ids.add(job_id)
                all_job_ids.append(job_id)
                all_jobs.append(_build_search_job_entry(job))

        if len(edges) < _PAGE_SIZE:
            break  # Last page

    search_text = "\n".join(
        f"{j.get('company', '')} — {j.get('title', '')} · "
        f"{j.get('salary', '')} · {j.get('job_type', '')} · "
        f"{', '.join(j.get('locations', []))}"
        for j in all_jobs
    )

    return {
        "url": base_url,
        "sections": {"search_results": search_text} if search_text else {},
        "job_ids": all_job_ids,
        "jobs": all_jobs,
    }

async def _search_jobs_fallback(
    self,
    base_url: str,
    keywords: str,
    location: str | None,
    max_pages: int,
) -> dict[str, Any]:
    """Inner fallback: scrape job search pages as innerText."""
    all_job_ids: list[str] = []
    seen_ids: set[str] = set()
    page_texts: list[str] = []
    page_references: list[Reference] = []
    section_errors: dict[str, dict[str, Any]] = {}

    for page_num in range(1, max_pages + 1):
        if page_num > 1:
            await asyncio.sleep(_NAV_DELAY)

        url = self._build_job_search_url(keywords, location=location)
        if page_num > 1:
            url += f"&page={page_num}"

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
        "jobs": [],
    }
    if page_references:
        result["references"] = {"search_results": dedupe_references(page_references, cap=15)}
    if section_errors:
        result["section_errors"] = section_errors
    return result
```

- [ ] **Step 4d: Run tests to verify they pass**

```bash
uv run pytest tests/test_extractor.py::TestSearchJobsGraphQL -v
```

Expected: 4 PASSED

- [ ] **Step 4e: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: All pass

- [ ] **Step 4f: Commit**

```bash
git add tests/test_extractor.py handshake_mcp_server/scraping/extractor.py
git commit -m "feat: use GraphQL in search_jobs with structured jobs list"
```

---

## Task 5: Add `get_job_search_filters`

**Files:**
- Modify: `handshake_mcp_server/scraping/extractor.py`
- Modify: `tests/test_extractor.py`

- [ ] **Step 5a: Write failing tests**

Add to `tests/test_extractor.py`:

```python
class TestGetJobSearchFilters:
    def _gql_response(self):
        return {
            "jobTypes": [{"id": "3", "name": "Internship", "behaviorIdentifier": "INTERNSHIP"}],
            "employmentTypes": [{"id": "1", "name": "Full-Time", "behaviorIdentifier": "FULL_TIME"}],
            "educationLevels": [{"id": "1", "name": "Bachelors"}],
            "salaryTypes": [{"id": "1", "name": "Paid"}],
            "paySchedules": [{"id": "1", "name": "Hourly Wage"}],
            "remunerations": [{"id": "6", "name": "Medical"}],
            "industries": [{"id": "1034", "name": "Internet & Software"}],
            "jobRoleGroups": [{"id": "64", "name": "Software Developers and Engineers"}],
            "currentUser": {
                "institution": {
                    "curations": {
                        "nodes": [{"id": "20902", "name": "On-Campus Student Employment"}]
                    }
                }
            },
        }

    async def test_returns_structured_filter_dict(self, extractor):
        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=self._gql_response())),
        ):
            result = await extractor.get_job_search_filters()

        assert result["job_types"] == [{"id": "3", "name": "Internship", "slug": "INTERNSHIP"}]
        assert result["employment_types"] == [{"id": "1", "name": "Full-Time", "slug": "FULL_TIME"}]
        assert result["education_levels"] == [{"id": "1", "name": "Bachelors"}]
        assert result["collections"] == [{"id": "20902", "name": "On-Campus Student Employment"}]
        assert result["industries"] == [{"id": "1034", "name": "Internet & Software"}]

    async def test_returns_error_dict_on_graphql_failure(self, extractor):
        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=None)),
        ):
            result = await extractor.get_job_search_filters()

        assert result == {}
        # or: assert "error" in result  — see step 5c for exact shape
```

- [ ] **Step 5b: Run tests to verify they fail**

```bash
uv run pytest tests/test_extractor.py::TestGetJobSearchFilters -v
```

Expected: `AttributeError: 'HandshakeExtractor' object has no attribute 'get_job_search_filters'`

- [ ] **Step 5c: Implement `get_job_search_filters` in `extractor.py`**

Add to `HandshakeExtractor`:

```python
async def get_job_search_filters(self) -> dict[str, Any]:
    """Return all available job search filter options for the current user.

    Navigates to /job-search to establish session context, then queries
    the GraphQL API for all filter options.

    Returns:
        Dict with keys: job_types, employment_types, education_levels,
        salary_types, pay_schedules, remunerations, collections,
        industries, job_role_groups.
        Returns empty dict on failure.
    """
    url = f"{BASE_URL}/job-search"
    await self._goto_with_auth_checks(url)

    data = await self._fetch_graphql(FILTERS_QUERY, _FILTERS_VARIABLES)
    if data is None:
        logger.debug("GraphQL failed for get_job_search_filters")
        return {}

    def _extract(items: list[dict] | None, include_slug: bool = False) -> list[dict]:
        result = []
        for item in (items or []):
            entry: dict[str, Any] = {"id": str(item["id"]), "name": item["name"]}
            if include_slug and "behaviorIdentifier" in item:
                entry["slug"] = item["behaviorIdentifier"]
            result.append(entry)
        return result

    curations = (
        ((data.get("currentUser") or {}).get("institution") or {})
        .get("curations", {})
        .get("nodes", [])
    )

    return {
        "job_types": _extract(data.get("jobTypes"), include_slug=True),
        "employment_types": _extract(data.get("employmentTypes"), include_slug=True),
        "education_levels": _extract(data.get("educationLevels")),
        "salary_types": _extract(data.get("salaryTypes")),
        "pay_schedules": _extract(data.get("paySchedules")),
        "remunerations": _extract(data.get("remunerations")),
        "collections": _extract(curations),
        "industries": _extract(data.get("industries")),
        "job_role_groups": _extract(data.get("jobRoleGroups")),
    }
```

Update the test for the failure case to match the empty dict return:

```python
# In TestGetJobSearchFilters.test_returns_error_dict_on_graphql_failure:
assert result == {}
```

- [ ] **Step 5d: Run tests to verify they pass**

```bash
uv run pytest tests/test_extractor.py::TestGetJobSearchFilters -v
```

Expected: 2 PASSED

- [ ] **Step 5e: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: All pass

- [ ] **Step 5f: Commit**

```bash
git add tests/test_extractor.py handshake_mcp_server/scraping/extractor.py
git commit -m "feat: add get_job_search_filters GraphQL method"
```

---

## Task 6: Update `tools/job.py`

**Files:**
- Modify: `handshake_mcp_server/tools/job.py`

No new tests needed — the tool layer is a thin adapter. Changes are verified by the existing test suite and by running the server.

- [ ] **Step 6a: Update the `search_jobs` tool signature**

Replace the existing `search_jobs` function in `tools/job.py` with:

```python
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

    Returns job_ids and a structured jobs list. Use get_job_search_filters
    to discover valid IDs for all *_ids parameters.

    Work location filtering (remote/hybrid/on_site) is not supported.

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
```

- [ ] **Step 6b: Register the `get_job_search_filters` tool**

Add this new tool inside the `register_job_tools` function, after `search_jobs`:

```python
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

    Returns filter IDs and names for use with search_jobs. Run this first
    to discover which IDs to pass as job_type_ids, collection_ids, etc.

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
```

- [ ] **Step 6c: Run full test suite**

```bash
uv run pytest --tb=short
```

Expected: All pass

- [ ] **Step 6d: Lint and type check**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check
```

Fix any issues before committing.

- [ ] **Step 6e: Commit**

```bash
git add handshake_mcp_server/tools/job.py
git commit -m "feat: update search_jobs signature and register get_job_search_filters tool"
```

---

## Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 7a: Update the Tool Return Format section**

Find the `## Tool Return Format` section in `CLAUDE.md` and update it:

**Old:**
```
- `metadata: {title, company, company_id, job_id, apply_url}` (get_job_details only) — structured fields extracted from semantic HTML
```

**New:**
```
- `metadata: {id, title, company, company_id, industry, salary, salary_type, work_type, locations, job_type, employment_type, start_date, end_date, deadline, posted_at, work_auth_required, accepts_opt, accepts_cpt, will_sponsor, apply_url}` (get_job_details only, GraphQL path) — all fields optional, present when available
- `jobs: [{id, title, company, job_type, employment_type, salary, locations}]` (search_jobs only) — card-level metadata; use job_ids with get_job_details for full details
```

- [ ] **Step 7b: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new job metadata and jobs list shapes"
```

---

## Verification

After all tasks complete, verify end-to-end:

```bash
# Run full test suite with coverage
uv run pytest --cov --tb=short

# Lint + format + type check
uv run ruff check . && uv run ruff format --check . && uv run ty check

# (Optional — requires live Handshake session) smoke test via HTTP
uv run -m handshake_mcp_server --transport streamable-http --log-level DEBUG
```

Smoke test the three affected tools manually using the curl pattern from CLAUDE.md:
1. `get_job_search_filters` — should return filter dict with `job_types`, `collections`, etc.
2. `search_jobs` with `keywords="software engineer"` — should return non-empty `job_ids` and `jobs` list
3. `get_job_details` with a valid job ID — should return rich `metadata` with `company`, `industry`, `salary`, etc.
