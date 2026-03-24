"""Tests for setup wizard helper functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- _check_docker ---


def test_check_docker_returns_false_when_not_installed():
    from handshake_mcp_server.setup_wizard import _check_docker

    with patch("handshake_mcp_server.setup_wizard.shutil.which", return_value=None):
        ok, msg = _check_docker()

    assert ok is False
    assert "not installed" in msg.lower()


def test_check_docker_returns_false_when_daemon_not_running():
    from handshake_mcp_server.setup_wizard import _check_docker

    with (
        patch(
            "handshake_mcp_server.setup_wizard.shutil.which", return_value="/usr/local/bin/docker"
        ),
        patch("handshake_mcp_server.setup_wizard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1)
        ok, msg = _check_docker()

    assert ok is False
    assert "not running" in msg.lower()


def test_check_docker_returns_true_when_running():
    from handshake_mcp_server.setup_wizard import _check_docker

    with (
        patch(
            "handshake_mcp_server.setup_wizard.shutil.which", return_value="/usr/local/bin/docker"
        ),
        patch("handshake_mcp_server.setup_wizard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        ok, msg = _check_docker()

    assert ok is True
    assert msg == ""


# --- _is_port_free ---


def test_is_port_free_returns_true_for_free_port():
    from handshake_mcp_server.setup_wizard import _is_port_free

    with patch("handshake_mcp_server.setup_wizard.socket.socket") as mock_socket_cls:
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = mock_sock

        assert _is_port_free(9999) is True
        mock_sock.bind.assert_called_once_with(("", 9999))


def test_is_port_free_returns_false_when_port_in_use():
    from handshake_mcp_server.setup_wizard import _is_port_free

    with patch("handshake_mcp_server.setup_wizard.socket.socket") as mock_socket_cls:
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.bind.side_effect = OSError("address in use")
        mock_socket_cls.return_value = mock_sock

        assert _is_port_free(6080) is False


# --- _copy_to_clipboard ---


def test_copy_to_clipboard_uses_pbcopy_on_macos():
    from handshake_mcp_server.setup_wizard import _copy_to_clipboard

    with (
        patch("handshake_mcp_server.setup_wizard.sys.platform", "darwin"),
        patch("handshake_mcp_server.setup_wizard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = _copy_to_clipboard("hello")

    assert result is True
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["pbcopy"]


def test_copy_to_clipboard_returns_false_on_failure():
    from handshake_mcp_server.setup_wizard import _copy_to_clipboard

    with (
        patch("handshake_mcp_server.setup_wizard.sys.platform", "darwin"),
        patch("handshake_mcp_server.setup_wizard.subprocess.run", side_effect=Exception("boom")),
    ):
        result = _copy_to_clipboard("hello")

    assert result is False


# --- _run_docker_path ---


@pytest.mark.asyncio
async def test_docker_path_fails_fast_when_docker_not_installed():
    from handshake_mcp_server.setup_wizard import _run_docker_path

    with (
        patch(
            "handshake_mcp_server.setup_wizard._check_docker", return_value=(False, "not installed")
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        await _run_docker_path()

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_docker_path_fails_fast_when_port_in_use():
    from handshake_mcp_server.setup_wizard import _run_docker_path

    with (
        patch("handshake_mcp_server.setup_wizard._check_docker", return_value=(True, "")),
        patch("handshake_mcp_server.setup_wizard._is_port_free", return_value=False),
        patch("handshake_mcp_server.setup_wizard.subprocess.run") as mock_run,
        pytest.raises(SystemExit) as exc_info,
    ):
        # Make build "succeed" so we reach the port check
        mock_run.return_value = MagicMock(returncode=0)
        await _run_docker_path()

    assert exc_info.value.code == 1


# --- _run_local_path ---


@pytest.mark.asyncio
async def test_local_path_skips_login_when_profile_exists_and_user_declines():
    import sys
    from pathlib import Path

    from handshake_mcp_server.setup_wizard import _run_local_path

    mock_pw = MagicMock()
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=None)
    mock_pw.chromium.executable_path = sys.executable  # always exists

    with (
        patch("patchright.async_api.async_playwright", return_value=mock_pw),
        patch("handshake_mcp_server.setup_wizard.profile_exists", return_value=True),
        patch("questionary.confirm") as mock_confirm,
        patch("handshake_mcp_server.setup_wizard._print_mcp_command") as mock_print,
    ):
        mock_confirm.return_value.ask_async = AsyncMock(
            return_value=False
        )  # user declines re-login
        await _run_local_path()

    mock_print.assert_called_once_with("local")


# --- run_setup_wizard ---


@pytest.mark.asyncio
async def test_wizard_calls_docker_path_when_docker_selected():
    from handshake_mcp_server.setup_wizard import run_setup_wizard

    with (
        patch("questionary.select") as mock_select,
        patch(
            "handshake_mcp_server.setup_wizard._run_docker_path", new_callable=AsyncMock
        ) as mock_docker,
        patch(
            "handshake_mcp_server.setup_wizard._run_local_path", new_callable=AsyncMock
        ) as mock_local,
    ):
        mock_select.return_value.ask_async = AsyncMock(return_value="docker")
        await run_setup_wizard()

    mock_docker.assert_awaited_once()
    mock_local.assert_not_called()


@pytest.mark.asyncio
async def test_wizard_calls_local_path_when_local_selected():
    from handshake_mcp_server.setup_wizard import run_setup_wizard

    with (
        patch("questionary.select") as mock_select,
        patch(
            "handshake_mcp_server.setup_wizard._run_docker_path", new_callable=AsyncMock
        ) as mock_docker,
        patch(
            "handshake_mcp_server.setup_wizard._run_local_path", new_callable=AsyncMock
        ) as mock_local,
    ):
        mock_select.return_value.ask_async = AsyncMock(return_value="local")
        await run_setup_wizard()

    mock_local.assert_awaited_once()
    mock_docker.assert_not_called()


@pytest.mark.asyncio
async def test_wizard_exits_cleanly_on_ctrl_c():
    from handshake_mcp_server.setup_wizard import run_setup_wizard

    with (
        patch("questionary.select") as mock_select,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_select.return_value.ask_async = AsyncMock(
            return_value=None
        )  # questionary returns None on Ctrl+C
        await run_setup_wizard()

    assert exc_info.value.code == 0
