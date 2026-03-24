"""Interactive setup wizard for first-time Handshake MCP Server configuration."""

import shutil
import socket
import subprocess
import sys
from typing import Literal

import questionary
from rich.console import Console
from rich.panel import Panel

from handshake_mcp_server.browser_manager import profile_exists

console = Console()

_MCP_COMMANDS: dict[str, str] = {
    "docker": (
        "claude mcp add-json handshake "
        '\'{"command":"uvx","args":["handshake-scraper-mcp","docker"]}\''
    ),
    "local": (
        'claude mcp add-json handshake \'{"command":"uvx","args":["handshake-scraper-mcp"]}\''
    ),
}


def _check_docker() -> tuple[bool, str]:
    """Return (True, '') if Docker is installed and daemon is running."""
    if shutil.which("docker") is None:
        return False, "Docker is not installed. Install it from https://docs.docker.com/get-docker/"
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, "Docker is not running — start Docker Desktop and try again."
        return True, ""
    except (subprocess.TimeoutExpired, OSError):
        return False, "Docker is not responding — start Docker Desktop and try again."


def _is_port_free(port: int) -> bool:
    """Return True if the given TCP port is available on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def _copy_to_clipboard(text: str) -> bool:
    """Try to copy text to the system clipboard. Returns True if successful."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=2)
            return True
        if sys.platform == "win32":
            # Use text mode on Windows to avoid code-page issues
            subprocess.run(["clip"], input=text, text=True, check=True, timeout=2)
            return True
        # Linux: try xclip then xsel
        for cmd in [
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]:
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=2)
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        return False
    except Exception:
        return False


def _print_mcp_command(mode: Literal["docker", "local"]) -> None:
    """Print the claude mcp add-json command and attempt clipboard copy."""
    cmd = _MCP_COMMANDS[mode]
    copied = _copy_to_clipboard(cmd)

    console.print("\n  Run this to add Handshake to Claude:\n")
    console.print(f"  [bold cyan]{cmd}[/bold cyan]")
    if copied:
        console.print("  [dim]↑ copied to clipboard[/dim]")
    console.print()
    console.print("  Restart Claude to activate.")
    console.print()


async def _run_docker_path() -> None:
    """Docker setup: build image, VNC login, print MCP command."""
    # 1. Check Docker
    ok, msg = _check_docker()
    if not ok:
        console.print(f"\n  [red]✗[/red]  {msg}\n")
        raise SystemExit(1)

    console.print("  [green]✓[/green]  Docker is running\n")

    # 2. Build image — stream output; capture only to detect failure
    console.print("  Building Docker image...\n")
    result = subprocess.run(
        ["docker", "compose", "build"],
        timeout=300,
    )
    if result.returncode != 0:
        console.print("\n  [red]✗[/red]  Image build failed. See output above.\n")
        raise SystemExit(1)

    console.print("\n  [green]✓[/green]  Image built\n")

    # 3. Check port 6080 before attempting VNC login
    if not _is_port_free(6080):
        console.print(
            "  [red]✗[/red]  Port 6080 is in use. "
            "Stop the conflicting service and run setup again.\n"
        )
        raise SystemExit(1)

    # 4. VNC login
    console.print("  Open this URL in your browser to log in:\n")
    console.print("  [bold cyan]http://localhost:6080/vnc.html[/bold cyan]\n")
    console.print("  Starting VNC login (Ctrl+C to cancel)...\n")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "-p",
            "6080:6080",
            "handshake-mcp",
            "--vnc-login",
        ],
    )
    if result.returncode != 0:
        console.print("\n  [yellow]![/yellow]  Login cancelled — run setup again when ready.\n")
        raise SystemExit(0)

    console.print("\n  [green]✓[/green]  Logged in\n")

    # 5. Print MCP command
    _print_mcp_command("docker")


async def _run_local_path() -> None:
    """Local setup: browser login, print MCP command."""
    # Deferred import — keeps setup_wizard importable without triggering patchright
    from handshake_mcp_server.browser_manager import (
        DEFAULT_PROFILE_DIR,
        close_browser,
        get_or_create_browser,
        set_headless,
    )
    from handshake_mcp_server.core.auth import wait_for_manual_login
    from handshake_mcp_server.scraping.fields import BASE_URL

    skip_login = False

    if profile_exists():
        console.print("  [green]✓[/green]  Existing profile found\n")
        relogin = questionary.confirm(
            "Already logged in — re-run login anyway?", default=False
        ).ask()
        if not relogin:
            skip_login = True

    if not skip_login:
        console.print("  Opening browser for Handshake login...\n")
        set_headless(False)
        browser = None
        try:
            browser = await get_or_create_browser()
            page = browser.page
            await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=20000)
            console.print("  Waiting for login... [dim](Ctrl+C to cancel)[/dim]\n")
            await wait_for_manual_login(page)
            console.print(
                f"  [green]✓[/green]  Logged in — profile saved to {DEFAULT_PROFILE_DIR}\n"
            )
        except KeyboardInterrupt:
            console.print(
                "\n  [yellow]![/yellow]  Login cancelled — run setup again when ready.\n"
            )
            raise SystemExit(0)
        except Exception as e:
            console.print(f"\n  [red]✗[/red]  Login failed: {e}\n")
            raise SystemExit(1)
        finally:
            if browser is not None:
                await close_browser()

    _print_mcp_command("local")


async def run_setup_wizard() -> None:
    """Placeholder — implemented in Task 7."""
    pass
