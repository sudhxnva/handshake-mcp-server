# Design: Subcommand Help Visibility + GitHub Actions CI

**Date:** 2026-03-24 **Status:** Approved

---

## Task 1: Subcommands in `--help`

### Problem

`setup`, `docker`, and `docker-clean` are dispatched before `argparse` runs (via `sys.argv[1]` check in `main()`). This is intentional — they are side-channel entry points, not argparse flags. The consequence is they are completely invisible to new users who run `--help`.

### Solution: Epilog text in `_build_parser()`

Add an `epilog=` string to the `ArgumentParser` constructor in `_build_parser()`. The parser already uses `RawDescriptionHelpFormatter`, so newlines and indentation are preserved exactly.

```
Subcommands (positional, before any flags):
  setup         Interactive wizard: Docker or Local path, login, prints MCP config
  docker        Exec into Docker container (MCP stdio entrypoint for IDEs)
  docker-clean  Remove handshake-mcp-server image and handshake-profile volume
```

**No dispatch code changes.** The pre-argparse dispatch mechanism in `main()` stays as-is. This is a documentation-only addition.

### Files changed

- `handshake_mcp_server/cli_main.py` — add `epilog=` to `_build_parser()`

---

## Task 2: GitHub Actions CI

### Problem

No CI exists. The repo has no `.github/workflows/` directory. Without CI, there is no signal to contributors or users that the code is healthy.

### Solution: Single workflow file

**File:** `.github/workflows/ci.yml`**Triggers:** `push` to any branch (every commit gets checked), `pull_request` targeting `master`**Runner:** `ubuntu-latest`**Python:** `3.12` (matches `requires-python = ">=3.12"`)

**Steps:**

1. `actions/checkout@v4`
2. `astral-sh/setup-uv@v5` — installs uv, handles dep cache automatically
3. `uv sync --group dev` — installs all deps including dev group
4. `uv run ruff check .` — lint
5. `uv run ruff format --check .` — format check
6. `uv run ty check` — type check
7. `uv run pytest` — tests

**Why no browser/Playwright setup:** All existing tests use mocks (confirmed in `tests/test_cli_dispatch.py` and other test files). No live browser is needed in CI.

**Why single Python version (no matrix):** `pyproject.toml` sets `requires-python = ">=3.12"` and the codebase targets 3.12 specifically (`target-version = "py312"` in ruff). A matrix adds noise without value here.

### Files changed

- `.github/workflows/ci.yml` — new file