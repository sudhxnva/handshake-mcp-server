"""Singleton browser management for Handshake scraping."""

import logging
from pathlib import Path
from typing import Any

from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from handshake_mcp_server.core.auth import is_logged_in
from handshake_mcp_server.core.browser import BrowserManager
from handshake_mcp_server.core.exceptions import AuthenticationError, SessionExpiredError
from handshake_mcp_server.scraping.fields import BASE_URL

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_DIR = Path.home() / ".handshake-mcp" / "profile"

# Global browser singleton
_browser: BrowserManager | None = None
_headless: bool = True
_use_virtual_display: bool = False
_virtual_display: Any | None = None  # pyvirtualdisplay.Display instance


def set_headless(headless: bool) -> None:
    """Set headless mode for browser startup."""
    global _headless
    _headless = headless


def set_virtual_display(enabled: bool) -> None:
    """Enable Xvfb virtual display for headless Linux environments.

    When enabled, launches a virtual framebuffer so Chrome runs in non-headless
    mode without requiring a physical display. This bypasses Cloudflare's
    headless browser detection. Requires Xvfb: apt install xvfb
    """
    global _use_virtual_display
    _use_virtual_display = enabled


def _start_virtual_display() -> Any | None:
    """Start an Xvfb virtual display and return the Display instance.

    Returns None on macOS/Windows (Xvfb is Linux-only) — caller should
    fall back to headless=False without a virtual display.
    """
    import sys

    if sys.platform != "linux":
        logger.warning(
            "Virtual display (Xvfb) is only supported on Linux. "
            "Falling back to headless=False (browser window will open)."
        )
        return None

    try:
        from pyvirtualdisplay import Display
    except ImportError as e:
        raise RuntimeError("pyvirtualdisplay is not installed. Run: uv sync") from e

    try:
        display = Display(visible=False, size=(1280, 720))
        display.start()
        logger.info("Virtual display started (Xvfb)")
        return display
    except Exception as e:
        raise RuntimeError(
            f"Failed to start virtual display: {e}. Make sure Xvfb is installed: apt install xvfb"
        ) from e


def get_profile_dir() -> Path:
    """Return the browser profile directory."""
    return DEFAULT_PROFILE_DIR


def profile_exists(profile_dir: Path | None = None) -> bool:
    """Return True if the browser profile directory exists and has content."""
    d = profile_dir or DEFAULT_PROFILE_DIR
    return d.exists() and any(d.iterdir())


async def get_or_create_browser() -> BrowserManager:
    """Get the singleton browser, creating it if needed."""
    global _browser, _virtual_display
    if _browser is None:
        headless = _headless
        if _use_virtual_display:
            _virtual_display = _start_virtual_display()
            headless = False  # Virtual display provides a real screen; no headless mode needed
        _browser = BrowserManager(
            user_data_dir=DEFAULT_PROFILE_DIR,
            headless=headless,
        )
        await _browser.start()
        logger.info(
            "Browser started (headless=%s, virtual_display=%s)", headless, _use_virtual_display
        )
    return _browser


async def ensure_authenticated() -> None:
    """Verify the browser session is authenticated.

    Navigates to the Handshake home page and checks for auth state.

    Raises:
        SessionExpiredError: If the session has expired.
        AuthenticationError: If not authenticated.
    """
    browser = await get_or_create_browser()

    if browser.is_authenticated:
        return

    # Navigate to the student portal to check auth state
    try:
        await browser.page.goto(
            f"{BASE_URL}/stu",
            wait_until="domcontentloaded",
            timeout=20000,
        )
    except Exception as e:
        logger.warning("Failed to navigate to Handshake portal: %s", e)

    # Wait for body content to appear — Cloudflare bot challenges (headless detection)
    # fire after domcontentloaded and take a moment to resolve via Patchright.
    # Without this wait, is_logged_in() sees an empty body and wrongly returns False.
    try:
        await browser.page.wait_for_function(
            "() => (document.body?.innerText || '').trim().length > 0",
            timeout=10000,
        )
    except PlaywrightTimeoutError:
        logger.debug("Body content did not appear within timeout on %s", browser.page.url)

    if await is_logged_in(browser.page):
        browser.is_authenticated = True
        return

    current_url = browser.page.url
    if "/login" in current_url or "/sign_in" in current_url:
        raise SessionExpiredError("Handshake session expired. Run with --login to re-authenticate.")

    raise AuthenticationError("Not authenticated with Handshake. Run with --login to authenticate.")


async def close_browser() -> None:
    """Close the singleton browser and stop any virtual display."""
    global _browser, _virtual_display
    if _browser is not None:
        try:
            await _browser.close()
        except Exception as e:
            logger.error("Error closing browser: %s", e)
        finally:
            _browser = None

    if _virtual_display is not None:
        try:
            _virtual_display.stop()
            logger.info("Virtual display stopped")
        except Exception as e:
            logger.error("Error stopping virtual display: %s", e)
        finally:
            _virtual_display = None
