"""Tests for scraping/link_metadata.py reference extraction."""

from handshake_mcp_server.scraping.link_metadata import (
    RawReference,
    Reference,
    classify_link,
    dedupe_references,
    normalize_reference,
)


def test_classify_job_link():
    result = classify_link("https://app.joinhandshake.com/jobs/9876543")
    assert result is not None
    kind, url = result
    assert kind == "job"
    assert "/jobs/9876543" in url


def test_classify_employer_link():
    result = classify_link("https://app.joinhandshake.com/e/123456")
    assert result is not None
    kind, url = result
    assert kind == "employer"


def test_classify_event_link():
    result = classify_link("https://app.joinhandshake.com/stu/events/654321")
    assert result is not None
    kind, url = result
    assert kind == "event"


def test_classify_student_link():
    result = classify_link("https://app.joinhandshake.com/users/12345678")
    assert result is not None
    kind, url = result
    assert kind == "student"


def test_classify_external_link():
    result = classify_link("https://www.google.com/careers")
    assert result is not None
    kind, url = result
    assert kind == "external"


def test_classify_unrelated_link():
    result = classify_link("https://www.twitter.com/handshake")
    assert result is not None
    assert result[0] == "external"


def test_classify_relative_path_job():
    result = classify_link("/jobs/9876543")
    assert result is not None
    kind, url = result
    assert kind == "job"


def test_normalize_reference_skips_nav():
    raw: RawReference = {
        "href": "https://app.joinhandshake.com/stu/jobs/123",
        "text": "Software Engineer",
        "in_nav": True,
    }
    result = normalize_reference(raw, "search_results")
    assert result is None


def test_normalize_reference_skips_footer():
    raw: RawReference = {
        "href": "https://app.joinhandshake.com/stu/jobs/123",
        "text": "Software Engineer",
        "in_footer": True,
    }
    result = normalize_reference(raw, "search_results")
    assert result is None


def test_dedupe_references_removes_duplicates():
    refs: list[Reference] = [
        {"kind": "job", "url": "/stu/jobs/1", "text": "Job A"},
        {"kind": "job", "url": "/stu/jobs/1", "text": "Job A duplicate"},
        {"kind": "job", "url": "/stu/jobs/2", "text": "Job B"},
    ]
    result = dedupe_references(refs, cap=10)
    urls = [r["url"] for r in result]
    assert len(urls) == 2
    assert urls.count("/stu/jobs/1") == 1


def test_dedupe_references_respects_cap():
    refs: list[Reference] = [
        {"kind": "job", "url": f"/stu/jobs/{i}", "text": f"Job {i}"} for i in range(20)
    ]
    result = dedupe_references(refs, cap=5)
    assert len(result) == 5
