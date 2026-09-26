# Contributing to GoRunRun Local AI

Thanks for helping. GoRunRun Local AI is a private assistant that runs entirely on the user's Mac; every change should keep it that way.

## Ground rules
- **Local first.** No telemetry, no analytics, no calls to hosted AI APIs. Network access happens only at setup (model downloads) and through tools the user turns on.
- **Loopback by default.** Servers bind to 127.0.0.1. Remote access stays opt-in and token-protected.
- **Untrusted data stays data.** File contents, web pages and tool output are wrapped as untrusted and never treated as instructions.
- **Pin versions.** Python dependencies are pinned in `pyproject.toml` and `uv.lock`; frontend dependencies in `package-lock.json`.

## Development setup
Requirements: an Apple Silicon Mac (32 GB+ memory), macOS 26, [Homebrew](https://brew.sh), about 40 GB of free disk.

```sh
git clone https://github.com/gorunrunai/local-ai.git && cd local-ai
make setup     # system deps, Python env, models, web app
make dev       # backend on :8000 plus the Vite dev server on :5173
```

## Tests
```sh
make test              # Python unit tests (no models needed) + frontend unit tests
make test-integration  # against the real local models
make test-e2e          # Playwright browser tests (starts its own backend on :8766)
uv run ruff check .
```
CI runs the unit tests and linters on every pull request. Please run the integration and E2E suites locally when you change model, pipeline or UI behavior.

## Where things live
| Area | Directory | Typical extension |
|---|---|---|
| Models and memory budget | `config/models.yaml`, `inference/` | add an LLM, speech or embedding model |
| Tools | `orchestrator/tools/`, `config/tools.yaml` | add a built-in tool (subclass `Tool`, register it in `orchestrator/policy.py`) |
| MCP servers | `config/mcp.json` | connect any MCP server without code |
| Media handling | `media/` | support a new file type |
| Prompts and styles | `config/prompts/`, `config/styles/` | change behavior without code |
| Web app | `frontend/src/` | UI features |
| Mac app and installer | `desktop/`, `install.sh` | packaging |

## Releasing a version
1. Bump the number in `VERSION`.
2. Add a `## <version> (<YYYY-MM-DD>)` section at the top of `UPDATES.md`, written for users: what's new and what's fixed.
3. Merge to `main`. **Settings → Updates** in everyone's app reads `UPDATES.md` from `main`, and people update by re-running the installer.

A unit test checks that `VERSION` matches the newest entry in `UPDATES.md`.

Model weights are pinned to exact Hugging Face commits under `revisions:` in `config/models.yaml`, so every install gets what was tested. To move to a newer upload, test it, then change its commit there (a unit test checks every downloadable repo is pinned).

## Pull requests
- Keep each PR focused, and describe what changed and how you tested it.
- Add or update tests for behavior changes.
- By contributing, you agree that your contributions are licensed under the Apache License 2.0.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
