"""
Handshake MCP Server - Main CLI application entry point.

Two-phase startup:
1. Authentication Check - Verify browser profile is available
2. Server Runtime - MCP server startup with transport selection
"""

import argparse
import asyncio
import contextlib
import logging
import sys
from typing import Literal

import questionary
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from handshake_mcp_server import __version__
from handshake_mcp_server.authentication import clear_profile, get_authentication_source
from handshake_mcp_server.browser_manager import (
    DEFAULT_PROFILE_DIR,
    close_browser,
    get_or_create_browser,
    profile_exists,
    set_headless,
    set_virtual_display,
)
from handshake_mcp_server.core.auth import is_logged_in, wait_for_manual_login
from handshake_mcp_server.core.exceptions import CredentialsNotFoundError
from handshake_mcp_server.core.utils import wait_for_cf_challenge
from handshake_mcp_server.scraping.fields import BASE_URL

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("patchright").setLevel(logging.WARNING)


def choose_transport_interactive() -> Literal["stdio", "streamable-http"]:
    """Prompt user for transport mode using questionary."""
    answer = questionary.select(
        "Choose MCP transport mode",
        choices=[
            questionary.Choice("stdio (Default CLI mode)", value="stdio"),
            questionary.Choice("streamable-http (HTTP server mode)", value="streamable-http"),
        ],
        default="stdio",
    ).ask()

    if answer is None:
        raise KeyboardInterrupt("Transport selection cancelled by user")

    return answer


def _login_and_exit(headless: bool, log_level: str = "INFO") -> None:
    """Open browser, navigate to Handshake login, wait for manual login, then exit."""
    _configure_logging(log_level)
    logger.info("Handshake MCP Server v%s - Login mode", __version__)

    set_headless(headless)

    async def _do_login() -> bool:
        browser = None
        try:
            browser = await get_or_create_browser()
            page = browser.page

            # Check if already logged in
            await page.goto(f"{BASE_URL}/stu", wait_until="domcontentloaded", timeout=20000)
            if await is_logged_in(page):
                print("Already logged in to Handshake!")
                return True

            # Navigate to login page
            print(f"Opening Handshake login page: {BASE_URL}/login")
            await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=20000)

            print("Please complete the login process in the browser window.")
            print("The server will detect when you're logged in automatically.")

            await wait_for_manual_login(page)

            print(f"Login successful! Profile saved to: {DEFAULT_PROFILE_DIR}")
            return True

        except KeyboardInterrupt:
            print("\nLogin cancelled.")
            return False
        except Exception as e:
            logger.error("Login failed: %s", e)
            print(f"Login failed: {e}")
            return False
        finally:
            if browser is not None:
                await close_browser()

    success = asyncio.run(_do_login())
    sys.exit(0 if success else 1)


def _vnc_login_and_exit(port: int = 6080, log_level: str = "INFO") -> None:
    """Start a noVNC server and open Handshake login for web-based login on Linux."""
    if sys.platform != "linux":
        print("--vnc-login is only supported on Linux. Use --login --no-headless instead.")
        sys.exit(1)

    _configure_logging(log_level)
    logger.info("Handshake MCP Server v%s - VNC login mode (port %d)", __version__, port)

    from handshake_mcp_server.vnc_login import VncLoginServer

    async def _do_vnc_login() -> bool:
        try:
            with VncLoginServer(port=port) as vnc:
                print("\nOpen this URL in your browser to complete Handshake login:")
                print(f"  {vnc.url}")
                print("\nWaiting for login to complete... (Ctrl+C to cancel)\n")

                # VncLoginServer already set DISPLAY to the Xvfb display.
                # Ensure pyvirtualdisplay does NOT start its own competing Xvfb.
                set_virtual_display(False)
                set_headless(False)

                browser = None
                try:
                    browser = await get_or_create_browser()
                    page = browser.page

                    await page.goto(
                        f"{BASE_URL}/login",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )

                    if await is_logged_in(page):
                        print("Already logged in to Handshake!")
                        return True

                    await wait_for_manual_login(page)
                    print(f"\nLogin successful! Profile saved to: {DEFAULT_PROFILE_DIR}")
                    return True

                except KeyboardInterrupt:
                    print("\nLogin cancelled.")
                    return False
                except Exception as e:
                    logger.error("Login failed: %s", e)
                    print(f"Login failed: {e}")
                    return False
                finally:
                    if browser is not None:
                        await close_browser()
        except RuntimeError as e:
            print(f"Error: {e}")
            return False

    success = asyncio.run(_do_vnc_login())
    sys.exit(0 if success else 1)


