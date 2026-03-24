"""VNC-based login server for headless Linux environments.

Manages Xvfb + x11vnc + websockify subprocesses so that Chrome can run
non-headless inside a virtual framebuffer and be viewed remotely via noVNC.
"""

import contextlib
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

_DEFAULT_DISPLAY = ":1"
_XVFB_RESOLUTION = "1280x720x24"
_X11VNC_PORT = 5900
_NOVNC_CANDIDATES = [
    "/usr/share/novnc",  # Debian/Ubuntu: apt install novnc
    "/usr/local/share/novnc",  # Some manual installs
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
        self._started: bool = False

    def __enter__(self) -> "VncLoginServer":
        self._start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self._stop()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/vnc.html"

    def _start(self) -> None:
        # Save original DISPLAY so we can restore it on exit
        self._original_display = os.environ.get("DISPLAY")
        self._started = True
        try:
            xvfb = subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", _XVFB_RESOLUTION],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(xvfb)
            os.environ["DISPLAY"] = self.display
            # Give Xvfb time to open the display socket before x11vnc connects
            time.sleep(0.5)

            x11vnc = subprocess.Popen(
                [
                    "x11vnc",
                    "-display",
                    self.display,
                    "-nopw",
                    "-listen",
                    "localhost",
                    "-forever",
                    "-quiet",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(x11vnc)
            # Give x11vnc time to bind port 5900 before websockify proxies to it
            time.sleep(0.5)

            websockify = subprocess.Popen(
                [
                    "websockify",
                    "--web",
                    self._find_novnc_path(),
                    str(self.port),
                    f"localhost:{_X11VNC_PORT}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(websockify)
            logger.info("VNC login server ready on port %d (display %s)", self.port, self.display)
        except Exception:
            self._stop()
            raise

    def _stop(self) -> None:
        for proc in reversed(self._procs):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as exc:
                logger.debug("Error terminating process: %s", exc)
                with contextlib.suppress(Exception):
                    proc.kill()
        self._procs.clear()

        # Restore original DISPLAY
        if self._started:
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
        raise RuntimeError("noVNC web assets not found. Install with: apt install novnc")
