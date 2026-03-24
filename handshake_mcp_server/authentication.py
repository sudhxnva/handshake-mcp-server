"""Authentication logic for Handshake MCP Server."""

import logging
import shutil
from pathlib import Path

from handshake_mcp_server.browser_manager import DEFAULT_PROFILE_DIR, profile_exists
from handshake_mcp_server.core.exceptions import CredentialsNotFoundError

logger = logging.getLogger(__name__)


def get_authentication_source() -> bool:
    """Check if authentication is available via persistent browser profile.

    Returns:
        True if a valid profile exists

    Raises:
        CredentialsNotFoundError: If no authentication profile is found
    """
    profile_dir = DEFAULT_PROFILE_DIR

    if profile_exists(profile_dir):
        logger.info("Using browser profile from %s", profile_dir)
        return True

    raise CredentialsNotFoundError(
        "No Handshake browser profile found.\n\n"
        "Options:\n"
        "  1. Run with --login to create a browser profile (recommended)\n"
        "  2. Run with --no-headless to login interactively\n\n"
        "For Docker users:\n"
        "  Create profile on host first: uv run -m handshake_mcp_server --login\n"
        "  Then mount into Docker: -v ~/.handshake-mcp:/home/pwuser/.handshake-mcp"
    )


def clear_profile(profile_dir: Path | None = None) -> bool:
    """Clear stored browser profile directory.

    Returns:
        True if clearing was successful
    """
    d = profile_dir or DEFAULT_PROFILE_DIR

    if d.exists():
        try:
            shutil.rmtree(d)
            logger.info("Profile cleared from %s", d)
            return True
        except OSError as e:
            logger.warning("Could not clear profile: %s", e)
            return False
    return True
