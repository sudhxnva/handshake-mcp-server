import os
from unittest.mock import MagicMock, patch

import pytest


def _make_proc():
    proc = MagicMock()
    proc.poll.return_value = None
    return proc


@pytest.fixture
def mock_popen():
    """Return a fresh mock process for each Popen() call."""
    procs = []

    def _factory(*args, **kwargs):
        p = _make_proc()
        procs.append(p)
        return p

    with patch("handshake_mcp_server.vnc_login.subprocess.Popen", side_effect=_factory) as m:
        m.procs = procs
        yield m


@pytest.fixture
def mock_sleep():
    with patch("handshake_mcp_server.vnc_login.time.sleep"):
        yield


@pytest.fixture
def mock_isdir():
    with patch("handshake_mcp_server.vnc_login.os.path.isdir", return_value=True):
        yield


def test_starts_three_processes(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080):
        assert mock_popen.call_count == 3


def test_first_process_is_xvfb(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080):
        first_call_args = mock_popen.call_args_list[0][0][0]
        assert first_call_args[0] == "Xvfb"


def test_second_process_is_x11vnc(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080):
        second_call_args = mock_popen.call_args_list[1][0][0]
        assert second_call_args[0] == "x11vnc"


def test_third_process_is_websockify(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080):
        third_call_args = mock_popen.call_args_list[2][0][0]
        assert third_call_args[0] == "websockify"


def test_sets_display_env(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(display=":42"):
        assert os.environ.get("DISPLAY") == ":42"


def test_restores_display_env_after_exit(mock_popen, mock_sleep, mock_isdir, monkeypatch):
    """DISPLAY should be restored to its original value after the context exits."""
    from handshake_mcp_server.vnc_login import VncLoginServer
    monkeypatch.setenv("DISPLAY", ":99")
    with VncLoginServer(display=":42"):
        assert os.environ["DISPLAY"] == ":42"
    assert os.environ["DISPLAY"] == ":99"


def test_restores_missing_display_after_exit(mock_popen, mock_sleep, mock_isdir, monkeypatch):
    """If DISPLAY was not set before, it should be unset again after exit."""
    from handshake_mcp_server.vnc_login import VncLoginServer
    monkeypatch.delenv("DISPLAY", raising=False)
    with VncLoginServer(display=":42"):
        assert os.environ["DISPLAY"] == ":42"
    assert "DISPLAY" not in os.environ


def test_url_property(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080) as vnc:
        assert vnc.url == "http://localhost:6080/vnc.html"


def test_url_reflects_custom_port(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=7090) as vnc:
        assert vnc.url == "http://localhost:7090/vnc.html"


def test_each_process_terminated_on_exit(mock_popen, mock_sleep, mock_isdir):
    """Each individual process must be terminated, not just the shared mock."""
    from handshake_mcp_server.vnc_login import VncLoginServer
    with VncLoginServer(port=6080):
        pass
    assert len(mock_popen.procs) == 3
    for proc in mock_popen.procs:
        proc.terminate.assert_called_once()


def test_each_process_terminated_on_exception(mock_popen, mock_sleep, mock_isdir):
    from handshake_mcp_server.vnc_login import VncLoginServer
    with pytest.raises(ValueError):
        with VncLoginServer(port=6080):
            raise ValueError("boom")
    assert len(mock_popen.procs) == 3
    for proc in mock_popen.procs:
        proc.terminate.assert_called_once()


def test_find_novnc_path_raises_when_not_installed():
    from handshake_mcp_server.vnc_login import VncLoginServer
    with patch("handshake_mcp_server.vnc_login.os.path.isdir", return_value=False):
        with pytest.raises(RuntimeError, match="noVNC"):
            VncLoginServer._find_novnc_path()


def test_find_novnc_path_returns_first_valid_candidate():
    from handshake_mcp_server.vnc_login import VncLoginServer

    def isdir(path):
        return path == "/usr/share/novnc"

    with patch("handshake_mcp_server.vnc_login.os.path.isdir", side_effect=isdir):
        result = VncLoginServer._find_novnc_path()
        assert result == "/usr/share/novnc"
