# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Clerk?

Clerk is a thin CLI for LLM agents to interact with email via IMAP/SMTP. It's intentionally dumb—a bridge, not a brain. The LLM (Claude) provides intelligence (summarization, prioritization, drafting), while clerk provides safe, structured access to email servers.

## Build and Development Commands

```bash
# Install in development mode
pip install -e ".[dev]"

# Run all unit tests
pytest

# Run specific test file
pytest tests/test_cache.py -v

# Run specific test class or method
pytest tests/test_cache.py::TestPrefixMatching -v
pytest tests/test_cache.py::TestPrefixMatching::test_find_conversations_by_prefix_single_match -v

# Run tests with coverage
pytest --cov=clerk --cov-report=term-missing

# Integration tests (require Docker mail server)
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/
docker-compose -f docker-compose.test.yml down

# Lint
ruff check src tests

# Type check
mypy src
```

## Architecture

### Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Entry Points: cli.py, shell.py, mcp_server.py              │
│  (User/LLM-facing interfaces - thin wrappers)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  api.py - ClerkAPI                                          │
│  (Unified business logic layer - ALL operations go here)    │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ imap_client.py  │  │    cache.py     │  │  smtp_client.py │
│ (IMAP protocol) │  │ (SQLite + FTS5) │  │  (SMTP sends)   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│  models.py (Pydantic models: Message, Conversation, etc.)   │
│  config.py (YAML config + credential management)            │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **ClerkAPI is the single source of truth** - CLI, shell, and MCP server all call into `api.py`. Never bypass it.

2. **Cache-first architecture** - Messages are cached in SQLite with FTS5 for search. IMAP is only hit when cache is stale or explicitly bypassed with `--fresh`.

3. **Server-first writes** - All modifications (flags, moves, sends) go to the IMAP/SMTP server first. Cache is only updated after server confirms success.

4. **Paranoid sending** - 5 safety layers protect against accidental sends: rate limiting, blocked recipients, two-step confirmation (mandatory for MCP), FROM verification, and audit logging.

### Key Modules

- **api.py** - `ClerkAPI` class with all business logic. Use `get_api()` singleton.
- **cache.py** - `Cache` class with SQLite + FTS5. Handles message storage, search, conversation threading.
- **imap_client.py** - `ImapClient` with context manager support. Use `get_imap_client(account_name)`.
- **smtp_client.py** - Async SMTP sending with safety checks.
- **search.py** - Search query parser supporting operators like `from:`, `to:`, `has:attachment`, `is:unread`.
- **threading.py** - Email threading using References/In-Reply-To headers.
- **drafts.py** - Local draft storage in `~/.local/share/clerk/drafts/`.
- **mcp_server.py** - FastMCP server for LLM integration. Two-step send confirmation is mandatory here.

### Conversation ID Prefix Matching

Conversation IDs are 12-char SHA256 prefixes. The system supports prefix matching:
- Unique prefix → returns the conversation
- Ambiguous prefix → returns list of `ConversationSummary` for disambiguation
- Use `api.resolve_conversation_id()` for graceful handling

### Data Locations

```
~/.config/clerk/config.yaml     # Configuration
~/.local/share/clerk/cache.db   # Message cache (SQLite)
~/.local/share/clerk/drafts/    # Pending drafts
```

## Testing Patterns

- Unit tests mock IMAP/SMTP clients and use temporary SQLite databases
- Use `tmp_path` fixture for isolated test databases
- `sample_message` fixture in test files provides common test data
- Integration tests in `tests/integration/` require a running mail server
