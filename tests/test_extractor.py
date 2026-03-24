"""Unit tests for HandshakeExtractor GraphQL helpers and pure functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from handshake_mcp_server.scraping.extractor import (
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
