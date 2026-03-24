# Publishing to PyPI

## How it works

Releases are published automatically via GitHub Actions (`publish.yml`) when a version tag is pushed. Publishing uses **Trusted Publishing (OIDC)** — no API tokens needed.

## One-time PyPI setup (pending publisher)

Do this once before the first release. The project doesn't need to exist on PyPI yet.

1. Go to `https://pypi.org/manage/account/publishing/`
2. Fill in **Add a new pending publisher**:
   - **PyPI Project Name**: `handshake-mcp-server`
   - **Owner**: `sudhxnva`
   - **Repository name**: `handshake-mcp-server`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`
3. On GitHub, create a **`pypi` environment**: `Settings → Environments → New environment → pypi`
   - Optionally add a required reviewer for manual approval before publish.

## Releasing a new version

```bash
git checkout master && git pull
uv version --bump minor   # or: major, patch
git add pyproject.toml uv.lock
git commit -m "chore: bump version to X.Y.Z"
git push
git tag vX.Y.Z
git push origin vX.Y.Z
```

Pushing the tag triggers `publish.yml`, which:
1. Builds the wheel and sdist with `uv build`
2. Uploads to PyPI via OIDC (no token required)
