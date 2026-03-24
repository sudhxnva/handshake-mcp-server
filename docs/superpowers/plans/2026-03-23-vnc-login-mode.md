# noVNC Login Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--vnc-login` mode so users can authenticate on a headless Linux server by connecting to a browser session via a web-based VNC viewer (noVNC).

**Architecture:** A new `VncLoginServer` context manager manages Xvfb + x11vnc + websockify as subprocesses. When `--vnc-login` is passed, the CLI starts the VNC server (which sets `DISPLAY`), launches Chrome non-headless (it renders into the Xvfb framebuffer), then polls for Handshake login completion. The user connects to `http://server:6080/vnc.html` in any browser, completes the login flow, and the session profile is persisted to the mounted volume. The existing `--virtual-display` runtime path is unchanged.

**Tech Stack:** Python stdlib `subprocess`, Xvfb, x11vnc, websockify, noVNC (system packages via apt). No new Python dependencies needed beyond what is already in `pyproject.toml`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `handshake_mcp_server/vnc_login.py` | **Create** | `VncLoginServer` context manager: lifecycle of Xvfb + x11vnc + websockify subprocesses |
| `handshake_mcp_server/cli_main.py` | **Modify** | Extract `_build_parser()`, add `--vnc-login`/`--vnc-port` flags, add `_vnc_login_and_exit()`, wire into `main()` |
| `Dockerfile` | **Modify** | Fix Python 3.14→3.13, add xvfb/x11vnc/novnc/websockify apt packages, add VOLUME, update CMD |
| `tests/test_vnc_login.py` | **Create** | Unit tests for `VncLoginServer` lifecycle and CLI integration |

---

## Task 1: `VncLoginServer` — subprocess lifecycle manager

**Files:**
- Create: `handshake_mcp_server/vnc_login.py`
- Test: `tests/test_vnc_login.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vnc_login.py
import os
import pytest
from unittest.mock import MagicMock, patch


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
    # Three distinct proc objects, each terminated once
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


# --- CLI tests ---

def test_vnc_login_flag_is_parsed():
    from handshake_mcp_server.cli_main import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["--vnc-login"])
    assert args.vnc_login is True


def test_vnc_port_flag_is_parsed():
    from handshake_mcp_server.cli_main import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["--vnc-login", "--vnc-port", "7080"])
    assert args.vnc_port == 7080


def test_vnc_port_default_is_6080():
    from handshake_mcp_server.cli_main import _build_parser
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.vnc_port == 6080


def test_vnc_login_rejected_on_non_linux():
    with patch("sys.platform", "darwin"):
        with pytest.raises(SystemExit) as exc_info:
            from handshake_mcp_server.cli_main import _vnc_login_and_exit
            _vnc_login_and_exit(port=6080)
    assert exc_info.value.code == 1


def test_main_calls_vnc_login_and_exit_when_flag_set():
    """main() must route --vnc-login to _vnc_login_and_exit()."""
    with patch("handshake_mcp_server.cli_main._vnc_login_and_exit") as mock_vnc_login, \
         patch("sys.argv", ["prog", "--vnc-login"]):
        from handshake_mcp_server.cli_main import main
        main()
        mock_vnc_login.assert_called_once()
        call_kwargs = mock_vnc_login.call_args
        assert call_kwargs.kwargs.get("port", call_kwargs.args[0] if call_kwargs.args else None) == 6080
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
uv run pytest tests/test_vnc_login.py -v
```

Expected: all fail with `ModuleNotFoundError: No module named 'handshake_mcp_server.vnc_login'` (for vnc tests) and `ImportError` / `AttributeError` for the CLI tests.

- [ ] **Step 3: Implement `VncLoginServer`**

```python
# handshake_mcp_server/vnc_login.py
"""VNC-based login server for headless Linux environments.

Manages Xvfb + x11vnc + websockify subprocesses so that Chrome can run
non-headless inside a virtual framebuffer and be viewed remotely via noVNC.
"""

import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

_DEFAULT_DISPLAY = ":1"
_XVFB_RESOLUTION = "1280x720x24"
_X11VNC_PORT = 5900
_NOVNC_CANDIDATES = [
    "/usr/share/novnc",           # Debian/Ubuntu: apt install novnc
    "/usr/local/share/novnc",     # Some manual installs
]


class VncLoginServer:
    """Context manager: Xvfb + x11vnc + websockify for web-based browser login.

    Sets DISPLAY for the duration of the context and restores the original
    value (or unsets it) on exit.

    Usage::

        with VncLoginServer(port=6080) as vnc:
            print(f"Open {vnc.url} to interact with the browser")
            # launch browser with headless=False here
    """

    def __init__(self, port: int = 6080, display: str = _DEFAULT_DISPLAY):
        self.port = port
        self.display = display
        self._procs: list[subprocess.Popen] = []
        self._original_display: str | None = None

    def __enter__(self) -> "VncLoginServer":
        self._start()
        return self

    def __exit__(self, *args: object) -> None:
        self._stop()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/vnc.html"

    def _start(self) -> None:
        # Save original DISPLAY so we can restore it on exit
        self._original_display = os.environ.get("DISPLAY")

        xvfb = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", _XVFB_RESOLUTION],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(xvfb)
        os.environ["DISPLAY"] = self.display
        time.sleep(0.5)  # Give Xvfb a moment to start

        x11vnc = subprocess.Popen(
            [
                "x11vnc",
                "-display", self.display,
                "-nopw",
                "-listen", "localhost",
                "-forever",
                "-quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(x11vnc)
        time.sleep(0.5)

        websockify = subprocess.Popen(
            [
                "websockify",
                "--web", self._find_novnc_path(),
                str(self.port),
                f"localhost:{_X11VNC_PORT}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._procs.append(websockify)
        logger.info("VNC login server ready on port %d (display %s)", self.port, self.display)

    def _stop(self) -> None:
        for proc in reversed(self._procs):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as exc:
                logger.debug("Error terminating process: %s", exc)
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()

        # Restore original DISPLAY
        if self._original_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._original_display

        logger.info("VNC login server stopped")

    @staticmethod
    def _find_novnc_path() -> str:
        for path in _NOVNC_CANDIDATES:
            if os.path.isdir(path):
                return path
        raise RuntimeError(
            "noVNC web assets not found. Install with: apt install novnc"
        )
```

