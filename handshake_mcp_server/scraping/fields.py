"""Section config dicts controlling which Handshake pages are visited during scraping."""

import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://app.joinhandshake.com"

# Maps section name -> (url_suffix, is_overlay)
# Each entry corresponds to exactly one page navigation.

# Student profile sections: base path /users/{id}
# NOTE: Handshake student profiles are largely single-page JS apps.
# Verify during live testing whether /experience, /education have distinct URLs.
STUDENT_SECTIONS: dict[str, tuple[str, bool]] = {
    "main_profile": ("", False),  # /users/{id}
}

# Employer profile sections: base path /stu/employers/{id}
EMPLOYER_SECTIONS: dict[str, tuple[str, bool]] = {
    "overview": ("", False),       # /stu/employers/{id}
    "jobs": ("/jobs", False),      # /stu/employers/{id}/jobs
    "reviews": ("/reviews", False), # /stu/employers/{id}/reviews
}

# Job detail section: base path /stu/jobs/{id}
JOB_SECTIONS: dict[str, tuple[str, bool]] = {
    "job_posting": ("", False),  # /stu/jobs/{id}
}

# Event detail section: base path /stu/events/{id}
EVENT_SECTIONS: dict[str, tuple[str, bool]] = {
    "event_details": ("", False),  # /stu/events/{id}
}


def parse_student_sections(
    sections: str | None,
) -> tuple[set[str], list[str]]:
    """Parse comma-separated section names for student profiles.

    "main_profile" is always included. Empty/None returns {"main_profile"} only.

    Returns:
        Tuple of (requested_sections, unknown_section_names).
    """
    requested: set[str] = {"main_profile"}
    unknown: list[str] = []
    if not sections:
        return requested, unknown
    for name in sections.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name in STUDENT_SECTIONS:
            requested.add(name)
        else:
            unknown.append(name)
            logger.warning(
                "Unknown student section %r ignored. Valid: %s",
                name,
                ", ".join(sorted(STUDENT_SECTIONS)),
            )
    return requested, unknown


def parse_employer_sections(
    sections: str | None,
) -> tuple[set[str], list[str]]:
    """Parse comma-separated section names for employer profiles.

    "overview" is always included. Empty/None returns {"overview"} only.

    Returns:
        Tuple of (requested_sections, unknown_section_names).
    """
    requested: set[str] = {"overview"}
    unknown: list[str] = []
    if not sections:
        return requested, unknown
    for name in sections.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name in EMPLOYER_SECTIONS:
            requested.add(name)
        else:
            unknown.append(name)
            logger.warning(
                "Unknown employer section %r ignored. Valid: %s",
                name,
                ", ".join(sorted(EMPLOYER_SECTIONS)),
            )
    return requested, unknown