def _logout_and_exit() -> None:
    """Clear browser profile and exit."""
    _configure_logging("INFO")
    logger.info("Handshake MCP Server v%s - Logout mode", __version__)

    if not profile_exists():
        print("No authentication profile found. Nothing to clear.")
        sys.exit(0)

    print(f"Clear Handshake authentication profile from {DEFAULT_PROFILE_DIR.parent}?")

    try:
        confirmation = input("Are you sure you want to clear the profile? (y/N): ").strip().lower()
        if confirmation not in ("y", "yes"):
            print("Operation cancelled.")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(0)

    if clear_profile():
        print("Handshake authentication profile cleared successfully!")
    else:
        print("Failed to clear authentication profile.")
        sys.exit(1)

    sys.exit(0)


def _status_and_exit(headless: bool) -> None:
    """Check session validity and display info, then exit."""
    _configure_logging("INFO")
    logger.info("Handshake MCP Server v%s - Status mode", __version__)

    if not profile_exists():
        print(f"No profile found at {DEFAULT_PROFILE_DIR}")
        print("Run with --login to create a profile.")
        sys.exit(1)

    print(f"Profile directory: {DEFAULT_PROFILE_DIR}")
    print("Profile exists: Yes")

    set_headless(headless)

    async def _check_session() -> bool:
        browser = None
        try:
            browser = await get_or_create_browser()
            await browser.page.goto(
                f"{BASE_URL}/stu",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            # Wait for CF challenge and page content to render before checking auth.
            await wait_for_cf_challenge(browser.page)
            with contextlib.suppress(PlaywrightTimeoutError):
                await browser.page.wait_for_function(
                    "() => (document.body?.innerText || '').trim().length > 0",
                    timeout=10000,
                )
            logged_in = await is_logged_in(browser.page)
            return logged_in
        except Exception as e:
            logger.warning("Session check failed: %s", e)
            return False
        finally:
            if browser is not None:
                await close_browser()

    print("Checking session validity...")
    is_valid = asyncio.run(_check_session())

    if is_valid:
        print("Session status: Active (logged in)")
    else:
        print("Session status: Expired or invalid")
        print("Run with --login to re-authenticate.")
        sys.exit(1)

    sys.exit(0)


def ensure_authentication_ready() -> None:
    """Verify authentication profile exists before starting the server."""
    try:
        get_authentication_source()
    except CredentialsNotFoundError as e:
        print(f"Authentication error: {e}")
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Handshake MCP Server — scrape Handshake via browser automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser and wait for manual login, then exit",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear saved authentication profile and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check current session status and exit",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in headed (visible) mode",
    )
    parser.add_argument(
        "--virtual-display",
        action="store_true",
        help="Run browser via Xvfb virtual display (Linux servers). Bypasses Cloudflare headless detection. Requires: apt install xvfb",
    )
    parser.add_argument(
        "--vnc-login",
        action="store_true",
        help=(
            "Start a noVNC server for web-based login on headless Linux servers."
            " Requires: apt install xvfb x11vnc novnc"
        ),
    )
    parser.add_argument(
        "--vnc-port",
        type=int,
        default=6080,
        help="Port for the noVNC web server during --vnc-login (default: 6080)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="MCP transport mode (default: prompt interactively)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for streamable-http transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for streamable-http transport (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"handshake-mcp-server {__version__}",
    )

    return parser


def main() -> None:
    """Main entry point for the Handshake MCP Server CLI."""
    parser = _build_parser()

    args = parser.parse_args()

    headless = not args.no_headless

    if args.virtual_display:
        set_virtual_display(True)

    if args.login:
        _login_and_exit(headless=headless, log_level=args.log_level)
        return

    if args.vnc_login:
        _vnc_login_and_exit(port=args.vnc_port, log_level=args.log_level)
        return

    if args.logout:
        _logout_and_exit()
        return

    if args.status:
        _status_and_exit(headless=headless)
        return

    # Normal server startup
    _configure_logging(args.log_level)
    logger.info("Handshake MCP Server v%s starting...", __version__)

    # Phase 1: Verify authentication
    ensure_authentication_ready()

    # Set headless mode before browser starts
    set_headless(headless)

    # Phase 2: Start server
    transport = args.transport
    if transport is None:
        # Interactive transport selection (only in TTY contexts)
        if sys.stdin.isatty():
            try:
                transport = choose_transport_interactive()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)
        else:
            transport = "stdio"

    from handshake_mcp_server.server import create_mcp_server

    mcp = create_mcp_server()

    logger.info("Starting MCP server with transport: %s", transport)

    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )
    else:
        mcp.run(transport="stdio")
