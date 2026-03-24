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


def test_docker_subcommand_calls_execvp():
    from handshake_mcp_server.cli_main import _docker_and_exit

    with patch("os.execvp") as mock_exec:
        _docker_and_exit()

    mock_exec.assert_called_once_with(
        "docker",
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            "handshake-profile:/home/pwuser/.handshake-mcp",
            "handshake-mcp-server",
            "--transport",
            "stdio",
            "--virtual-display",
        ],
    )


def test_main_dispatches_docker_subcommand():
    from handshake_mcp_server.cli_main import main

    with (
        patch("sys.argv", ["handshake-mcp-server", "docker"]),
        patch("handshake_mcp_server.cli_main._docker_and_exit") as mock_docker,
    ):
        main()
        mock_docker.assert_called_once()


def test_main_dispatches_setup_subcommand():
    from handshake_mcp_server.cli_main import main

    with (
        patch("sys.argv", ["handshake-mcp-server", "setup"]),
        patch("handshake_mcp_server.cli_main._setup_and_exit") as mock_setup,
    ):
        main()
        mock_setup.assert_called_once()
