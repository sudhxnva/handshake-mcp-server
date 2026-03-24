"""Tests for CLI subcommand dispatch and transport selection."""
from unittest.mock import MagicMock, patch

import pytest


def test_choose_transport_interactive_returns_stdio_by_default():
    from handshake_mcp_server.cli_main import choose_transport_interactive

    with patch("questionary.select") as mock_select:
        mock_question = MagicMock()
        mock_question.ask.return_value = "stdio"
        mock_select.return_value = mock_question

        result = choose_transport_interactive()

    assert result == "stdio"


def test_choose_transport_interactive_raises_on_ctrl_c():
    from handshake_mcp_server.cli_main import choose_transport_interactive

    with patch("questionary.select") as mock_select:
        mock_question = MagicMock()
        mock_question.ask.return_value = None  # questionary returns None on Ctrl+C
        mock_select.return_value = mock_question

        with pytest.raises(KeyboardInterrupt):
            choose_transport_interactive()
