"""Interactive setup wizard for first-time Handshake MCP Server configuration."""

import shutil
import socket
import subprocess
import sys
from typing import Literal

from rich.console import Console

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


async def run_setup_wizard() -> None:
    """Placeholder — implemented in Task 7."""
    pass
