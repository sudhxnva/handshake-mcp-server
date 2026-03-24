# Help Visibility + GitHub Actions CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `setup`/`docker`/`docker-clean` subcommands visible in `--help` output, and add a GitHub Actions CI workflow that runs lint, type-check, and tests on every push.

**Architecture:** Task 1 adds an `epilog=` string to the existing `ArgumentParser` in `_build_parser()` — no dispatch logic changes. Task 2 creates `.github/workflows/ci.yml` using `astral-sh/setup-uv` and runs all three quality checks in a single job.

**Tech Stack:** Python 3.12, argparse (stdlib), uv, ruff, ty, pytest, GitHub Actions

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `handshake_mcp_server/cli_main.py` | Modify | Add `epilog=` to `_build_parser()` |
| `tests/test_cli_dispatch.py` | Modify | Add test asserting epilog contains subcommand names |
| `.github/workflows/ci.yml` | Create | CI workflow: lint + type-check + tests on every push |

---

## Task 1: Subcommand Epilog in `--help`

**Files:**
- Modify: `tests/test_cli_dispatch.py`
- Modify: `handshake_mcp_server/cli_main.py`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `tests/test_cli_dispatch.py`:

```python
def test_parser_epilog_lists_subcommands():
    from handshake_mcp_server.cli_main import _build_parser

    parser = _build_parser()
    epilog = parser.epilog or ""
    assert "setup" in epilog
    assert "docker" in epilog
    assert "docker-clean" in epilog
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli_dispatch.py::test_parser_epilog_lists_subcommands -v
```

Expected: FAIL — `AssertionError` because `parser.epilog` is currently `None`.

- [ ] **Step 3: Add epilog to `_build_parser()`**

In `handshake_mcp_server/cli_main.py`, find `_build_parser()` (line 323). Change the `ArgumentParser(...)` call to add `epilog=`:

```python
def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Handshake MCP Server — scrape Handshake via browser automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Subcommands (positional, before any flags):\n"
            "  setup         Interactive wizard: Docker or Local path, login, prints MCP config\n"
            "  docker        Exec into Docker container (MCP stdio entrypoint for IDEs)\n"
            "  docker-clean  Remove handshake-mcp-server image and handshake-profile volume\n"
        ),
    )
```

The rest of `_build_parser()` is unchanged.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_cli_dispatch.py::test_parser_epilog_lists_subcommands -v
```

Expected: PASS

- [ ] **Step 5: Visually verify the help output looks right**

```bash
uv run -m handshake_mcp_server --help
```

Expected: bottom of output shows a "Subcommands" section with `setup`, `docker`, `docker-clean` and their descriptions.

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add handshake_mcp_server/cli_main.py tests/test_cli_dispatch.py
git commit -m "feat: surface setup/docker/docker-clean subcommands in --help epilog"
```

---

## Task 2: GitHub Actions CI Workflow

**Files:**
- Create: `.github/workflows/ci.yml`

No unit tests needed — the workflow is infrastructure config. Its correctness is verified by GitHub running it.

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/ci.yml` with this exact content:

```yaml
name: CI

on:
  push:
  pull_request:
    branches: [master]

jobs:
  ci:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --group dev

      - name: Lint
        run: uv run ruff check .

      - name: Format check
        run: uv run ruff format --check .

      - name: Type check
        run: uv run ty check

      - name: Tests
        run: uv run pytest
```

- [ ] **Step 2: Verify the YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow (lint, type-check, tests)"
```