- [ ] **Step 4: Run the VncLoginServer tests — all should pass**

```bash
uv run pytest tests/test_vnc_login.py -k "not (cli or vnc_login_flag or vnc_port or non_linux or main_calls)" -v
```

Expected: all `VncLoginServer` tests PASS. CLI tests still fail (not implemented yet).

- [ ] **Step 5: Commit**

```bash
git add handshake_mcp_server/vnc_login.py tests/test_vnc_login.py
git commit -m "feat: add VncLoginServer for web-based login on headless Linux"
```

---

## Task 2: CLI `--vnc-login` flag

**Files:**
- Modify: `handshake_mcp_server/cli_main.py`

The tests were already written in Task 1, Step 1. Now implement the CLI changes.

- [ ] **Step 1: Verify the CLI tests still fail**

```bash
uv run pytest tests/test_vnc_login.py::test_vnc_login_flag_is_parsed \
              tests/test_vnc_login.py::test_vnc_port_flag_is_parsed \
              tests/test_vnc_login.py::test_vnc_port_default_is_6080 \
              tests/test_vnc_login.py::test_vnc_login_rejected_on_non_linux \
              tests/test_vnc_login.py::test_main_calls_vnc_login_and_exit_when_flag_set -v
```

Expected: all 5 fail — `_build_parser` and `_vnc_login_and_exit` don't exist yet.

- [ ] **Step 2: Extract `_build_parser()` from `main()`**

In `handshake_mcp_server/cli_main.py`, find the `parser = argparse.ArgumentParser(...)` block inside `main()`. Move it into its own function:

```python
def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Handshake MCP Server — scrape Handshake via browser automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser and wait for manual login, then exit",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear saved authentication profile and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check current session status and exit",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in headed (visible) mode",
    )
    parser.add_argument(
        "--virtual-display",
        action="store_true",
        help="Run browser via Xvfb virtual display (Linux servers). Bypasses Cloudflare headless detection. Requires: apt install xvfb",
    )
    parser.add_argument(
        "--vnc-login",
        action="store_true",
        help="Start a noVNC server for web-based login on headless Linux servers. Requires: apt install xvfb x11vnc novnc",
    )
    parser.add_argument(
        "--vnc-port",
        type=int,
        default=6080,
        help="Port for the noVNC web server during --vnc-login (default: 6080)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="MCP transport mode (default: prompt interactively)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for streamable-http transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for streamable-http transport (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"handshake-mcp-server {__version__}",
    )
    return parser
```

Update `main()` to call it:

```python
def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    # ... rest of main unchanged ...
```

- [ ] **Step 3: Add `_vnc_login_and_exit()`** — insert after `_login_and_exit()`:

```python
def _vnc_login_and_exit(port: int = 6080, log_level: str = "INFO") -> None:
    """Start a noVNC server and open Handshake login for web-based login on Linux."""
    import sys as _sys

    if _sys.platform != "linux":
        print("--vnc-login is only supported on Linux. Use --login --no-headless instead.")
        _sys.exit(1)

    _configure_logging(log_level)
    logger.info("Handshake MCP Server v%s - VNC login mode (port %d)", __version__, port)

    from handshake_mcp_server.vnc_login import VncLoginServer

    async def _do_vnc_login() -> bool:
        try:
            with VncLoginServer(port=port) as vnc:
                print(f"\nOpen this URL in your browser to complete Handshake login:")
                print(f"  {vnc.url}")
                print("\nWaiting for login to complete... (Ctrl+C to cancel)\n")

                # VncLoginServer already set DISPLAY to the Xvfb display.
                # Ensure pyvirtualdisplay does NOT start its own competing Xvfb.
                set_virtual_display(False)
                set_headless(False)

                browser = None
                try:
                    browser = await get_or_create_browser()
                    page = browser.page

                    await page.goto(
                        f"{BASE_URL}/login",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )

                    if await is_logged_in(page):
                        print("Already logged in to Handshake!")
                        return True

                    await wait_for_manual_login(page)
                    print(f"\nLogin successful! Profile saved to: {DEFAULT_PROFILE_DIR}")
                    return True

                except KeyboardInterrupt:
                    print("\nLogin cancelled.")
                    return False
                except Exception as e:
                    logger.error("Login failed: %s", e)
                    print(f"Login failed: {e}")
                    return False
                finally:
                    if browser is not None:
                        await close_browser()
        except RuntimeError as e:
            print(f"Error: {e}")
            return False

    success = asyncio.run(_do_vnc_login())
    sys.exit(0 if success else 1)
```

