# Changelog

All notable changes to this project are listed here. Versions follow [Semantic Versioning](https://semver.org/).

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
