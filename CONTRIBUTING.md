# Contributing to Handshake MCP Server

Thanks for your interest in contributing! This document covers how to get set up, the architecture you'll be working in, and the checklist for submitting changes.

Please [open an issue](https://github.com/sudhxnva/handshake-mcp-server/issues) first to discuss any feature or bug fix before submitting a PR. This avoids duplicated effort and ensures the change is in scope.

## Table of Contents

- [Getting Started](#getting-started)
- [Architecture Overview](#architecture-overview)
- [Adding a New Tool](#adding-a-new-tool)
- [Adding or Modifying Scraped Sections](#adding-or-modifying-scraped-sections)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Checklist](#pull-request-checklist)

---

## Getting Started

```bash
git clone https://github.com/sudhxnva/handshake-mcp-server
cd handshake-mcp-server

uv sync --group dev
uv run patchright install chromium
uv run -m handshake_mcp_server --login --no-headless
```

Run the full check suite before opening a PR:

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run ty check              # type check
uv run pytest                # tests
```

---

## Architecture Overview

```
handshake_mcp_server/
├── server.py                    # FastMCP app, middleware wiring
├── tools/                       # One file per resource domain
│   ├── job.py
│   ├── employer.py
│   ├── student.py
│   └── event.py
├── scraping/
│   ├── extractor.py             # Core scraping engine (navigation, auth checks, GraphQL)
│   ├── fields.py                # Section config: name → (url_suffix, is_overlay)
│   └── link_metadata.py         # Reference link extraction
├── core/
│   ├── browser.py               # Patchright browser lifecycle
│   ├── auth.py                  # Login / session management
│   ├── utils.py                 # Cloudflare challenge handling, helpers
│   └── exceptions.py            # Typed exception hierarchy
├── sequential_tool_middleware.py # asyncio.Lock — one tool at a time
├── cli_main.py                  # Argument parsing + subcommand dispatch
└── setup_wizard.py              # Interactive setup (Docker / local)
```

**Key invariants:**

- **One section = one navigation.** Each entry in `STUDENT_SECTIONS` / `EMPLOYER_SECTIONS` / etc. in `scraping/fields.py` maps to exactly one `page.goto()` call. Do not make additional navigations inside a section.
- **Minimal DOM dependence.** Extract content via `innerText` on broad elements (`<main>`, `<article>`). Avoid class names tied to Handshake's layout — they change without warning.
- **Sequential execution.** `SequentialToolMiddleware` holds an `asyncio.Lock` so only one tool runs at a time. Do not add concurrency inside individual tools.
- **Verify URLs live.** Handshake is a React SPA — URL patterns can change. Always test against a real logged-in session before adding a new route.

---

## Adding a New Tool

1. **Add the tool function** in the appropriate file under `tools/` (or create a new file for a new domain). Use the `@mcp.tool()` decorator from the injected `mcp` instance.

2. **Register it in `server.py`** if you created a new `tools/` file.

3. **Add scraped sections** to `scraping/fields.py` if the tool needs to navigate to new pages (see below).

4. **Return the standard format:**
   ```python
   {"url": str, "sections": dict[str, str], ...}
   ```
   Optional keys: `metadata`, `references`, `section_errors`, `unknown_sections`, `job_ids`, `employer_ids`, `event_ids`.

5. **Write tests** in `tests/` covering the happy path and common error cases.

---

## Adding or Modifying Scraped Sections

All scrapeable sections are declared in `scraping/fields.py`:

```python
EMPLOYER_SECTIONS: dict[str, tuple[str, bool]] = {
    "overview": ("", False),        # /e/{id}
    "jobs":     ("/jobs", False),   # /e/{id}/jobs
    "posts":    ("/posts", False),  # /e/{id}/posts
}
```

Each entry is `section_name: (url_suffix, is_overlay)`.

- `url_suffix` is appended to the base resource URL.
- `is_overlay` is reserved for future use (modal/drawer content that doesn't require a full navigation).

**Before adding a new section:**

1. Verify the URL exists and returns the expected content on live Handshake.
2. Check whether Handshake's GraphQL API (`/hs/graphql`) already exposes the data — prefer GraphQL over scraping when available (see `extractor.py::_fetch_graphql`).
3. Add a corresponding `parse_*_sections` entry if the tool accepts a `sections` parameter.

---

## Code Style

- **Formatter / linter:** [Ruff](https://docs.astral.sh/ruff/). Run `uv run ruff format .` and `uv run ruff check . --fix` before committing.
- **Type checker:** `ty` (`uv run ty check`). All new code must pass without errors.
- **Line length:** `E501` is suppressed globally (pre-existing violations in JS strings made per-line suppression unworkable) — but keep new lines reasonably short.
- **Commit messages:** Follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`. Subject under 50 characters, imperative mood.
- **No docstrings required** on internal helpers, but complex logic should have inline comments.

---

## Testing

Tests live in `tests/` and use `pytest`. The test suite runs without a live browser — use mocks/fixtures for any Patchright calls.

```bash
uv run pytest            # run all tests
uv run pytest --cov      # with coverage report
uv run pytest -x         # stop on first failure
uv run pytest -k "name"  # run tests matching a pattern
```

When fixing a bug, add a regression test that would have caught it.

---

## Pull Request Checklist

Before submitting:

- [ ] `uv run ruff check .` passes (no lint errors)
- [ ] `uv run ruff format --check .` passes (no formatting diff)
- [ ] `uv run ty check` passes (no type errors)
- [ ] `uv run pytest` passes
- [ ] New behaviour is covered by tests
- [ ] New or changed URLs verified against live Handshake
- [ ] `CLAUDE.md` updated if you changed URL routes, return format, or key invariants
- [ ] PR title follows Conventional Commits format

If your PR changes the public tool interface (parameters, return format), update the relevant section in `README.md` as well.
