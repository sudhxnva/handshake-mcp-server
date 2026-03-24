# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

- Use `uv` for dependency management: `uv sync` (dev: `uv sync --group dev`)
- Lint: `uv run ruff check .` (auto-fix with `--fix`)
- Format: `uv run ruff format .`
- Type check: `uv run ty check` (using ty, not mypy)
- Tests: `uv run pytest` (with coverage: `uv run pytest --cov`)
- Run server locally: `uv run -m handshake_mcp_server --no-headless`
- Docker build: `docker build -t handshake-mcp-server .`
- Install browser: `uv run patchright install chromium`

## CLI Flags

- `--login` — Open browser, navigate to Handshake login, wait for manual login, then exit
- `--logout` — Clear saved authentication profile and exit
- `--status` — Check current session status and exit
- `--no-headless` — Run browser in headed (visible) mode
- `--virtual-display` — Run browser via Xvfb virtual display (Linux only). Bypasses Cloudflare headless detection. On macOS, falls back to `--no-headless`.
- `--vnc-login` — Start a noVNC server for web-based login on headless Linux. Requires `apt install xvfb x11vnc novnc`
- `--vnc-port` — Port for noVNC web server during `--vnc-login` (default: 6080)
- `--transport` — MCP transport: `stdio` (default) or `streamable-http`
- `--host` / `--port` — Host/port for `streamable-http` transport (default: 127.0.0.1:8000)

## Cloudflare Bot Detection

Patchright does **not** bypass headless detection when `headless=True`. This is because `headless=True` selects the `chromium-headless-shell` binary, which is trivially fingerprinted. Fixes:

- **Local (macOS):** Use `--no-headless` to open a real browser window.
- **Linux server:** Use `--virtual-display` to run Xvfb (virtual framebuffer). Chrome runs non-headless against the virtual display — no headless binary, no fingerprint.
- **Docker:** The default CMD uses `--virtual-display`. See Docker section below.

Cloudflare challenges (`?cf_challenge=1`) are handled in `core/utils.py::wait_for_cf_challenge()`, called in `scraping/extractor.py::_goto_with_auth_checks()`. If unresolved, a `RateLimitError` is raised.

## CLI Subcommands

Subcommands are dispatched before argparse by checking `sys.argv[1]` — they do NOT appear in `--help`.

- `setup` — interactive wizard: Docker or Local path, login, prints `claude mcp add-json` command
- `docker` — `os.execvp` into `docker run --rm -i --transport stdio --virtual-display` (used as MCP entrypoint by IDE)
- `docker-clean` — removes `handshake-mcp-server` image and `handshake-profile` volume (force-stops containers first)

## Docker Deployment

Prefer the wizard (`handshake-mcp-server setup`) over manual commands. For manual use:

```bash
docker compose build
docker compose run --rm -p 6080:6080 handshake-mcp --vnc-login  # one-time login
docker compose up -d                                              # run HTTP server
```

Named volume `handshake-profile` uses explicit `name:` in `docker-compose.yml` to prevent Compose from prefixing it with the project directory.

The default CMD uses `--virtual-display` to bypass Cloudflare headless detection inside the container.

## questionary in async contexts

`questionary.ask()` calls `asyncio.run()` internally — crashes with `RuntimeError: cannot be called from a running event loop` if used inside an `async` function. Always use `await question.ask_async()` inside async functions; `.ask()` is only safe in sync contexts.

## Chromium Profile Lock Files

Chromium writes `SingletonLock`, `SingletonCookie`, `SingletonSocket` to the profile dir and doesn't clean up on container kill. `core/browser.py::start()` removes these before launching. If you see "profile is in use by another process", these files are the cause.

## Linting

`E501` (line too long) is suppressed globally in `pyproject.toml` — pre-existing violations in JS strings and help text made per-line suppression unworkable.

## Handshake URL Routes

Handshake is a React SPA — URL patterns change. Current verified routes:

| Resource | URL pattern |
|---|---|
| Student profile | `/users/{id}` (redirects to `/profiles/{id}`) |
| Employer profile | `/e/{id}` |
| Employer jobs | `/e/{id}/jobs` |
| Employer posts | `/e/{id}/posts` |
| Job detail | `/jobs/{id}` |
| Job search | `/job-search?{params}` |
| Employer search | `/employer-search?query={q}&per_page={n}` |
| Events | `/stu/events` (no query param support) |
| Event detail | `/stu/events/{id}` |

