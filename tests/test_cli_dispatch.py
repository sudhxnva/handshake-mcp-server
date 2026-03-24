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


def test_auto_trigger_fires_in_tty_when_no_profile():
    """Auto-trigger should launch setup when TTY + no profile + user confirms."""
    from handshake_mcp_server.cli_main import main

    with (
        patch("sys.argv", ["handshake-mcp-server"]),
        patch("sys.stdin") as mock_stdin,
        patch("handshake_mcp_server.cli_main.profile_exists", return_value=False),
        patch("handshake_mcp_server.cli_main._setup_and_exit") as mock_setup,
        patch("questionary.confirm") as mock_confirm,
    ):
        mock_stdin.isatty.return_value = True
        mock_confirm.return_value.ask.return_value = True  # user says yes
        main()

    mock_setup.assert_called_once()


def test_auto_trigger_does_not_fire_in_non_tty():
    """Auto-trigger must NOT fire when not a TTY (e.g., launched by Claude Desktop)."""
    from handshake_mcp_server.cli_main import main

    with (
        patch("sys.argv", ["handshake-mcp-server"]),
        patch("sys.stdin") as mock_stdin,
        patch("handshake_mcp_server.cli_main.profile_exists", return_value=False),
        patch("handshake_mcp_server.cli_main._setup_and_exit") as mock_setup,
        patch(
            "handshake_mcp_server.cli_main.ensure_authentication_ready",
            side_effect=SystemExit(1),
        ),
        pytest.raises(SystemExit),
    ):
        mock_stdin.isatty.return_value = False
        main()

    mock_setup.assert_not_called()


def test_auto_trigger_skipped_when_profile_exists():
    """Auto-trigger must not fire if profile already exists."""
    from handshake_mcp_server.cli_main import main

    with (
        patch("sys.argv", ["handshake-mcp-server", "--transport", "stdio"]),
        patch("sys.stdin") as mock_stdin,
        patch("handshake_mcp_server.cli_main.profile_exists", return_value=True),
        patch("handshake_mcp_server.cli_main._setup_and_exit") as mock_setup,
        patch("handshake_mcp_server.cli_main.ensure_authentication_ready"),
        patch("handshake_mcp_server.cli_main.create_mcp_server") as mock_server,
    ):
        mock_stdin.isatty.return_value = True
        mock_server.return_value.run.side_effect = SystemExit(0)
        with pytest.raises(SystemExit):
            main()

    mock_setup.assert_not_called()


def test_parser_epilog_lists_subcommands():
    from handshake_mcp_server.cli_main import _build_parser

    parser = _build_parser()
    epilog = parser.epilog or ""
    assert "setup" in epilog
    assert "docker" in epilog
    assert "docker-clean" in epilog
