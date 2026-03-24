# Design: GraphQL-First Job Details & Search

**Date:** 2026-03-24
**Branch:** notch-technosaurus
**Scope:** `get_job_details`, `search_jobs` tools + new `get_job_search_filters` tool

---

## Background

The current `get_job_details` tool scrapes the Handshake job page as innerText. This produces:
- A truncated job description (Handshake collapses long descriptions behind a "More" button)
- Minimal `metadata` (`title`, `company_id`, `job_id` only — `company` and `apply_url` are often missing)
- Noise from the "Similar Jobs" section appended to the section text
- Flakiness risk from UI layout changes

The current `search_jobs` tool always returns `job_ids: []` because Handshake's React SPA does not render job card links as standard `<a href="/jobs/{id}">` anchors.

**Root cause investigation** confirmed that Handshake uses an internal GraphQL API at `/hs/graphql`. Live testing showed:
- `GetBasicJobDetails(id: ID!)` → returns the full job record (title, description in HTML, salary, location, work type, dates, work auth, etc.)
- `JobSearchQuery(first, after, input)` → returns paginated search results with job IDs and basic job data
- `JobSearchInitialFilterValuesQuery` → returns all filter options (job types, employment types, education levels, school-specific curations, industries, majors, etc.)

A `page.evaluate(fetch('/hs/graphql', ...))` call from within the browser page context succeeds with HTTP 200 using the browser's existing session cookies. No CSRF token required — `Content-Type: application/json` and `X-Requested-With: XMLHttpRequest` are sufficient.

---

## Goals

1. `get_job_details` returns rich structured metadata and a complete (non-truncated) job description
2. `search_jobs` returns a populated `job_ids` list and supports all available filters via IDs
3. New `get_job_search_filters` tool exposes all filter options (static + school-specific) so callers can discover IDs before filtering
4. All three tools fall back gracefully to existing behavior if the GraphQL call fails

---

## Out of scope

- Other tools (`get_employer_details`, `get_student_profile`, `get_event_details`)
- Work location filtering (remote/hybrid/on_site) — the `JobSearchInput` filter key for work location could not be confirmed after exhaustive testing; it is not supported in the GraphQL path
- Employer or student GraphQL APIs

---

## Architecture

### New private method: `_fetch_graphql(query, variables)`

Added to `HandshakeExtractor`. Calls `page.evaluate()` to execute a `fetch('/hs/graphql', ...)` from within the browser context. Omits `None`-valued variable entries before serialization (GraphQL APIs can treat explicit `null` differently from omitted keys). Returns the parsed `data` dict on success, `None` on any error (network, HTTP non-200, missing `data` key). The caller is responsible for fallback logic.

```python
async def _fetch_graphql(self, query: str, variables: dict) -> dict | None:
    ...
```

All query strings live as module-level constants in `extractor.py`. This is the only place that knows about the GraphQL endpoint.

### HTML-to-text conversion

The `description` field in the GraphQL response is HTML. We convert it to plain text using `page.evaluate()` — inject the HTML into a detached `<div>` and read `innerText`. This handles entities, nested elements, and whitespace correctly without adding a dependency.

```python
async def _html_to_text(self, html: str) -> str:
    ...
```

---

## `get_job_details` — new flow

```
scrape_job(job_id)
  │
  ├─ navigate to /jobs/{id}                  (same as today — establishes session context)
  │
  ├─ _fetch_graphql(JOB_DETAILS_QUERY, {id}) ──success──► build metadata + description from JSON
  │                                                         convert HTML description → plain text
  │                                                         return {url, sections, metadata}
  │
  └─ on any GraphQL error ────────────────────fallback──► existing innerText extraction (unchanged)
```

**Single combined query** — one round-trip covering both job details and the `apply_url` field (previously only in `GetExtendedJobDetails`). We write our own minimal query rather than reusing the app's fragment-heavy queries.

### New `metadata` shape

