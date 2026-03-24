"""Authentication functions for Handshake."""

import asyncio
import logging
from urllib.parse import urlparse

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# URL path fragments that indicate Handshake login/auth barriers
_AUTH_BLOCKER_URL_PATTERNS = (
    "/login",
    "/users/sign_in",
    "/sign_in",
    "/configure_auth",
    "/saml/sign_in",
)

# URL path fragments that indicate the user is in the authenticated student portal
_AUTHENTICATED_URL_PATTERNS = (
    "/stu/",
    "/edu/",
    "/emp/",
    "/dashboard",
    "/users/",
)

# Title patterns that indicate a login page
_LOGIN_TITLE_PATTERNS = (
    "sign in",
    "log in",
    "handshake login",
)


async def is_logged_in(page: Page) -> bool:
    """Check if currently logged in to Handshake.

    Uses a two-tier strategy:
    1. Fail-fast on auth barrier URLs
    2. URL-based check for authenticated-only pages
    """
    try:
        current_url = page.url

        # Step 1: Fail-fast on auth blockers
        if _is_auth_blocker_url(current_url):
            return False

        # Step 2: Check if we're in the authenticated portal
        if any(pattern in current_url for pattern in _AUTHENTICATED_URL_PATTERNS):
            # Require some real page content to avoid false positives
            body_text = await page.evaluate("() => document.body?.innerText || ''")
            if not isinstance(body_text, str):
                return False
            return bool(body_text.strip())

        # Step 3: Check page title as fallback
        try:
            title = (await page.title()).strip().lower()
        except Exception:
            title = ""

        if any(pattern in title for pattern in _LOGIN_TITLE_PATTERNS):
            return False

        return False  # Unknown page state — treat as not logged in

    except PlaywrightTimeoutError:
        logger.warning(
            "Timeout checking login status on %s — treating as not logged in",
            page.url,
        )
        return False
    except Exception:
        logger.error("Unexpected error checking login status", exc_info=True)
        raise


async def detect_auth_barrier(page: Page) -> str | None:
    """Detect Handshake auth barriers on the current page."""
    return await _detect_auth_barrier(page, include_body_text=True)


async def _detect_auth_barrier(
    page: Page,
    *,
    include_body_text: bool,
) -> str | None:
    """Detect Handshake auth/login barriers on the current page."""
    try:
        current_url = page.url
        if _is_auth_blocker_url(current_url):
            return f"auth blocker URL: {current_url}"

        try:
            title = (await page.title()).strip().lower()
        except Exception:
            title = ""
        if any(pattern in title for pattern in _LOGIN_TITLE_PATTERNS):
            return f"login title: {title}"

        if not include_body_text:
            return None

        return None

    except PlaywrightTimeoutError:
        logger.warning(
            "Timeout checking auth barrier on %s — continuing without barrier detection",
            page.url,
        )
        return None
    except Exception:
        logger.error("Unexpected error checking auth barrier", exc_info=True)
        return None


async def detect_auth_barrier_quick(page: Page) -> str | None:
    """Cheap auth-barrier check using URL and title only."""
    return await _detect_auth_barrier(page, include_body_text=False)


def _is_auth_blocker_url(url: str) -> bool:
    """Return True only for real auth routes."""
    path = urlparse(url).path or "/"

    if path in _AUTH_BLOCKER_URL_PATTERNS:
        return True

    return any(
        path == f"{pattern}/" or path.startswith(f"{pattern}/")
        for pattern in _AUTH_BLOCKER_URL_PATTERNS
    )


async def wait_for_manual_login(page: Page, timeout: int = 300000) -> None:
    """Wait for user to manually complete login.

    Args:
        page: Patchright page object
        timeout: Timeout in milliseconds (default: 5 minutes)

    Raises:
        AuthenticationError: If timeout or login not completed
    """
    logger.info(
        "Please complete the login process manually in the browser. "
        "Waiting up to 5 minutes..."
    )

    loop = asyncio.get_running_loop()
    start_time = loop.time()

    while True:
        if await is_logged_in(page):
            logger.info("Manual login completed successfully")
            return

        elapsed = (loop.time() - start_time) * 1000
        if elapsed > timeout:
            raise AuthenticationError(
                "Manual login timeout. Please try again and complete login faster."
            )

        await asyncio.sleep(1)