- [ ] **Step 4: Wire `--vnc-login` into `main()`** — add after the `--login` block (after `if args.login:`):

```python
    if args.vnc_login:
        _vnc_login_and_exit(port=args.vnc_port, log_level=args.log_level)
        return
```

- [ ] **Step 5: Run all CLI tests**

```bash
uv run pytest tests/test_vnc_login.py::test_vnc_login_flag_is_parsed \
              tests/test_vnc_login.py::test_vnc_port_flag_is_parsed \
              tests/test_vnc_login.py::test_vnc_port_default_is_6080 \
              tests/test_vnc_login.py::test_vnc_login_rejected_on_non_linux \
              tests/test_vnc_login.py::test_main_calls_vnc_login_and_exit_when_flag_set -v
```

Expected: all 5 PASS

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass — no regressions.

- [ ] **Step 7: Lint**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: no errors. If format errors, run `uv run ruff format .` and re-check.

- [ ] **Step 8: Commit**

```bash
git add handshake_mcp_server/cli_main.py
git commit -m "feat: add --vnc-login CLI flag for web-based login on Linux"
```

---

## Task 3: Dockerfile — system deps, VOLUME, CMD

**Files:**
- Modify: `Dockerfile`

No unit tests — verify by build + targeted smoke test.

- [ ] **Step 1: Replace the entire Dockerfile**

```dockerfile
FROM python:3.13-slim-bookworm

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create non-root user
RUN useradd -m -s /bin/bash pwuser

# Set working directory and ownership
WORKDIR /app
RUN chown pwuser:pwuser /app

# Copy project files with correct ownership
COPY --chown=pwuser:pwuser . /app

# System dependencies:
#   git           — uv may need it for VCS deps
#   xvfb          — virtual framebuffer for --virtual-display and --vnc-login
#   x11vnc        — VNC server for --vnc-login
#   novnc         — web VNC client assets (/usr/share/novnc)
#   websockify    — WebSocket proxy between noVNC and x11vnc
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    && rm -rf /var/lib/apt/lists/*

# Set browser install location outside the app directory
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/patchright

# Install Python dependencies, Chromium system libs, and patched Chromium binary
RUN uv sync --frozen && \
    uv run patchright install-deps chromium && \
    uv run patchright install chromium && \
    chmod -R 755 /opt/patchright

# Fix ownership of app directory (venv created by uv during RUN above)
RUN chown -R pwuser:pwuser /app

# Switch to non-root user
USER pwuser

# Persist the browser profile (cookies/session) across container restarts.
# Mount a host volume here: -v handshake-profile:/home/pwuser/.handshake-mcp
VOLUME /home/pwuser/.handshake-mcp

# Default: HTTP server mode with virtual display (Xvfb) for CF bypass.
# Override CMD for login: docker run -it -p 6080:6080 ... --vnc-login
ENTRYPOINT ["uv", "run", "-m", "handshake_mcp_server"]
CMD ["--transport", "streamable-http", "--virtual-display", "--host", "0.0.0.0"]
```

- [ ] **Step 2: Build the image**

```bash
docker build -t handshake-mcp-server:dev .
```

Expected: build succeeds. The `patchright install-deps` step installs Chromium system libraries and may take a few minutes.

- [ ] **Step 3: Smoke test — verify all required binaries are present**

```bash
docker run --rm --entrypoint bash handshake-mcp-server:dev -c \
  "which Xvfb && which x11vnc && which websockify && ls /usr/share/novnc/vnc.html && echo ALL_OK"
```

Expected output ends with `ALL_OK`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: add xvfb/x11vnc/novnc to Docker image for VNC login mode"
```

---

## Usage Summary

After this plan is complete, the full Docker workflow on a headless Linux server is:

```bash
# Build
docker build -t handshake-mcp-server .

# Create a named volume for the browser profile
docker volume create handshake-profile

# One-time login: run, open http://server:6080/vnc.html, complete Handshake login
docker run --rm -it \
  -p 6080:6080 \
  -v handshake-profile:/home/pwuser/.handshake-mcp \
  handshake-mcp-server --vnc-login

# Run the server (profile already saved in the volume)
docker run -d \
  -p 8000:8000 \
  -v handshake-profile:/home/pwuser/.handshake-mcp \
  --restart unless-stopped \
  handshake-mcp-server
```

On macOS (dev machine), `--login --no-headless` continues to work unchanged.
