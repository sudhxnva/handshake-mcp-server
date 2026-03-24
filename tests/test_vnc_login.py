import os
from unittest.mock import MagicMock, patch

import pytest

from handshake_mcp_server.vnc_login import VncLoginServer


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
    with VncLoginServer(port=6080):
        assert mock_popen.call_count == 3


def test_first_process_is_xvfb(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(port=6080):
        first_call_args = mock_popen.call_args_list[0][0][0]
        assert first_call_args[0] == "Xvfb"


def test_second_process_is_x11vnc(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(port=6080):
        second_call_args = mock_popen.call_args_list[1][0][0]
        assert second_call_args[0] == "x11vnc"


def test_third_process_is_websockify(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(port=6080):
        third_call_args = mock_popen.call_args_list[2][0][0]
        assert third_call_args[0] == "websockify"


def test_sets_display_env(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(display=":42"):
        assert os.environ.get("DISPLAY") == ":42"


def test_restores_display_env_after_exit(mock_popen, mock_sleep, mock_isdir, monkeypatch):
    """DISPLAY should be restored to its original value after the context exits."""
    monkeypatch.setenv("DISPLAY", ":99")
    with VncLoginServer(display=":42"):
        assert os.environ["DISPLAY"] == ":42"
    assert os.environ["DISPLAY"] == ":99"


def test_restores_missing_display_after_exit(mock_popen, mock_sleep, mock_isdir, monkeypatch):
    """If DISPLAY was not set before, it should be unset again after exit."""
    monkeypatch.delenv("DISPLAY", raising=False)
    with VncLoginServer(display=":42"):
        assert os.environ["DISPLAY"] == ":42"
    assert "DISPLAY" not in os.environ


def test_url_property(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(port=6080) as vnc:
        assert vnc.url == "http://localhost:6080/vnc.html"


def test_url_reflects_custom_port(mock_popen, mock_sleep, mock_isdir):
    with VncLoginServer(port=7090) as vnc:
        assert vnc.url == "http://localhost:7090/vnc.html"


def test_each_process_terminated_on_exit(mock_popen, mock_sleep, mock_isdir):
    """Each individual process must be terminated, not just the shared mock."""
    with VncLoginServer(port=6080):
        pass
    assert len(mock_popen.procs) == 3
    for proc in mock_popen.procs:
        proc.terminate.assert_called_once()


def test_each_process_terminated_on_exception(mock_popen, mock_sleep, mock_isdir):
    with pytest.raises(ValueError):
        with VncLoginServer(port=6080):
            raise ValueError("boom")
    assert len(mock_popen.procs) == 3
    for proc in mock_popen.procs:
        proc.terminate.assert_called_once()


def test_find_novnc_path_raises_when_not_installed():
    with patch("handshake_mcp_server.vnc_login.os.path.isdir", return_value=False):
        with pytest.raises(RuntimeError, match="noVNC"):
            VncLoginServer._find_novnc_path()


def test_find_novnc_path_returns_first_valid_candidate():
    def isdir(path):
        return path == "/usr/share/novnc"

    with patch("handshake_mcp_server.vnc_login.os.path.isdir", side_effect=isdir):
        result = VncLoginServer._find_novnc_path()
        assert result == "/usr/share/novnc"


def test_cleans_up_started_processes_when_startup_fails(mock_sleep, monkeypatch):
    """Processes already started before a startup failure must still be terminated."""
    started_procs = []

    def failing_popen(cmd, *args, **kwargs):
        # First two Popen calls (Xvfb, x11vnc) succeed; third (websockify) fails
        # because _find_novnc_path raises before Popen is even called.
        proc = _make_proc()
        started_procs.append(proc)
        return proc

    monkeypatch.delenv("DISPLAY", raising=False)

    with (
        patch("handshake_mcp_server.vnc_login.subprocess.Popen", side_effect=failing_popen),
        patch("handshake_mcp_server.vnc_login.os.path.isdir", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="noVNC"):
            with VncLoginServer(port=6080):
                pass  # __enter__ itself raises

    # The two processes that DID start must have been terminated
    assert len(started_procs) == 2
    for proc in started_procs:
        proc.terminate.assert_called_once()
