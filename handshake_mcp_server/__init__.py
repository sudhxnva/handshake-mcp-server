"""Handshake MCP Server — browser-automation MCP server for app.joinhandshake.com."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("handshake-mcp-server")
except PackageNotFoundError:
    __version__ = "0.0.0"
