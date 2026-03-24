# Handshake MCP Server

A Model Context Protocol (MCP) server that provides browser-automation-based scraping for [Handshake](https://app.joinhandshake.com) — the leading job/internship platform for students. Built with [Patchright](https://github.com/Kaliber5/patchright) and [FastMCP](https://github.com/jlowin/fastmcp).

## Features

- **Student profiles** — Scrape student profiles by user ID
- **Employer profiles** — Scrape employer overview, jobs, and reviews
- **Job/internship details** — Get full job posting content
- **Event details** — Get career fair and info session details
- **Search** — Search jobs, employers, and events with filters

## Tool Return Format

All scraping tools return:
```json
{
  "url": "string",
  "sections": { "section_name": "raw text content" },
  "references": { "section_name": [{ "kind": "job", "url": "/stu/jobs/123", "text": "..." }] },
  "section_errors": { "section_name": { "error_type": "...", "error_message": "..." } },
  "unknown_sections": ["invalid_section_name"],
  "job_ids": ["123", "456"]
}
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- A Handshake student account

## Installation & Setup

### 1. Install dependencies and browser

```bash
uv sync
uv run patchright install chromium
```

### 2. Login (creates persistent browser profile)

```bash
uv run -m handshake_mcp_server --login --no-headless
```

A browser window will open. Complete the Handshake login (including SSO if needed). The session is saved to `~/.handshake-mcp/profile/` and reused automatically.

### 3. Start the server

```bash
# stdio (for Claude Desktop / MCP clients)
uv run -m handshake_mcp_server

# HTTP mode (for testing)
uv run -m handshake_mcp_server --transport streamable-http --log-level DEBUG
```

## Claude Desktop Configuration

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uvx",
      "args": ["handshake-scraper-mcp"]
    }
  }
}
```

Or using the local development version:

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/handshake-mcp-server",
        "-m",
        "handshake_mcp_server"
      ]
    }
  }
}
```

## Available Tools

### Student
- `get_student_profile(user_id, sections?)` — Get a student's Handshake profile

### Employer
- `get_employer_profile(employer_id, sections?)` — Get employer overview, jobs, reviews
- `search_employers(keywords, max_pages?)` — Search for employers

### Jobs
- `get_job_details(job_id)` — Get full job/internship posting
- `search_jobs(keywords, location?, job_type?, employment_type?, sort_by?, max_pages?)` — Search jobs

### Events
- `get_event_details(event_id)` — Get event details (career fairs, info sessions)
- `search_events(keywords, max_pages?)` — Search for upcoming events

### Session
- `close_session()` — Close the browser session and free resources

## CLI Options

```
uv run -m handshake_mcp_server --help

  --login          Open browser for manual login, save profile, then exit
  --logout         Clear saved authentication profile and exit
  --status         Check current session status and exit
  --no-headless    Run browser in headed (visible) mode
  --transport      MCP transport (stdio | streamable-http)
  --host           Host for HTTP mode (default: 127.0.0.1)
  --port           Port for HTTP mode (default: 8000)
  --log-level      Logging level (DEBUG | INFO | WARNING | ERROR)
```

## Docker

```bash
# Build
docker build -t handshake-mcp-server .

# Login (creates profile on host first)
uv run -m handshake_mcp_server --login --no-headless

# Run with mounted profile
docker run -v ~/.handshake-mcp:/home/pwuser/.handshake-mcp handshake-mcp-server
```

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Run linter
uv run ruff check .

# Run formatter
uv run ruff format .

# Run type checker
uv run ty check

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov
```

## Architecture

Mirrors the [LinkedIn MCP Server](https://github.com/stickerdaniel/linkedin-mcp-server) architecture:

- **Browser**: Patchright persistent Chromium context with session reuse
- **Extraction**: `innerText` from `<main>` element — minimal DOM dependence
- **Sequential execution**: `asyncio.Lock` middleware ensures one tool runs at a time
- **Auth barriers**: URL-based detection of login redirects
- **Rate limiting**: HTTP 429 detection with retry backoff

## License

Apache-2.0
