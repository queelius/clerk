# Design: Clerk v0.6.0 — Cleanup, MCP Parity, Docs

## Overview

Four workstreams: fix mypy errors, fix integration tests, add missing MCP tools, update README.

## 1. Mypy Fixes (51 errors)

Fix in layers:

- **Annotations**: Add missing return types in `drafts.py`, `shell.py`
- **Generics**: Add type parameters to `list`, `tuple`, `dict` in `cache.py`, `imap_client.py`, `api.py`
- **Variance**: Change `list[MessageFlag]` params to `Sequence[MessageFlag]` in `cache.py`, `api.py`, `cli.py`
- **None guards**: Add early returns after `exit_with_code()` in `cli.py`
- **Union narrowing**: Add `isinstance` checks before `.decode()` in `imap_client.py`
- **Untyped imports**: `type: ignore[import-untyped]` for `imapclient`, `google_auth_oauthlib`
- **Status dict typing**: Type `status_info` properly in `cli.py`

## 2. Integration Test Fix

Problem: `FromAddress(address="test@localhost")` fails Pydantic EmailStr validation.

Fix: Change `GREENMAIL_EMAIL` to use a valid domain that Greenmail still accepts, or add a test-mode validator override. Greenmail accepts any domain, so `test@localhost.test` or `test@example.com` should work.

## 3. MCP Server Parity

Design principle: **SQL for reads, specialized tools for mutations.**

### Read interface

Add `clerk_sql` tool:
- Accepts a SQL SELECT query string
- Opens SQLite connection in **readonly mode** (`?mode=ro` or `PRAGMA query_only = ON`)
- Returns results as JSON
- Rejects anything that isn't a SELECT (belt-and-suspenders with readonly mode)
- Exposes the messages table schema in the tool description

### Mutation tools to add

| Tool | Action | Safety |
|------|--------|--------|
| `clerk_move` | Move message to folder | Server-first, then cache update |
| `clerk_flag` | Flag/unflag a message | Server-first |
| `clerk_mark_unread` | Mark as unread | Server-first |

These follow the existing pattern: IMAP server write first, cache update after confirmation.

### Existing MCP tools (no changes needed)

- `clerk_inbox`, `clerk_show`, `clerk_search` — keep as convenience wrappers (common operations shouldn't require SQL)
- `clerk_draft_*`, `clerk_send` — keep with two-step confirmation
- `clerk_mark_read`, `clerk_archive` — already implemented

## 4. README Updates

Add sections:
- **Claude Code Integration**: `clerk skill install/uninstall/status`
- **Demo Environment**: docker-compose setup, `make send-test`, test accounts
- **Conversation IDs**: Prefix matching behavior
- **MCP SQL Interface**: Schema reference, example queries

## Execution Order

1. Fix integration tests (unblocks test validation)
2. Fix mypy errors (systematic, layer by layer)
3. Add MCP mutation tools (move, flag, mark-unread)
4. Add MCP SQL read tool (readonly mode)
5. Update README
6. Full test suite + coverage
7. Release v0.6.0
