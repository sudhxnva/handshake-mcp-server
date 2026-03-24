# Setup Wizard Design

**Date:** 2026-03-24
**Status:** Approved

## Problem

Setting up the Handshake MCP server requires knowing several commands in the right order, and the Docker path involves a verbose `docker run` command that is impractical to paste into an IDE config. New users hit a `CredentialsNotFoundError` with no guidance. The goal is to reduce setup to two commands: `uvx handshake-mcp-server setup` and a single `claude mcp add-json` line.

## Scope

- Interactive setup wizard (`handshake-mcp-server setup`)
- Auto-trigger wizard on first run when no profile exists (TTY only — see below)
- `docker` subcommand as a thin wrapper for the verbose `docker run` invocation
- Replace `inquirer` with `questionary` + add `rich` for styled output
- No IDE config file writing — the wizard prints the `claude mcp add-json` command

Out of scope: VS Code / Windsurf / Cursor config automation. No port management — stdio transport is used for both local and Docker runtime paths.

**Assumption:** users run via `uvx`. The printed `claude mcp add-json` commands are scoped to `uvx`. Users on a cloned repo get a note to adapt the command.

---

## CLI Shape

Two new subcommands are added alongside existing flags. No breaking changes.

| Command | What it does |
|---|---|
| `handshake-mcp-server setup` | Interactive wizard: login + print `add-json` command |
| `handshake-mcp-server docker` | Thin wrapper: `exec`s `docker run ... --transport stdio --virtual-display` |

Existing flags (`--login`, `--logout`, `--status`, `--transport`, `--vnc-login`, etc.) remain unchanged.

**Auto-trigger:** When the server starts with no subcommand, no flags, and no profile exists, AND `sys.stdin.isatty()` is True, it prompts `"No profile found. Run setup? [Y/n]"` and launches the wizard on confirmation. In non-TTY contexts (e.g., launched by Claude Desktop), the auto-trigger is skipped entirely — the server falls through to the existing `CredentialsNotFoundError` exit with an improved message: `"No profile found. Run: handshake-mcp-server setup"`.

---

## Volume Naming Contract

The wizard (Docker path) and the `docker` subcommand both reference the named volume `handshake-profile`. To prevent Compose from prefixing the name with the project directory, `docker-compose.yml` declares the volume with an explicit `name:` field:

```yaml
volumes:
  handshake-profile:
    name: handshake-profile
```

This guarantees the volume name is always `handshake-profile` regardless of the Compose project name, matching the `os.execvp` argument exactly.

---

## Setup Wizard Flow

Entry: `handshake-mcp-server setup` (or auto-triggered on TTY).

```
◆  Handshake MCP Server — Setup

? How do you want to run the server?
❯  Docker  (recommended)
   Local
```

### Docker path

1. Check Docker is installed and daemon is running — fail fast with a clear message if not
2. Build the image: `docker compose build` — stream Docker output directly; suppress behind a spinner only if output is clean, show raw output on failure
3. Run VNC login: `docker compose run --rm -p 6080:6080 handshake-mcp --vnc-login`
   - Check port 6080 is free before running — if not, print `"Port 6080 is in use. Stop the conflicting service and run setup again."` and exit 1
   - Print the VNC URL (`http://localhost:6080/vnc.html`) prominently
   - Wait for the login subprocess to complete
4. Print the `add-json` command and attempt clipboard copy

### Local path

1. Check if profile already exists — if so, ask `"Already logged in — re-run login anyway? [y/N]"` and skip login if no
2. Open browser (non-headless) and navigate to Handshake login
3. Wait for `wait_for_manual_login()` to detect successful login
4. Print the `add-json` command and attempt clipboard copy

### Shared exit

Both paths end with:

```
  Run this command to add Handshake to Claude:

  claude mcp add-json handshake '{"command":"uvx","args":["handshake-mcp-server","docker"]}'
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

`os.execvp` replaces the Python process with `docker run` — no subprocess wrapper, no PID overhead, signal handling falls naturally to Docker. Fails immediately with `FileNotFoundError` if Docker is not installed.

The `--transport stdio` flag is explicit for clarity; it matches the server default but makes the intent obvious.

The resulting MCP configs:

```json
// Local
{"command": "uvx", "args": ["handshake-mcp-server"]}

// Docker
{"command": "uvx", "args": ["handshake-mcp-server", "docker"]}
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
| `docker-compose.yml` | Add `name: handshake-profile` to volume definition |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Docker not installed (docker path) | Print clear message with install link, exit 1 |
| Docker daemon not running | Print `"Docker is not running — start Docker Desktop"`, exit 1 |
| `docker compose build` fails | Stream output already shown; print failure message, exit 1 |
| Port 6080 already in use (VNC login step) | Print `"Port 6080 is in use. Stop the conflicting service and run setup again."`, exit 1 |
| Login timeout / cancelled | Print `"Login cancelled — run setup again when ready"`, exit 0. This is intentional: the user made a deliberate choice to cancel, which is not an error. This differs from `--login --no-headless` which exits 1 on failure — that distinction is acknowledged. |
| Profile already exists (local path) | Ask `"Already logged in — re-run login anyway? [y/N]"`, skip if no |
| `docker` image not built (docker subcommand) | `docker run` fails with its own error — acceptable; user should run `setup` first |
| Non-TTY auto-trigger | Skip wizard entirely; exit 1 with message pointing to `setup` |

---

## Success Criteria

- `uvx handshake-mcp-server setup` completes end-to-end on both paths with no manual intervention beyond the browser/VNC login step
- `handshake-mcp-server docker` correctly passes stdio: running `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | handshake-mcp-server docker` produces a valid JSON-RPC initialize response
- No `inquirer` import remains in the codebase
- Auto-trigger fires in TTY context when no profile exists; does not fire in non-TTY context
- Volume name `handshake-profile` resolves consistently between `docker compose run` (wizard) and `docker run` (subcommand)