Always verify URLs against live Handshake before adding new sections. The SPA can redirect or change routes without warning.

## Handshake GraphQL API

Handshake exposes an internal Apollo GraphQL API at `/hs/graphql`. It uses existing session cookies — no CSRF token needed; `Content-Type: application/json` + `X-Requested-With: XMLHttpRequest` is sufficient.

Queries are executed via `page.evaluate(fetch('/hs/graphql', ...))` from the Playwright browser context (`scraping/extractor.py::_fetch_graphql`). Navigate to a Handshake page first to establish session context before calling GraphQL.

Key gotchas:
- **Salary values are in cents** — GraphQL returns `3000` for $30. Divide by 100 to get dollars.
- **`Institution` is a union type** — direct field selections on `currentUser.institution` fail with "Selections can't be made directly on unions". Use inline fragments (`... on ConcreteType { }`) or avoid the field.
- **Cursor pagination** — `after` is `base64(str((page - 1) * 25))`. Page 1 uses `after = base64("0")`, not omitting it.

## Scraping Rules

- **One section = one navigation.** Each entry in `STUDENT_SECTIONS` / `EMPLOYER_SECTIONS` (`scraping/fields.py`) maps to exactly one page navigation.
- **Minimize DOM dependence.** Prefer innerText and URL navigation over DOM selectors. When DOM access is unavoidable, use minimal generic selectors (`a[href*="/jobs/"]`) — never class names tied to Handshake's layout.
- **Verify URLs live.** Handshake is a React SPA — URL patterns may not match assumptions. Always test against live Handshake before adding new sections.

## Tool Return Format

All scraping tools return: `{url, sections: {name: raw_text}}`.

Optional additional keys:
- `metadata: {id, title, company, company_id, industry, salary, salary_type, work_type, locations, job_type, employment_type, start_date, end_date, deadline, posted_at, work_auth_required, accepts_opt, accepts_cpt, will_sponsor, apply_url}` (get_job_details only, GraphQL path) — all fields optional, present when available
- `jobs: [{id, title, company, job_type, employment_type, salary, locations}]` (search_jobs only) — card-level metadata; use job_ids with get_job_details for full details
- `references: {section_name: [{kind, url, text?, context?}]}` — Handshake URLs are relative paths
- `section_errors: {section_name: {error_type, error_message}}`
- `unknown_sections: [name, ...]`
- `job_ids: [id, ...]` (search_jobs only)
- `employer_ids: [id, ...]` (search_employers only)
- `event_ids: [id, ...]` (search_events only)

`get_job_search_filters` returns a flat dict (no `url`/`sections` wrapper):
`{job_types, employment_types, education_levels, salary_types, pay_schedules, remunerations, industries, job_role_groups}` — each a list of `{id, name}` (job_types and employment_types also include `slug`). `collections` is not returned because Handshake's `Institution` GraphQL type is a union.

## Verifying Bug Reports

Always verify scraping bugs end-to-end against live Handshake. Use `uv run`, not `uvx`. Assume a valid login profile exists at `~/.handshake-mcp/profile/`.

```bash
# Start server
uv run -m handshake_mcp_server --transport streamable-http --log-level DEBUG

# Initialize MCP session (grab Mcp-Session-Id from response headers)
curl -s -D /tmp/mcp-headers -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# Extract the session ID from saved headers
SESSION_ID=$(grep -i 'Mcp-Session-Id' /tmp/mcp-headers | awk '{print $2}' | tr -d '\r')

# Call a tool
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_jobs","arguments":{"keywords":"software engineer"}}}'
```

## Release Process

See [`docs/publishing.md`](docs/publishing.md) for PyPI setup (one-time Trusted Publishing configuration).

```bash
git checkout master && git pull
uv version --bump minor   # or: major, patch
git add pyproject.toml uv.lock
git commit -m "chore: bump version to X.Y.Z"
git push
git tag vX.Y.Z
git push origin vX.Y.Z   # triggers publish.yml → PyPI
```

## Commit Messages

- Follow conventional commits: `type(scope): subject`
- Types: feat, fix, docs, style, refactor, test, chore, perf, ci
- Keep subject <50 chars, imperative mood
