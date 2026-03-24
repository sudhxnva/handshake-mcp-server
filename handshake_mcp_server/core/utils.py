"""Utility functions for scraping operations."""

import asyncio
import logging

from patchright.async_api import Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from .exceptions import RateLimitError

logger = logging.getLogger(__name__)


async def detect_rate_limit(page: Page) -> None:
    """Detect if Handshake has rate-limited or blocked the session.

    Checks (in order):
    1. URL contains auth/login barriers (session expired or redirected)
    2. Page contains CAPTCHA iframe (bot detection)
    3. Body text contains rate-limit phrases on error-shaped pages

    The body-text heuristic only runs on pages without a ``<main>`` element
    and with short body text (<2000 chars).

    Raises:
        RateLimitError: If any rate-limiting or security challenge is detected
    """
    current_url = page.url

    # Check for login redirect (session expired)
    if "/login" in current_url or "/sign_in" in current_url:
        raise RateLimitError(
            "Handshake redirected to login. Session may have expired.",
            suggested_wait_time=30,
        )

    # Check for CAPTCHA
    try:
        captcha = await page.locator('iframe[title*="captcha" i], iframe[src*="captcha" i]').count()
        if captcha > 0:
            raise RateLimitError(
                "CAPTCHA challenge detected. Manual intervention required.",
                suggested_wait_time=30,
            )
    except RateLimitError:
        raise
    except PlaywrightTimeoutError:
        pass
    except Exception as e:
        logger.debug("Error checking for CAPTCHA: %s", e)

    # Check for rate limit messages on error-shaped pages
    try:
        has_main = await page.locator("main").count() > 0
        if has_main:
            return

        body_text = await page.locator("body").inner_text(timeout=1000)
        if body_text and len(body_text) < 2000:
            body_lower = body_text.lower()
            if any(
                phrase in body_lower
                for phrase in [
                    "too many requests",
                    "rate limit",
                    "slow down",
                    "try again later",
                    "429",
                ]
            ):
                raise RateLimitError(
                    "Rate limit message detected on page.",
                    suggested_wait_time=60,
                )
    except RateLimitError:
        raise
    except PlaywrightTimeoutError:
        pass


_CF_CHALLENGE_PHRASES = (
    "Performing security verification",
    "Please wait while we verify",
    "Just a moment",
    "Checking your browser",
)


async def wait_for_cf_challenge(page: Page, timeout: float = 60000) -> bool:
    """Wait for a Cloudflare bot challenge to resolve, if one is present.

    Patchright bypasses CF challenges automatically but needs a moment to
    execute the challenge JS. The challenge renders asynchronously after
    domcontentloaded, so we must wait for body content to appear before
    we can know whether a challenge is in progress.

    Handshake also uses a URL-based CF trigger (?cf_challenge=1). When
    present, CF resolves by redirecting to the same URL without the param,
    so we also watch for URL changes as a resolution signal.

    This function is a no-op when no challenge is detected.
    """
    current_url = page.url

    # Step 1: Check for Handshake's URL-based CF trigger (?cf_challenge=1).
    url_has_cf_param = "cf_challenge" in current_url

    if not url_has_cf_param:
        # Step 1b: Wait briefly for any body text to appear so the CF challenge
        # JS has time to render its page content (or confirm the real page loaded).
        try:
            await page.wait_for_function(
                "() => (document.body?.innerText || '').trim().length > 5",
                timeout=3000,
            )
        except PlaywrightTimeoutError:
            return True  # Page still blank — nothing to detect yet

        # Step 2: Check if this is a CF challenge page by body text.
        try:
            body_text = await page.evaluate("() => document.body?.innerText || ''")
        except Exception:
            return True
        if not isinstance(body_text, str) or not any(
            phrase in body_text for phrase in _CF_CHALLENGE_PHRASES
        ):
            return True  # Real page content — no challenge

    # Step 3: Challenge detected. Wait for Patchright to solve it.
    # Resolution is signalled by EITHER:
    # - URL no longer contains "cf_challenge" (URL-based trigger resolved), OR
    # - Body text is no longer a CF challenge page (body-text trigger resolved)
    logger.debug("Cloudflare challenge detected on %s, waiting for resolution...", current_url)
    phrases_js = str(list(_CF_CHALLENGE_PHRASES))
    try:
        await page.wait_for_function(
            f"""() => {{
                const url = window.location.href;
                const urlResolved = !url.includes('cf_challenge');
                const text = document.body?.innerText || '';
                const bodyResolved = !{phrases_js}.some(p => text.includes(p)) && text.trim().length > 50;
                return urlResolved || bodyResolved;
            }}""",
            timeout=timeout,
        )
        logger.debug("Cloudflare challenge resolved, now on %s", page.url)
        return True
    except PlaywrightTimeoutError:
        logger.warning(
            "Cloudflare challenge did not resolve within %.0fs on %s", timeout / 1000, page.url
        )
        return False


async def scroll_to_bottom(page: Page, pause_time: float = 1.0, max_scrolls: int = 10) -> None:
    """Scroll to the bottom of the page to trigger lazy loading.

    Args:
        page: Patchright page object
        pause_time: Time to pause between scrolls (seconds)
        max_scrolls: Maximum number of scroll attempts
    """
    for i in range(max_scrolls):
        previous_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause_time)

        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == previous_height:
            logger.debug("Reached bottom after %d scrolls", i + 1)
            break


async def handle_modal_close(page: Page) -> bool:
    """Close any popup modals that might be blocking content.

    Returns:
        True if a modal was closed, False otherwise
    """
    try:
        close_button = page.locator(
            'button[aria-label="Dismiss"], '
            'button[aria-label="Close"], '
            'button[aria-label="close"], '
            "button.modal-close"
        ).first

        if await close_button.is_visible(timeout=1000):
            await close_button.click()
            await asyncio.sleep(0.5)
            logger.debug("Closed modal")
            return True
    except PlaywrightTimeoutError:
        pass
    except Exception as e:
        logger.debug("Error closing modal: %s", e)

    return False
