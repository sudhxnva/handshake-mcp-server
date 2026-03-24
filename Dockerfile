FROM python:3.14-slim-bookworm

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create non-root user
RUN useradd -m -s /bin/bash pwuser

# Set working directory and ownership
WORKDIR /app
RUN chown pwuser:pwuser /app

# Copy project files with correct ownership
COPY --chown=pwuser:pwuser . /app

# Install git (in case needed for dependencies)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Set browser install location
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/patchright

# Install dependencies, system libs for Chromium, and patched Chromium binary
RUN uv sync --frozen && \
    uv run patchright install-deps chromium && \
    uv run patchright install chromium && \
    chmod -R 755 /opt/patchright

# Fix ownership of app directory (venv created by uv)
RUN chown -R pwuser:pwuser /app

# Switch to non-root user
USER pwuser

# Set entrypoint
ENTRYPOINT ["uv", "run", "-m", "handshake_mcp_server"]
CMD []
