# Design: GraphQL-First Job Details & Search

**Date:** 2026-03-24
**Branch:** notch-technosaurus
**Scope:** `get_job_details` and `search_jobs` tools

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

A `page.evaluate(fetch('/hs/graphql', ...))` call from within the browser page context succeeds with HTTP 200 using the browser's existing session cookies. No CSRF token required — `Content-Type: application/json` and `X-Requested-With: XMLHttpRequest` are sufficient.

---

## Goals

1. `get_job_details` returns rich structured metadata and a complete (non-truncated) job description
2. `search_jobs` returns a populated `job_ids` list
3. Both tools fall back gracefully to the existing innerText approach if the GraphQL call fails

---

## Out of scope

- Other tools (`get_employer_details`, `get_student_profile`, `get_event_details`) — not investigated, not changed
- Search result sorting and filter improvements
- Employer or student GraphQL APIs

---

## Architecture

### New private method: `_fetch_graphql(query, variables)`

Added to `HandshakeExtractor`. Calls `page.evaluate()` to execute a `fetch('/hs/graphql', ...)` from within the browser context. Returns the parsed `data` dict on success, `None` on any error (network, HTTP non-200, missing `data` key). The caller is responsible for fallback logic.

```python
async def _fetch_graphql(self, query: str, variables: dict) -> dict | None:
    ...
```

This method is the only place that knows about the GraphQL endpoint. All query strings live as module-level constants in `extractor.py`.

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

**Single combined query** — one round-trip that covers both `GetBasicJobDetails` and the `apply_url` field (previously only in `GetExtendedJobDetails`). We write our own minimal query rather than reusing the app's fragment-heavy queries.

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
| `job_type` | `job.jobType.behaviorIdentifier` | `"INTERNSHIP"`, `"FULL_TIME"`, etc. |
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

Contains the plain-text job description only (converted from the HTML `job.description`). No longer contains the full page scrape (search list, similar jobs, employer boilerplate). **This is an intentional narrowing** — the structured `metadata` fields cover all key data that was previously scattered in the raw text. On fallback, retains current behavior (full page innerText).

### `references` in GraphQL path

`references` are **not** extracted in the GraphQL path — there is no page DOM to walk for anchor links. The `references` key is omitted from the response when GraphQL succeeds. On fallback, `references` are extracted as today. This is consistent with the narrower, more focused output intent of the GraphQL path.

### Fallback

If `_fetch_graphql` returns `None`, `scrape_job` falls through to the existing `extract_page` + `_extract_job_metadata` path. The fallback is the current implementation, unchanged. A `DEBUG` log line records when the fallback is used.

---

## `search_jobs` — new flow

```
search_jobs(keywords, ...)
  │
  ├─ navigate to /job-search?query=...       (same as today — establishes session context)
  │
  ├─ for each page (1..max_pages):
  │    _fetch_graphql(JOB_SEARCH_QUERY, {first: 25, after: cursor, input: filters})
  │    ──success──► extract job_ids from edges[].node.job.id
  │                 build search_results text from job data
  │                 advance cursor: btoa(str(page * 25))
  │                 stop if no new IDs
  │
  └─ on any GraphQL error (first page) ──fallback──► existing _extract_search_page path
     (subsequent page errors just stop pagination)
```

### Cursor pagination

`JobSearchQuery` uses relay-style cursor pagination with a base64-encoded page offset. The app always sends `after` even on page 1 (confirmed by live request interception — the app sent `after: "MA=="` for its first request). We follow the same pattern:
- Page 1: `after: "MA=="` (base64 of `"0"`)
- Page 2: `after: "MjU="` (base64 of `"25"`)
- Page N: `after: btoa(str((N-1) * 25))`

`pageInfo` is `null` in observed responses; do not rely on it. Stop pagination when a page returns 0 new IDs or fewer edges than `first`.

### `job_ids`

Extracted from `edges[].node.job.id` (string IDs). Previously always `[]`.

### `sections.search_results`

When using GraphQL, each job gets one line: `{company} — {title} · {salary} · {job_type} · {location} · {deadline_or_age}`. This is more compact and consistent than the raw page scrape. On fallback, retains current behavior (raw innerText).

### Filter mapping

The existing `job_type`, `employment_type`, `sort_by`, `location` parameters map to `JobSearchInput` as follows.

**`job_type` maps to two different GraphQL filters** (confirmed via `GetFilterValues` query). The tool's `job_type` values mix job *category* and work *schedule*, which are separate concepts in the GraphQL schema:

| Tool slug | GraphQL filter | ID |
|---|---|---|
| `internship` | `input.filter.jobTypeIds` | `["3"]` |
| `on_campus` | `input.filter.jobTypeIds` | `["6"]` |
| `full_time` | `input.filter.employmentTypeIds` | `["1"]` |
| `part_time` | `input.filter.employmentTypeIds` | `["2"]` |

`full_time` and `part_time` are NOT passed to `jobTypeIds`. A `DEBUG` log is emitted when `full_time` or `part_time` is used: `"job_type=full_time mapped to employmentTypeIds in GraphQL path"`.

**`employment_type` (work location) → not mapped in GraphQL path.** The GraphQL `employmentTypeIds` field maps to `FULL_TIME`/`PART_TIME`/`SEASONAL`, which is a different concept from work location (`remote`/`hybrid`/`on_site`). The work location filter in `JobSearchInput` requires additional schema investigation. **Decision: in the GraphQL path, `employment_type` is silently ignored (not passed to the API). A `DEBUG`-level log line is emitted: `"employment_type filter not supported in GraphQL path, ignored"`. The tool docstring is updated to note this limitation. The fallback path applies all filters via URL params as today.**

**`sort_by` → `input.sort`**:
- `"relevance"` → `{field: "RELEVANCE", direction: "ASC"}`
- `"date"` → `{field: "POSTED_DATE", direction: "DESC"}`

**`location` → `input.filter.locationId`**: Location IDs are dynamic and can't be hardcoded. Pass `location` as part of `input.filter.query` string (append it to the keyword) as a best-effort approach. Fallback path handles it accurately via URL params.

---

## Error handling

- GraphQL returns `{"errors": [...]}` instead of `{"data": {...}}` → treat as failure, fall back
- `fetch` throws (network error) → treat as failure, fall back
- HTTP non-200 → treat as failure, fall back
- `data.job` is null (job not found) → raise `HandshakeScraperException` with clear message (same behavior as current 404)

---

## Testing

- Unit tests for `_html_to_text` and `_fetch_graphql` using mocked `page`
- Unit tests for salary formatting logic (cents → display string)
- Unit test for cursor generation
- Integration: existing tests should continue to pass (fallback path unchanged)
- No new live-browser tests (those are covered manually per CLAUDE.md)

---

## Files changed

| File | Change |
|---|---|
| `scraping/extractor.py` | Add `_fetch_graphql`, `_html_to_text`, `JOB_DETAILS_QUERY`, `JOB_SEARCH_QUERY` constants; modify `scrape_job`, `search_jobs` |
| `CLAUDE.md` | Update tool return format docs to reflect new `metadata` fields |
| `tests/test_extractor.py` | New unit tests for new helpers (create new file — does not already exist) |

No new dependencies.
