"""Dependency injection factories for MCP tools."""

from handshake_mcp_server.browser_manager import ensure_authenticated, get_or_create_browser
from handshake_mcp_server.error_handler import raise_tool_error
from handshake_mcp_server.scraping import HandshakeExtractor


async def get_extractor() -> HandshakeExtractor:
    """Acquire the singleton browser, authenticate, and return a ready extractor.

    Known exceptions are converted to structured ToolError responses
    via raise_tool_error(); unexpected exceptions propagate as-is.
    """
    try:
        browser = await get_or_create_browser()
        await ensure_authenticated()
        return HandshakeExtractor(browser.page)
    except Exception as e:
        raise_tool_error(e, "get_extractor")  # NoReturn
