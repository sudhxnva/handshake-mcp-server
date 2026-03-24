"""Singleton browser management for Handshake scraping."""

import logging
from pathlib import Path

from handshake_mcp_server.core.browser import BrowserManager
from handshake_mcp_server.core.auth import is_logged_in
from handshake_mcp_server.core.exceptions import AuthenticationError, SessionExpiredError
from handshake_mcp_server.scraping.fields import BASE_URL

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_DIR = Path.home() / ".handshake-mcp" / "profile"

# Global browser singleton
_browser: BrowserManager | None = None
_headless: bool = True


def set_headless(headless: bool) -> None:
    """Set headless mode for browser startup."""
    global _headless
    _headless = headless


def get_profile_dir() -> Path:
    """Return the browser profile directory."""
    return DEFAULT_PROFILE_DIR


def profile_exists(profile_dir: Path | None = None) -> bool:
    """Return True if the browser profile directory exists and has content."""
    d = profile_dir or DEFAULT_PROFILE_DIR
    return d.exists() and any(d.iterdir())


async def get_or_create_browser() -> BrowserManager:
    """Get the singleton browser, creating it if needed."""
    global _browser
    if _browser is None:
        _browser = BrowserManager(
            user_data_dir=DEFAULT_PROFILE_DIR,
            headless=_headless,
        )
        await _browser.start()
        logger.info("Browser started (headless=%s)", _headless)
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

    if await is_logged_in(browser.page):
        browser.is_authenticated = True
        return

    current_url = browser.page.url
    if "/login" in current_url or "/sign_in" in current_url:
        raise SessionExpiredError(
            "Handshake session expired. Run with --login to re-authenticate."
        )

    raise AuthenticationError(
        "Not authenticated with Handshake. Run with --login to authenticate."
    )


async def close_browser() -> None:
    """Close the singleton browser."""
    global _browser
    if _browser is not None:
        try:
            await _browser.close()
        except Exception as e:
            logger.error("Error closing browser: %s", e)
        finally:
            _browser = None