| Field | Source | Notes |
|---|---|---|
| `id` | `job.id` | |
| `title` | `job.title` | |
| `company` | `job.employer.name` | Previously often missing |
| `company_id` | `job.employer.id` | |
| `industry` | `job.employer.industry.name` | New |
| `salary` | formatted string | See salary formatting table below |
| `salary_type` | `job.salaryType.behaviorIdentifier` | `"PAID"` / `"UNPAID"` |
| `work_type` | derived from `hybrid`/`remote`/`onSite` booleans | `"hybrid"` / `"remote"` / `"on_site"` |
| `locations` | `job.locations[].city + state` | list of strings |
| `job_type` | `job.jobType.behaviorIdentifier` | `"INTERNSHIP"`, `"ON_CAMPUS"`, `"JOB"`, etc. |
| `employment_type` | `job.employmentType.behaviorIdentifier` | `"FULL_TIME"`, `"PART_TIME"`, etc. |
| `start_date` | `job.startDate` | ISO date string, omitted if null |
| `end_date` | `job.endDate` | ISO date string, omitted if null |
| `deadline` | `job.expirationDate` | ISO date string |
| `posted_at` | `job.createdAt` | ISO date string |
| `work_auth_required` | `job.studentScreen.workAuthRequired` | bool |
| `accepts_opt` | `job.studentScreen.acceptsOptCandidates` | bool |
| `accepts_cpt` | `job.studentScreen.acceptsCptCandidates` | bool |
| `will_sponsor` | `job.studentScreen.willingToSponsorCandidate` | bool |
| `apply_url` | `job.jobApplySetting.externalUrl` | Previously often missing |

### Salary formatting

`salaryRange` values are in cents (e.g., `3000` → `$30`). `paySchedule.behaviorIdentifier` → short suffix:
- `HOURLY_WAGE` → `"/hr"`
- `ANNUAL_SALARY` → `"/yr"`
- `MONTHLY_STIPEND` → `"/mo"`

Checks run in this order:

| Condition | Formatted `salary` |
|---|---|
| `salaryType.behaviorIdentifier == "UNPAID"` | `"Unpaid"` |
| `salaryRange` is null | omit field entirely |
| `min` and `max` are both null or 0 | omit field entirely |
| `min == max` (and non-zero) | `"$30/hr"` |
| both non-zero and `min != max` | `"$30–35/hr"` |
| only `max` non-zero | `"Up to $35/hr"` |
| only `min` non-zero | `"$30+/hr"` |

### `sections.job_posting`

Contains the plain-text job description only (converted from the HTML `job.description`). **This is an intentional narrowing** — the structured `metadata` fields cover all key data that was previously scattered in the raw text. On fallback, retains current behavior (full page innerText).

### `references` in GraphQL path

Not extracted — there is no page DOM to walk. The `references` key is omitted when GraphQL succeeds. On fallback, `references` are extracted as today.

### Fallback

If `_fetch_graphql` returns `None`, `scrape_job` falls through to the existing `extract_page` + `_extract_job_metadata` path. A `DEBUG` log line records when the fallback is used.

---

## New tool: `get_job_search_filters`

### Purpose

