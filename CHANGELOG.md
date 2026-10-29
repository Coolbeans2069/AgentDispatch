# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Docker support: a multi-stage `Dockerfile` (Python 3.12 slim, non-root user with UID/GID 10001, `HEALTHCHECK` on `/api/stats`, ports 8870 and 8898), a `.dockerignore`, and an example `docker-compose.yml` with named volumes, the Engine Room under the `engineroom` profile and an optional Ollama service under the `ollama` profile. The image is not published to a registry.
- `AGENTDISPATCH_HOST` (and `serve --host`) sets the listen address (default unchanged: 127.0.0.1); `AGENTDISPATCH_ALLOWED_HOSTS` adds Host-header names to the DNS-rebinding guard.
- CI: a `docker` job builds the image without pushing it and smoke-tests the container and the compose setup.

### Changed
- `OLLAMA_URL` (already in `.env.example`) is now honoured by every Ollama caller in the broker, not only the Engine Room; the default is still `http://127.0.0.1:11434`.
- The package data now includes the agent sandbox files (`agent_entry.sh`, `agent.Dockerfile`), so a `pip install` has them too.

## [0.1.1] - 2026-10-28

### Added
- `examples/mcp-b1-demo/`: an offline demo MCP server over a synthetic dataset shaped like SAP Business One Service Layer objects (unofficial; not affiliated with SAP SE; it does not connect to SAP software). It has nine read tools, `audit_tail`, and one approval-gated write (a draft purchase order: preview, a single-use 10-minute token bound to the payload, then an explicit approve), an append-only audit log, a `B1DEMO_READ_ONLY=1` switch and standard-library core tests.
- CI now also runs the Engine Room integration test and the demo's tests (the MCP end-to-end test in the with-extras jobs).

### Changed
- The README, package description and keywords now describe AgentDispatch plainly as a local AI agent broker for small businesses.
- `tests/integration_engineroom.py` starts the Engine Room in this repository instead of looking for a separate checkout, so it really runs.
- The `lite` MCP profile is now the 4 board tools.

### Removed
- The lean tool index (`agentdispatch/toolindex/`: catalog compaction, hints, search and the `tool_search` / `tool_describe` / `tool_call` / `tool_result` meta-tools), its backends, the `approve` and `index` CLI commands, the matching web endpoints, and its tests and golden files. It is kept on the `archive/ghidra-index` branch.

## [0.1.0] - 2026-10-28

First public release.

### Added
- MCP server (stdio and HTTP) with `lite` and `full` tool profiles.
- SQLite notice board with an append-only event log, claims with leases, an attempt ledger and secret redaction.
- Job and step model with a dispatcher, a planner/reviewer loop and a review gate (an independent reviewer plus objective checks).
- Lean tool index: catalog compaction, search and `tool_search` / `tool_describe` / `tool_call` meta-tools.
- Local-first, OpenAI-compatible router (`/v1/chat/completions`, `/v1/models`): local Ollama models first, then your own API keys.
- Night shift: a sandboxed overnight fix loop that only attaches passing patches.
- Watchdog and conductor, a web UI (Command Centre, Board, Agency, Brain view) and the optional Engine Room dashboard.
- `pyproject.toml` with optional extras (`calc`, `engineroom`, `media`, `docs`, `web`, `units`, `all`), `requirements-dev.txt` and a GitHub Actions workflow for Python 3.11 and 3.12.

### Changed
- The README leads with local-first routing and bring-your-own-key use, notes that vendor terms should be checked, and states the optional dependencies accurately.
- Screenshots and the Brain demo GIF re-captured from this release.
- The test suite passes with the standard library only; the symbolic-maths paths run when sympy is installed.

### Removed
- The face detection and recognition tool, its model category and its settings.
- The "rebuild" job kind from the design spec, prompts and code.
