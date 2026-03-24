"""Custom exceptions for Handshake scraping operations."""


class HandshakeScraperException(Exception):
    """Base exception for Handshake scraper."""

    pass


class AuthenticationError(HandshakeScraperException):
    """Raised when authentication fails."""

    pass


class CredentialsNotFoundError(HandshakeScraperException):
    """Raised when no stored credentials/profile are found."""

    pass


class SessionExpiredError(HandshakeScraperException):
    """Raised when the session has expired and re-login is needed."""

    pass


class RateLimitError(HandshakeScraperException):
    """Raised when rate limiting is detected."""

    def __init__(self, message: str, suggested_wait_time: int = 300):
        super().__init__(message)
        self.suggested_wait_time = suggested_wait_time


class ElementNotFoundError(HandshakeScraperException):
    """Raised when an expected element is not found."""

    pass


class ProfileNotFoundError(HandshakeScraperException):
    """Raised when a profile/page returns 404."""

    pass


class NetworkError(HandshakeScraperException):
    """Raised when network-related issues occur."""

    pass


class ScrapingError(HandshakeScraperException):
    """Raised when scraping fails for various reasons."""

    pass
