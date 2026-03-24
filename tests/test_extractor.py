"""Unit tests for HandshakeExtractor GraphQL helpers and pure functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handshake_mcp_server.core.exceptions import HandshakeScraperException
from handshake_mcp_server.scraping.extractor import (
    ExtractedSection,
    HandshakeExtractor,
    _build_job_metadata,
    _build_search_job_entry,
    _format_salary,
    _search_cursor,
)


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

    def test_work_type_on_site(self):
        meta = _build_job_metadata(self._job(hybrid=False, remote=False, onSite=True))
        assert meta["work_type"] == "on_site"

    def test_no_work_type_when_all_flags_false(self):
        meta = _build_job_metadata(self._job(hybrid=False, remote=False, onSite=False))
        assert "work_type" not in meta

    def test_salary_type_omitted_when_salary_omitted(self):
        # When salary range is zero, both salary and salary_type should be absent
        job = self._job(
            salaryRange={"min": 0, "max": 0, "paySchedule": {"behaviorIdentifier": "HOURLY_WAGE"}}
        )
        meta = _build_job_metadata(job)
        assert "salary" not in meta
        assert "salary_type" not in meta


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
        "salaryRange": {
            "min": 4000,
            "max": 4000,
            "paySchedule": {"behaviorIdentifier": "HOURLY_WAGE"},
        },
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
            patch.object(
                extractor, "_fetch_graphql", new=AsyncMock(return_value={"job": job_data})
            ),
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
            patch.object(
                extractor,
                "_extract_job_metadata",
                new=AsyncMock(
                    return_value={
                        "title": "SWE",
                        "company_id": "456",
                        "job_id": "123",
                        "company": "",
                        "apply_url": "",
                    }
                ),
            ),
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
            patch.object(
                extractor, "_fetch_graphql", new=AsyncMock(side_effect=[gql_response, None])
            ),
        ):
            result = await extractor.search_jobs("engineer", max_pages=2)

        assert len(result["job_ids"]) == 25
        assert result["job_ids"][0] == "0"
        assert len(result["jobs"]) == 25
        assert result["jobs"][0]["title"] == "Job 0"
        assert result["jobs"][0]["company"] == "Corp"

    async def test_stops_pagination_when_fewer_than_25_edges(self, extractor):
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
            patch.object(
                extractor, "_extract_search_page", new=AsyncMock(return_value=fallback_extracted)
            ),
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
            patch.object(
                extractor, "_fetch_graphql", new=AsyncMock(return_value=gql_response)
            ) as mock_gql,
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

    async def test_page2_graphql_failure_returns_partial_results(self, extractor):
        # Page 1 succeeds with 25 edges, page 2 GraphQL fails — returns 25 results, no fallback
        page1 = {"jobSearch": {"edges": [_make_search_edge(str(i)) for i in range(25)]}}

        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(side_effect=[page1, None])),
        ):
            result = await extractor.search_jobs("engineer", max_pages=3)

        assert len(result["job_ids"]) == 25
        assert len(result["jobs"]) == 25
        assert result["sections"]  # search_results text was built from page 1


class TestGetJobSearchFilters:
    def _gql_response(self):
        return {
            "jobTypes": [{"id": "3", "name": "Internship", "behaviorIdentifier": "INTERNSHIP"}],
            "employmentTypes": [
                {"id": "1", "name": "Full-Time", "behaviorIdentifier": "FULL_TIME"}
            ],
            "educationLevels": [{"id": "1", "name": "Bachelors"}],
            "salaryTypes": [{"id": "1", "name": "Paid"}],
            "paySchedules": [{"id": "1", "name": "Hourly Wage"}],
            "remunerations": [{"id": "6", "name": "Medical"}],
            "industries": [{"id": "1034", "name": "Internet & Software"}],
            "jobRoleGroups": [{"id": "64", "name": "Software Developers and Engineers"}],
        }

    async def test_returns_structured_filter_dict(self, extractor):
        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(
                extractor, "_fetch_graphql", new=AsyncMock(return_value=self._gql_response())
            ),
        ):
            result = await extractor.get_job_search_filters()

        assert result["job_types"] == [{"id": "3", "name": "Internship", "slug": "INTERNSHIP"}]
        assert result["employment_types"] == [{"id": "1", "name": "Full-Time", "slug": "FULL_TIME"}]
        assert result["education_levels"] == [{"id": "1", "name": "Bachelors"}]
        assert "collections" not in result
        assert result["industries"] == [{"id": "1034", "name": "Internet & Software"}]

    async def test_returns_empty_dict_on_graphql_failure(self, extractor):
        with (
            patch.object(extractor, "_goto_with_auth_checks", new=AsyncMock()),
            patch.object(extractor, "_fetch_graphql", new=AsyncMock(return_value=None)),
        ):
            result = await extractor.get_job_search_filters()

        assert result == {}
