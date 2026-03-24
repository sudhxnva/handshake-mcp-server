# Setup Wizard Design

**Date:** 2026-03-24
**Status:** Approved

## Problem

Setting up the Handshake MCP server requires knowing several commands in the right order, and the Docker path involves a verbose `docker run` command that is impractical to paste into an IDE config. New users hit a `CredentialsNotFoundError` with no guidance. The goal is to reduce setup to two commands: `uvx handshake-scraper-mcp setup` and a single `claude mcp add-json` line.

## Scope

- Interactive setup wizard (`handshake-mcp-server setup`)
- Auto-trigger wizard on first run when no profile exists
- `docker` subcommand as a thin wrapper for the verbose `docker run` invocation
- Replace `inquirer` with `questionary` + add `rich` for styled output
- No IDE config file writing — the wizard prints the `claude mcp add-json` command

Out of scope: VS Code / Windsurf / Cursor config automation, port management (stdio transport used throughout, no ports).

---

## CLI Shape

Two new subcommands are added alongside existing flags. No breaking changes.

| Command | What it does |
|---|---|
| `handshake-mcp-server setup` | Interactive wizard: login + print `add-json` command |
| `handshake-mcp-server docker` | Thin wrapper: `exec`s `docker run ... --transport stdio --virtual-display` |

Existing flags (`--login`, `--logout`, `--status`, `--transport`, `--vnc-login`, etc.) remain unchanged.

**Auto-trigger:** When the server starts with no subcommand, no flags, and no profile exists, it prompts `"No profile found. Run setup? [Y/n]"` and launches the wizard on confirmation rather than exiting with an error.

---

## Setup Wizard Flow

Entry: `handshake-mcp-server setup` (or auto-triggered).

```
◆  Handshake MCP Server — Setup

? How do you want to run the server?
❯  Docker  (recommended)
   Local
```

### Docker path

1. Check Docker is installed and daemon is running — fail fast with a clear message if not
2. Build the image: `docker compose build` — stream output, show spinner
3. Run VNC login: `docker compose run --rm -p 6080:6080 handshake-mcp --vnc-login`
   - Print the VNC URL (`http://localhost:6080/vnc.html`) prominently
   - Wait for the login subprocess to complete
4. Print the `add-json` command and attempt clipboard copy

### Local path

1. Check if profile already exists — skip login if so, confirm with user
2. Open browser (non-headless) and navigate to Handshake login
3. Wait for `wait_for_manual_login()` to detect successful login
4. Print the `add-json` command and attempt clipboard copy

### Shared exit

Both paths end with:

```
  Run this command to add Handshake to Claude:

  claude mcp add-json handshake '{"command":"uvx","args":["handshake-scraper-mcp","docker"]}'
  ↑ copied to clipboard

  Done. Restart Claude to activate.
```

Local path prints the `uvx` variant without `"docker"`. Clipboard copy is attempted via `pbcopy` (macOS), `xclip`/`xsel` (Linux), `clip` (Windows) — silently skipped if unavailable.

---

## `docker` Subcommand

A one-function wrapper in `cli_main.py`:

```python
def _docker_and_exit() -> None:
    import os
    os.execvp("docker", [
        "docker", "run", "--rm", "-i",
        "-v", "handshake-profile:/home/pwuser/.handshake-mcp",
        "handshake-mcp-server",
        "--transport", "stdio",
        "--virtual-display",
    ])
```

`os.execvp` replaces the Python process with `docker run` — no subprocess wrapper, no PID overhead. Fails immediately with a clear OS error if Docker is not installed.

The resulting MCP configs are:

```json
// Local
{"command": "uvx", "args": ["handshake-scraper-mcp"]}

// Docker
{"command": "uvx", "args": ["handshake-scraper-mcp", "docker"]}
```

---

## Dependencies

| Change | Reason |
|---|---|
| Add `rich>=13.0.0` | Panels, spinners, coloured status lines |
| Add `questionary>=2.0.0` | Interactive prompts — direct modern replacement for `inquirer` |
| Remove `inquirer` | Replaced entirely by `questionary` |

The existing `choose_transport_interactive()` in `cli_main.py` is updated from `inquirer.List` to `questionary.select`.

---

## File Structure

| File | Change |
|---|---|
| `handshake_mcp_server/setup_wizard.py` | New — all wizard logic |
| `handshake_mcp_server/cli_main.py` | Add `setup` / `docker` subcommand dispatch; replace `inquirer`; auto-trigger logic |
| `pyproject.toml` | Add `rich`, `questionary`; remove `inquirer` |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Docker not installed (docker path) | Print clear message with install link, exit 1 |
| Docker daemon not running | Print "Docker is not running — start Docker Desktop", exit 1 |
| `docker compose build` fails | Stream output already shown; print failure message, exit 1 |
| Login timeout / cancelled | Print "Login cancelled — run `setup` again when ready", exit 0 |
| Profile already exists (local path) | Ask "Already logged in — reconfigure anyway? [y/N]", skip login if no |
| `docker` image not built (docker subcommand) | `docker run` will fail with its own error — acceptable, user should run `setup` first |

---

## Success Criteria

- `uvx handshake-scraper-mcp setup` completes end-to-end with no manual intervention beyond the browser/VNC login
- `handshake-mcp-server docker` passes stdio correctly — the MCP server starts and responds to tool calls when launched via the IDE config
- No `inquirer` import remains in the codebase
- Auto-trigger fires when `handshake-mcp-server` is run with no args and no profile
