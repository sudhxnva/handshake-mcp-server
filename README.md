# Handshake MCP Server

<p align="left">
  <a href="https://pypi.org/project/handshake-mcp-server/" target="_blank"><img src="https://img.shields.io/pypi/v/handshake-mcp-server?color=blue" alt="PyPI"></a>
  <a href="https://github.com/sudhxnva/handshake-mcp-server/actions/workflows/ci.yml" target="_blank"><img src="https://github.com/sudhxnva/handshake-mcp-server/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI Status"></a>
  <a href="https://github.com/sudhxnva/handshake-mcp-server/actions/workflows/publish.yml" target="_blank"><img src="https://github.com/sudhxnva/handshake-mcp-server/actions/workflows/publish.yml/badge.svg" alt="Publish"></a>
  <a href="https://github.com/sudhxnva/handshake-mcp-server/blob/master/LICENSE" target="_blank"><img src="https://img.shields.io/badge/License-Apache%202.0-brightgreen?labelColor=32383f" alt="License"></a>
</p>

Through this Handshake MCP server, AI assistants like Claude can connect to your [Handshake](https://app.joinhandshake.com) account — the leading job and internship platform for students. Search jobs, browse employers, explore events, and pull student or employer profiles, all from your AI chat.

## Installation Methods

[![uvx](https://img.shields.io/badge/uvx-Quick_Install-de5fe9?style=for-the-badge)](#-uvx-setup-recommended)
[![Docker](https://img.shields.io/badge/Docker-Headless_Server-008fe2?style=for-the-badge&logo=docker&logoColor=white)](#-docker-setup)
[![Development](https://img.shields.io/badge/Development-Local-ffdc53?style=for-the-badge&logo=python&logoColor=black)](#-local-setup-develop--contribute)

<!-- TODO: Add demo video here -->

## Usage Examples

```
Find software engineering internships at companies in San Francisco
```

```
What events are coming up on Handshake this week?
```

```
Get details for job posting 12345678 — does it sponsor visas?
```

```
Show me the employer profile for Google on Handshake
```

## Features & Tools

| Tool | Description | Status |
|------|-------------|--------|
| `get_student_profile` | Get a student's profile by user ID (education, experience, skills, etc.) | Working |
| `get_employer_profile` | Get employer overview, open jobs, and reviews | Working |
| `search_employers` | Search for employers by keyword | Working |
| `get_job_details` | Get full job/internship posting with metadata (salary, visa, dates) | Working |
| `search_jobs` | Search jobs with keyword, location, type, and sort filters | Working |
| `get_event_details` | Get event details for career fairs and info sessions | Working |
| `search_events` | Search for upcoming events | Working |
| `close_session` | Close the browser session and free resources | Working |

> [!IMPORTANT]
> Handshake is a **student-gated platform** — you must have a valid student account and log in before tools will work. Run `uvx handshake-mcp-server --login` (or the setup wizard) to authenticate.

<br/>

## 🚀 uvx Setup (Recommended)

**Prerequisites:** [Install uv](https://docs.astral.sh/uv/getting-started/installation/) and a Handshake student account.

### 1. Run the setup wizard

```bash
uvx handshake-mcp-server setup
```

The wizard asks whether you want Docker or local mode, handles login, and prints the exact `claude mcp add-json` command to register the server with your MCP client.

### 2. Register with your MCP client

After setup, paste the command the wizard printed. For example:

```bash
# Local mode (opens a browser window on your machine)
claude mcp add-json handshake '{"command":"uvx","args":["handshake-mcp-server"]}'

# Docker mode (runs headless in a container)
claude mcp add-json handshake '{"command":"uvx","args":["handshake-mcp-server","docker"]}'
```

Or add manually to your MCP client config (`claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uvx",
      "args": ["handshake-mcp-server"]
    }
  }
}
```

Restart your MCP client. Done.

> [!NOTE]
> The server keeps a single Chromium browser open for its entire lifetime — this avoids re-authentication overhead and makes subsequent tool calls much faster. On macOS with `--no-headless`, a browser window stays visible. This is normal.

### uvx Setup Help

<details>
<summary><b>🔧 CLI Options</b></summary>

| Flag | Description |
|------|-------------|
| `--login` | Open browser for manual login, save profile, then exit |
| `--logout` | Clear saved authentication profile and exit |
| `--status` | Check current session status and exit |
| `--no-headless` | Run browser in headed (visible) mode |
| `--virtual-display` | Run via Xvfb virtual framebuffer (Linux only) |
| `--vnc-login` | Start a noVNC web server for browser-based login |
| `--vnc-port` | Port for noVNC server (default: 6080) |
| `--transport` | MCP transport: `stdio` (default) or `streamable-http` |
| `--host` | HTTP host (default: 127.0.0.1) |
| `--port` | HTTP port (default: 8000) |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` (default: WARNING) |

**HTTP mode example:**

```bash
uvx handshake-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000
```

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Login issues:**

- Make sure you have a valid Handshake student account
- Handshake may show a CAPTCHA on first login — `--login` opens a browser so you can solve it manually
- If your session expires, re-run `uvx handshake-mcp-server --login`

**Cloudflare detection:**

- Handshake uses Cloudflare bot protection. On macOS, always use `--no-headless` (the setup wizard does this automatically)
- On Linux, use `--virtual-display` to avoid the headless fingerprint
- See [Cloudflare Bot Detection](#cloudflare-bot-detection) for details

**Session issues:**

- Browser profile is stored at `~/.handshake-mcp/profile/`
- Run `uvx handshake-mcp-server --status` to check if your session is still valid
- Run `uvx handshake-mcp-server --logout` to clear the profile and start fresh

**Installation issues:**

- Ensure uv is installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Install the Chromium browser: `uvx handshake-mcp-server` will install it automatically on first run

</details>

<br/>

## 🐳 Docker Setup

**Prerequisites:** [Docker](https://www.docker.com/get-started/) installed and running.

Docker runs the server headless inside a container using an Xvfb virtual display — no browser window on your machine. The browser profile (cookies/session) is stored in a named Docker volume (`handshake-profile`) and persists across container restarts.

### 1. One-time login

```bash
docker compose build
docker compose run --rm -p 6080:6080 handshake-mcp --vnc-login
```

Open `http://localhost:6080/vnc.html` in your browser and log into Handshake manually.

### 2. Start the server

```bash
docker compose up -d
```

The MCP server is now available at `http://127.0.0.1:8000/mcp` (streamable-http transport).

### 3. Configure your MCP client

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uvx",
      "args": ["handshake-mcp-server", "docker"]
    }
  }
}
```

The `docker` subcommand `exec`s into `docker run --rm -i` using stdio transport, which is the standard way for MCP clients to talk to containerized servers.

### Docker Setup Help

<details>
<summary><b>🔧 Useful Commands</b></summary>

```bash
docker compose logs -f                              # tail server logs
docker compose run --rm handshake-mcp --status      # check session validity
docker compose down                                 # stop the server
uvx handshake-mcp-server docker-clean               # remove image and volume (full reset)
```

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Login issues:**

- Open `http://localhost:6080/vnc.html` within 5 minutes of starting `--vnc-login`
- If the session expires, repeat the one-time login step
- Chromium may leave lock files (`SingletonLock`) in the profile dir after a container kill — the server cleans these up automatically on next start

**Docker issues:**

- Check Docker is running: `docker ps`
- Rebuild after pulling new code: `docker compose build`
- Full reset: `uvx handshake-mcp-server docker-clean` removes the image and named volume

</details>

<br/>

## 🐍 Local Setup (Develop & Contribute)

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture guidelines and the PR checklist. Please [open an issue](https://github.com/sudhxnva/handshake-mcp-server/issues) first to discuss your change before submitting a PR.


**Prerequisites:** [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/) installed.

```bash
# 1. Clone the repository
git clone https://github.com/sudhxnva/handshake-mcp-server
cd handshake-mcp-server

# 2. Install dependencies
uv sync --group dev

# 3. Install the Chromium browser
uv run patchright install chromium

# 4. Log in (opens a browser window)
uv run -m handshake_mcp_server --login --no-headless

# 5. Start the server
uv run -m handshake_mcp_server --no-headless
```

### Local Setup Help

<details>
<summary><b>🔧 Development Commands</b></summary>

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # lint + auto-fix
uv run ruff format .         # format
uv run ty check              # type check
uv run pytest                # tests
uv run pytest --cov          # tests with coverage
```

**Claude Desktop config for local dev:**

```json
{
  "mcpServers": {
    "handshake": {
      "command": "uv",
      "args": ["--directory", "/path/to/handshake-mcp-server", "run", "-m", "handshake_mcp_server"]
    }
  }
}
```

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Scraping issues:**

- Use `--no-headless` to watch browser actions and debug scraping problems
- Add `--log-level DEBUG` for verbose logging

**Python/Patchright issues:**

- Requires Python 3.12+: `python --version`
- Reinstall Patchright: `uv run patchright install chromium`
- Reinstall dependencies: `uv sync --reinstall`

</details>

<br/>

## Cloudflare Bot Detection

Patchright does **not** bypass Cloudflare's headless fingerprint when `headless=True` — that mode uses the `chromium-headless-shell` binary, which is trivially detected.

| Environment | Solution |
|---|---|
| macOS (local) | Use `--no-headless` — opens a real browser window |
| Linux server | Use `--virtual-display` — Chrome runs non-headless against an Xvfb virtual display |
| Docker | Default CMD already uses `--virtual-display` |

Cloudflare challenge pages are detected automatically and raise a `RateLimitError` if unresolved.

<br/>

## Tool Return Format

All scraping tools return:

```json
{
  "url": "string",
  "sections": { "section_name": "raw text content" },
  "references": { "section_name": [{ "kind": "job", "url": "/jobs/123", "text": "..." }] },
  "section_errors": { "section_name": { "error_type": "...", "error_message": "..." } },
  "unknown_sections": ["invalid_section_name"]
}
```

`get_job_details` also returns a `metadata` key with structured fields:

```json
{
  "metadata": {
    "id": "123", "title": "Software Engineer Intern", "company": "Acme Corp",
    "salary": 3000, "salary_type": "hourly",
    "work_type": "hybrid", "locations": ["San Francisco, CA"],
    "job_type": "internship", "employment_type": "part_time",
    "start_date": "2025-06-01", "end_date": "2025-08-31",
    "deadline": "2025-03-01", "posted_at": "2025-01-15",
    "work_auth_required": false, "accepts_opt": true, "accepts_cpt": false,
    "will_sponsor": true, "apply_url": "https://..."
  }
}
```

> [!NOTE]
> Salary values from the GraphQL API are in **cents** — divide by 100 to get dollars.

`search_jobs` also returns `jobs` (card-level metadata list) and `job_ids`. `search_employers` returns `employer_ids`. `search_events` returns `event_ids`.

<br/>

## Acknowledgements

Built with [FastMCP](https://gofastmcp.com/) and [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Inspired by the architecture of [linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server).

Use in accordance with [Handshake's Terms of Service](https://joinhandshake.com/tos/). Web scraping may violate platform terms. This tool is intended for personal use only.

## License

Apache-2.0 — see [LICENSE](LICENSE).