Returns all available filter options for the current user — both static (same for everyone) and dynamic (school-specific curations, user's declared majors). Callers use the returned IDs with `search_jobs`.

### Flow

```
get_job_search_filters()
  │
  ├─ navigate to /job-search             (establishes session context)
  │
  ├─ _fetch_graphql(FILTERS_QUERY, {})  ──success──► build and return filters dict
  │
  └─ on any GraphQL error ───────────────fallback──► return empty dict with error message
```

### Return shape

```json
{
  "job_types": [
    {"id": "3", "name": "Internship", "slug": "INTERNSHIP"},
    {"id": "6", "name": "On Campus Student Employment", "slug": "ON_CAMPUS"},
    {"id": "9", "name": "Job", "slug": "JOB"},
    {"id": "7", "name": "Fellowship", "slug": "FELLOWSHIP"},
    {"id": "4", "name": "Cooperative Education", "slug": "CO_OP"},
    ...
  ],
  "employment_types": [
    {"id": "1", "name": "Full-Time", "slug": "FULL_TIME"},
    {"id": "2", "name": "Part-Time", "slug": "PART_TIME"},
    {"id": "3", "name": "Seasonal", "slug": "SEASONAL"}
  ],
  "education_levels": [
    {"id": "1", "name": "Bachelors"},
    {"id": "2", "name": "Masters"},
    {"id": "3", "name": "Doctorate"},
    ...
  ],
  "salary_types": [
    {"id": "1", "name": "Paid"},
    {"id": "2", "name": "Unpaid"}
  ],
  "pay_schedules": [
    {"id": "1", "name": "Hourly Wage"},
    {"id": "2", "name": "Annual Salary"},
    {"id": "5", "name": "Monthly Stipend"}
  ],
  "remunerations": [
    {"id": "6", "name": "Medical"},
    {"id": "8", "name": "Dental"},
    ...
  ],
  "collections": [
    {"id": "20902", "name": "On-Campus Student Employment"},
    {"id": "20840", "name": "Engineering Career Hub"},
    ...
  ],
  "industries": [
    {"id": "1034", "name": "Internet & Software"},
    {"id": "1029", "name": "Biotech & Life Sciences"},
    ...
  ],
  "job_role_groups": [
    {"id": "64", "name": "Software Developers and Engineers"},
    ...
  ]
}
```

`collections` is school-specific — a CU Boulder user sees CU Boulder's curations; another school's user sees theirs. `job_types`, `employment_types`, `education_levels`, `salary_types`, `pay_schedules`, `remunerations`, `industries`, `job_role_groups` are global (same for all users) but returned dynamically so they stay current if Handshake adds new options.

**No `majors` field** — the API returns only the user's declared majors (20 items for the test user), which is an incomplete picture. Omitting it avoids confusion.

### GraphQL query

Uses `JobSearchInitialFilterValuesQuery` with all `include*` flags set to `true`, plus the `curations` sub-field on `currentUser.institution`. This is the same query the app fires at page load, so it's confirmed stable.

---

## `search_jobs` — new flow and signature

### Signature change

Old: `search_jobs(keywords, location, job_type, employment_type, sort_by, max_pages)`

New:
```python
search_jobs(
    keywords: str,
    # Filter IDs — get from get_job_search_filters
    job_type_ids: list[str] | None = None,        # e.g. ["3"] for Internship
    employment_type_ids: list[str] | None = None, # e.g. ["1"] for Full-Time
    education_level_ids: list[str] | None = None,
    collection_ids: list[str] | None = None,      # school-specific curations
    industry_ids: list[str] | None = None,
    job_role_group_ids: list[str] | None = None,
    remuneration_ids: list[str] | None = None,
    salary_type_ids: list[str] | None = None,
    # Non-ID filters
    location: str | None = None,  # best-effort: appended to query string
    sort_by: str | None = None,   # "relevance" | "date"
    max_pages: int = 3,
)
```

The old `job_type` and `employment_type` string params are **removed**. Callers use `job_type_ids` and `employment_type_ids` with IDs from `get_job_search_filters`. The mapping that was previously in `_JOB_TYPE_MAP` and `_WORK_LOCATION_MAP` is deleted.

**Work location (remote/hybrid/on_site) is not supported** — the `JobSearchInput` filter key could not be identified after exhaustive testing. No parameter for it is added. Noted in the tool docstring.

### Flow

```
search_jobs(keywords, filters..., max_pages)
  │
  ├─ navigate to /job-search?query={keywords}    (establishes session context)
  │
  ├─ for each page (1..max_pages):
  │    _fetch_graphql(JOB_SEARCH_QUERY, {first: 25, after: cursor, input: {filter, sort}})
  │    ──success──► extract job_ids from edges[].node.job.id
  │                 build search_results text from job data
  │                 advance cursor: btoa(str(page_num * 25))
  │                 stop if returned edges < 25 (last page)
  │
  └─ on GraphQL error on page 1 ──fallback──► existing _extract_search_page path
     (GraphQL errors on pages 2+ just stop pagination silently)
```

### GraphQL filter object

```json
{
  "filter": {
    "query": "software engineer",
    "jobTypeIds": ["3"],
    "employmentTypeIds": ["1"],
    "educationLevelIds": ["1", "2"],
    "curationIds": ["20902"],
    "industryIds": ["1034"],
    "jobRoleGroupIds": ["64"],
    "remunerationIds": ["6"],
    "salaryTypeIds": ["1"]
  },
  "sort": {
    "field": "RELEVANCE",
    "direction": "ASC"
  }
}
```

Only non-null filter lists are included (None values are omitted from the GraphQL variables).

### Confirmed filter keys (live-tested)

| Filter | GraphQL key | Tested |
|---|---|---|
| Job type | `jobTypeIds` | ✓ confirmed working |
| Employment type | `employmentTypeIds` | ✓ confirmed working |
| School collection | `curationIds` | ✓ confirmed working |
| Education level | `educationLevelIds` | ✓ confirmed working (from schema) |
| Industry | `industryIds` | ✓ confirmed working (from schema) |
| Job role group | `jobRoleGroupIds` | ✓ confirmed working (from schema) |
| Remuneration | `remunerationIds` | ✓ confirmed working (from schema) |
| Salary type | `salaryTypeIds` | ✓ confirmed working (from schema) |
| Work location | unknown | ✗ not found, not supported |

"Confirmed working (from schema)" means the key appears in the `JobSearchInitialFilterValuesQuery` response with those IDs, and the `JobSearchInput` type accepted it without errors. Live result correctness was verified for `curationIds` (76 results for On-Campus Employment) and `jobTypeIds`.

### Cursor pagination

The app always sends `after` even on page 1 (confirmed by live interception). We follow the same pattern:
- Page 1: `after: btoa("0")` = `"MA=="`
- Page N: `after: btoa(str((N-1) * 25))`

`pageInfo` is `null` in observed responses; do not rely on it. Stop pagination when a page returns fewer than 25 edges.

### `job_ids`

Extracted from `edges[].node.job.id` (string IDs). Previously always `[]`.

### `sections.search_results`

When using GraphQL, each job is one line: `{company} — {title} · {salary} · {job_type} · {location}`. On fallback, retains current behavior (raw innerText).

---

## Error handling

- GraphQL returns `{"errors": [...]}` → treat as failure, fall back
- `fetch` throws (network error) → treat as failure, fall back
- HTTP non-200 → treat as failure, fall back
- `data.job` is null (job not found) → raise `HandshakeScraperException` with clear message

---

## Testing

- Unit tests for `_html_to_text` and `_fetch_graphql` using mocked `page`
- Unit tests for salary formatting logic (all table cases)
- Unit test for cursor generation (`btoa` equivalent in Python: `base64.b64encode(str(n).encode()).decode()`)
- Integration: existing tests should continue to pass (fallback path unchanged)
- No new live-browser tests

---

## Files changed

| File | Change |
|---|---|
| `scraping/extractor.py` | Add `_fetch_graphql`, `_html_to_text`, `JOB_DETAILS_QUERY`, `JOB_SEARCH_QUERY`, `FILTERS_QUERY` constants; modify `scrape_job`, `search_jobs`; add `get_job_search_filters` |
| `tools/job.py` | Add `get_job_search_filters` tool registration; update `search_jobs` signature (remove old params, add ID lists) |
| `CLAUDE.md` | Update tool return format docs; update URL routes section |
| `tests/test_extractor.py` | New file — unit tests for new helpers |

No new dependencies.
