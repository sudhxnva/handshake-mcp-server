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
RUN uv sync --frozen --no-group dev && \
    uv run patchright install-deps chromium && \
    uv run patchright install chromium && \
    chmod -R 755 /opt/patchright

# Fix ownership of app directory (venv created by uv during RUN above)
RUN chown -R pwuser:pwuser /app

# Switch to non-root user
USER pwuser

# Pre-create the profile directory as pwuser so Docker seeds the named volume
# with correct ownership on first mount.
RUN mkdir -p /home/pwuser/.handshake-mcp

# Persist the browser profile (cookies/session) across container restarts.
# Mount a host volume here: -v handshake-profile:/home/pwuser/.handshake-mcp
VOLUME /home/pwuser/.handshake-mcp

# Default: HTTP server mode with virtual display (Xvfb) for CF bypass.
# Override CMD for login: docker run -it -p 6080:6080 ... --vnc-login
ENTRYPOINT ["uv", "run", "-m", "handshake_mcp_server"]
CMD ["--transport", "streamable-http", "--virtual-display", "--host", "0.0.0.0"]
