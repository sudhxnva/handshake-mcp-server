"""Tests for scraping/fields.py section parsing."""

from handshake_mcp_server.scraping.fields import (
    EMPLOYER_SECTIONS,
    STUDENT_SECTIONS,
    parse_employer_sections,
    parse_student_sections,
)


def test_parse_student_sections_empty():
    sections, unknown = parse_student_sections(None)
    assert sections == {"main_profile"}
    assert unknown == []


def test_parse_student_sections_valid():
    sections, unknown = parse_student_sections("main_profile")
    assert "main_profile" in sections
    assert unknown == []


def test_parse_student_sections_unknown():
    sections, unknown = parse_student_sections("nonexistent")
    assert "main_profile" in sections
    assert "nonexistent" in unknown


def test_parse_employer_sections_empty():
    sections, unknown = parse_employer_sections(None)
    assert sections == {"overview"}
    assert unknown == []


def test_parse_employer_sections_valid():
    sections, unknown = parse_employer_sections("jobs,posts")
    assert "overview" in sections  # always included
    assert "jobs" in sections
    assert "posts" in sections
    assert unknown == []


def test_parse_employer_sections_unknown():
    sections, unknown = parse_employer_sections("nonexistent")
    assert "overview" in sections
    assert "nonexistent" in unknown


def test_student_sections_structure():
    for name, (suffix, is_overlay) in STUDENT_SECTIONS.items():
        assert isinstance(name, str)
        assert isinstance(suffix, str)
        assert isinstance(is_overlay, bool)
        assert not is_overlay, f"Student section {name!r} unexpectedly has overlay=True"


def test_employer_sections_structure():
    for name, (suffix, is_overlay) in EMPLOYER_SECTIONS.items():
        assert isinstance(name, str)
        assert isinstance(suffix, str)
        assert isinstance(is_overlay, bool)
