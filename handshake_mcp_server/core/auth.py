"""Authentication functions for Handshake."""

import asyncio
import logging
from urllib.parse import urlparse

from patchright.async_api import Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

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

# Title patterns that indicate a login page
_LOGIN_TITLE_PATTERNS = (
    "sign in",
    "log in",
    "handshake login",
)


async def is_logged_in(page: Page) -> bool:
    """Check if currently logged in to Handshake.

    Strategy:
    1. Fail-fast on known auth barrier URLs (login page, sign-in routes)
    2. Fail-fast if the page title indicates a login page
    3. If neither barrier is present AND the page has body content → logged in

    This is intentionally permissive: we check for the *absence* of auth
    barriers rather than a specific allowlist of post-login URLs, because
    Handshake's post-login redirect can vary (e.g. /home, /stu, /dashboard).
    """
    try:
        current_url = page.url

        # Step 1: Must be on the Handshake app domain.
        # This prevents false positives from SSO redirect pages (e.g. fedauth.colorado.edu),
        # Cloudflare challenge pages, or any other intermediate domains.
        parsed_host = urlparse(current_url).netloc
        if parsed_host and "app.joinhandshake.com" not in parsed_host:
            logger.debug("is_logged_in: not on Handshake domain: %s", current_url)
            return False

        # Step 2: Fail-fast on auth blocker URLs
        if _is_auth_blocker_url(current_url):
            logger.debug("is_logged_in: auth blocker URL: %s", current_url)
            return False

        # Step 3: Fail-fast on login page titles
        try:
            title = (await page.title()).strip().lower()
        except Exception:
            title = ""

        if any(pattern in title for pattern in _LOGIN_TITLE_PATTERNS):
            logger.debug("is_logged_in: login title detected: %r", title)
            return False

        # Step 4: Check for ajs_user_id cookie — Handshake sets this on every
        # authenticated page via analytics.js; it's readable via JS (not HttpOnly).
        try:
            cookie_str = await page.evaluate("() => document.cookie")
            if isinstance(cookie_str, str) and "ajs_user_id=" in cookie_str:
                logger.debug("is_logged_in: ajs_user_id cookie present at %s", current_url)
                return True
        except Exception:
            pass

        # Step 5: Require real page content (guards against blank/loading pages)
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        if not isinstance(body_text, str) or not body_text.strip():
            logger.debug("is_logged_in: no body content on %s", current_url)
            return False

        logger.debug("is_logged_in: logged in at %s", current_url)
        return True

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

    Polls the page URL every 500 ms. Login is considered complete when the
    browser is on app.joinhandshake.com at any path that is not a known auth
    barrier (login page, SAML callback, etc.).

    URL-only detection avoids:
    - React SPA hydration races (body text empty at domcontentloaded)
    - Patchright wait_for_url blocking on load-state after URL match
    - False positives from the university SSO domain

    Args:
        page: Patchright page object
        timeout: Timeout in milliseconds (default: 5 minutes)

    Raises:
        AuthenticationError: If timeout expires before login completes
    """
    logger.info(
        "Please complete the login process manually in the browser. Waiting up to 5 minutes..."
    )

    loop = asyncio.get_running_loop()
    start_time = loop.time()

    while True:
        current_url = page.url
        logger.debug("wait_for_manual_login: current URL = %s", current_url)

        if (
            "joinhandshake.com" in current_url
            and not _is_auth_blocker_url(current_url)
            and "/login" not in current_url
            and "saml" not in current_url.lower()
            and "sign_in" not in current_url
        ):
            logger.info("Manual login completed at: %s", current_url)
            return

        elapsed = (loop.time() - start_time) * 1000
        if elapsed > timeout:
            raise AuthenticationError(
                "Manual login timeout. Please try again and complete login faster."
            )

        await asyncio.sleep(0.5)
