# Repository Guidelines

## Project Structure & Module Organization

Clerk provides structured IMAP/SMTP email access through MCP; its CLI handles setup, authentication, and debugging.

- `src/clerk/`: application code. `mcp_server.py` and `cli.py` expose interfaces; `api.py` coordinates mutations, sync, and status. `cache.py` manages SQLite/FTS5; `models.py` defines Pydantic models. Protocol clients and authentication have separate modules.
- `tests/test_*.py`: unit tests; `tests/integration/`: GreenMail-backed email tests.
- `demo/`: local demonstration configuration and scripts. `docs/`: design specifications and implementation plans. `README.md` documents supported behavior.

## Build, Test, and Development Commands

- `pip install -e ".[dev]"`: install the package and development tools for Python 3.11+.
- `clerk mcp-server`: start the primary MCP interface locally.
- `make test` or `make test-unit`: run unit tests only.
- `make test-integration`: start GreenMail, run integration tests, and tear it down; requires Docker and `docker-compose`.
- `make coverage`: run unit tests with terminal and HTML coverage reports (`htmlcov/`).
- `make lint`, `make format`, `make typecheck`: run Ruff checks, Ruff formatting, and strict mypy checks, respectively.
- Install the build frontend with `pip install build`, then run `make build` to clean artifacts and build distributions.

## Coding Style & Naming Conventions

Use four-space indentation, Ruff's 100-character formatting width, `snake_case` functions/modules, and `PascalCase` classes. Add type annotations compatible with strict mypy. Prefer Pydantic models, enums, and standard parsers over stringly typed interfaces, regex parsing of structured data, or duplicated mappings.

## Testing Guidelines

Use pytest and pytest-asyncio; async tests run in automatic mode. Name files `test_<module>.py` and functions `test_<behavior>`. Use `tmp_path` for isolated SQLite databases and mock IMAP/SMTP in unit tests. Add regression tests for fixes and tests for new features. Run both suites and inspect uncovered branches; preserve the configured 74% coverage floor. Target individual cases with `pytest tests/test_cache.py -k prefix`.

## Commit & Pull Request Guidelines

Follow the history's Conventional Commit style: `fix(config): ...`, `refactor(imap): ...`, or `test: ...`. Keep commits focused. PR descriptions should explain behavior changes, link relevant issues, report validation results, and update README/CHANGELOG when appropriate.

## Architecture & Configuration Safety

Keep reads cache-based and mutations server-first; update cached state after server confirmation. Preserve mandatory MCP send confirmation and audit logging. Keep credentials, OAuth tokens, and mailbox data out of commits; configuration lives in `~/.config/clerk/`, and runtime data in `~/.local/share/clerk/`.
