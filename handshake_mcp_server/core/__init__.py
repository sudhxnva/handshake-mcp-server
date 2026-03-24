"""Core browser, auth, and utility exports."""

from .auth import (
    detect_auth_barrier,
    detect_auth_barrier_quick,
    is_logged_in,
    wait_for_manual_login,
)
from .browser import BrowserManager
from .exceptions import (
    AuthenticationError,
    ElementNotFoundError,
    HandshakeScraperException,
    NetworkError,
    ProfileNotFoundError,
    RateLimitError,
    ScrapingError,
)
from .utils import detect_rate_limit, handle_modal_close, scroll_to_bottom, wait_for_cf_challenge

__all__ = [
    "BrowserManager",
    "AuthenticationError",
    "ElementNotFoundError",
    "HandshakeScraperException",
    "NetworkError",
    "ProfileNotFoundError",
    "RateLimitError",
    "ScrapingError",
    "detect_auth_barrier",
    "detect_auth_barrier_quick",
    "is_logged_in",
    "wait_for_manual_login",
    "detect_rate_limit",
    "handle_modal_close",
    "scroll_to_bottom",
    "wait_for_cf_challenge",
]
