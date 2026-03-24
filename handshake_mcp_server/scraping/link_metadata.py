"""Helpers for extracting compact, typed references from Handshake DOM links."""

from __future__ import annotations

import re
from typing import Literal, NotRequired, Required, TypedDict
from urllib.parse import urlparse

ReferenceKind = Literal[
    "student",
    "employer",
    "job",
    "event",
    "external",
]


class Reference(TypedDict):
    """Compact reference payload returned to MCP clients."""

    kind: Required[ReferenceKind]
    url: Required[str]
    text: NotRequired[str]
    context: NotRequired[str]


class RawReference(TypedDict, total=False):
    """Raw anchor data collected from the browser DOM."""

    href: str
    text: str
    aria_label: str
    title: str
    heading: str
    in_article: bool
    in_nav: bool
    in_footer: bool


_GENERIC_LABELS = {
    "apply",
    "apply now",
    "view",
    "view all",
    "follow",
    "following",
    "save",
    "share",
    "close",
    "back",
    "next",
    "previous",
    "more",
    "less",
    "show more",
    "show less",
}

_SECTION_CONTEXTS: dict[str, str] = {
    "main_profile": "profile",
    "overview": "overview",
    "jobs": "jobs",
    "posts": "posts",
    "job_posting": "job",
    "event_details": "event",
    "search_results": "search",
}

_DEFAULT_REFERENCE_CAP = 12
_REFERENCE_CAPS: dict[str, int] = {
    "main_profile": 12,
    "overview": 12,
    "jobs": 8,
    "posts": 8,
    "job_posting": 8,
    "event_details": 8,
    "search_results": 15,
}

_WHITESPACE_RE = re.compile(r"\s+")

# Handshake URL patterns
_STUDENT_PATH_RE = re.compile(r"^/(?:users|profiles)/(\d+)")
_EMPLOYER_PATH_RE = re.compile(r"^/e/(\d+)")
_JOB_PATH_RE = re.compile(r"^/jobs/(\d+)")
_EVENT_PATH_RE = re.compile(r"^/stu/events/(\d+)")


def build_references(
    raw_references: list[RawReference],
    section_name: str,
) -> list[Reference]:
    """Filter and normalize raw DOM anchors into compact references."""
    cap = _REFERENCE_CAPS.get(section_name, _DEFAULT_REFERENCE_CAP)
    normalized: list[Reference] = []

    for raw in raw_references:
        ref = normalize_reference(raw, section_name)
        if ref is None:
            continue
        normalized.append(ref)

    return dedupe_references(normalized, cap=cap)


def normalize_reference(
    raw: RawReference,
    section_name: str,
) -> Reference | None:
    """Normalize one raw DOM anchor into a compact reference."""
    if raw.get("in_nav") or raw.get("in_footer"):
        return None

    href = raw.get("href", "")
    if not href or href == "#":
        return None

    kind_url = classify_link(href)
    if kind_url is None:
        return None
    kind, normalized_url = kind_url

    text = choose_reference_text(raw, kind)
    if text is None and kind == "external":
        return None

    ref: Reference = {"kind": kind, "url": normalized_url}
    if text:
        ref["text"] = text
    context = derive_context(raw, section_name)
    if context:
        ref["context"] = context
    return ref


def classify_link(href: str) -> tuple[ReferenceKind, str] | None:
    """Classify a URL into a Handshake reference kind."""
    parsed = urlparse(href)

    # Only process Handshake internal URLs or relative paths
    host = parsed.netloc
    if host and "joinhandshake.com" not in host:
        # External link
        if href.startswith("http"):
            return ("external", href)
        return None

    path = parsed.path

    if _STUDENT_PATH_RE.match(path):
        return ("student", path)
    if _EMPLOYER_PATH_RE.match(path):
        return ("employer", path)
    if _JOB_PATH_RE.match(path):
        return ("job", path)
    if _EVENT_PATH_RE.match(path):
        return ("event", path)

    return None


def choose_reference_text(raw: RawReference, kind: ReferenceKind) -> str | None:
    """Pick the best label for a reference from available anchor attributes."""
    candidates = [
        raw.get("aria_label", ""),
        raw.get("title", ""),
        raw.get("text", ""),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        cleaned = clean_label(candidate)
        if cleaned and cleaned.lower() not in _GENERIC_LABELS:
            return cleaned

    return None


def clean_label(text: str) -> str:
    """Normalize label text."""
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    # Remove leading "View " prefix
    if cleaned.lower().startswith("view "):
        cleaned = cleaned[5:]
    return cleaned


def derive_context(raw: RawReference, section_name: str) -> str | None:
    """Derive a context hint for a reference."""
    heading = raw.get("heading", "")
    if heading:
        return heading.lower()[:50]
    section_context = _SECTION_CONTEXTS.get(section_name)
    return section_context


def dedupe_references(references: list[Reference], cap: int) -> list[Reference]:
    """Deduplicate references by URL and apply the cap."""
    seen_urls: set[str] = set()
    deduped: list[Reference] = []

    for ref in references:
        url = ref["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(ref)
        if len(deduped) >= cap:
            break

    return deduped
