# Clerk v0.7.0 — Radical Simplification

**Goal:** Reframe clerk as an MCP server for Claude Code. Delete dead code, gut the CLI, slim the API layer, fix MCP tool issues.

**Breaking change:** All CLI read/write email commands removed. The CLI becomes setup/auth/debug tooling only.

---

## 1. Deletions

### 1.1 Kill shell.py

Delete `src/clerk/shell.py` (544 lines). Interactive REPL with 0% test coverage. Dead code — the MCP server is the agent interface.

### 1.2 Gut cli.py (1,363 → ~200 lines)

**Delete these CLI commands:**
- `inbox`, `show`, `search` — reads served by `clerk_sql`
- `draft`, `drafts`, `send`, `delete-draft` — writes served by `clerk_draft` / `clerk_reply` / `clerk_send`
- `flag`, `unflag`, `mark-read`, `mark-unread`, `move`, `archive` — served by `clerk_flag` / `clerk_move`
- `attachments`, `download-attachment` — attachment listing via `clerk_sql`
- `refresh` — served by `clerk_sync`

**Keep these CLI commands:**
- `mcp-server` — launch MCP server (the primary entry point)
- `accounts` group — `accounts list`, `accounts add`, `accounts test`, `accounts remove`, `accounts auth` (these are the actual command names in code)
- `status` — connection health check (`clerk status`)
- **New:** `sync` — create a top-level command wrapping `api.sync_folder()` for debugging (`clerk sync [--account NAME] [--full]`)
- `cache stats` / `cache clear` — cache diagnostics

### 1.3 Slim api.py (778 → ~450 lines)

**Delete these dataclasses** (CLI-only output types):
- `InboxResult`
- `SearchResult`
- `SendPreview`
- `ConversationLookupResult`

**Delete these methods** (reads now served by `clerk_sql`):
- `list_inbox()`
- `search()`
- `search_advanced()`
- `search_sql()`
- `resolve_conversation_id()`
- `refresh_cache()`
- `list_attachments()` — only called from deleted CLI `attachments` command
- `download_attachment()` — only called from deleted CLI `download-attachment` command

**Keep these methods** (used by MCP tools):
- `sync_folder()`, `get_message()`, `get_conversation()`
- `create_draft()`, `get_draft()`, `list_drafts()`, `update_draft()`, `delete_draft()`, `send_draft()`
- `mark_read()`, `mark_unread()`, `flag_message()`, `unflag_message()`
- `move_message()`, `archive_message()`
- `list_folders()`, `get_unread_counts()`
- `get_status()`, `get_cache_stats()`, `clear_cache()`

### 1.4 Dead code in cache.py

**Delete these methods** (only called by deleted api.py methods):
- `search_advanced()` — called by `api.search_advanced()`
- `execute_raw_query()` — called by `api.search_sql()`

**Delete:**
- `search()` — no remaining callers after `api.search()` is deleted. Dead code.

**Keep:**
- `execute_readonly_sql()` — used by `clerk_sql`
- FTS table + triggers — `clerk_sql` users can write MATCH queries
- `find_conversations_by_prefix()` — useful for MCP if we want prefix lookups later

**Keep search.py** (402 lines) — the FTS infrastructure (`messages_fts` table, MATCH queries) is still valuable for `clerk_sql` users. The `parse_search_query()` and `build_fts_query()` functions become unused by internal code. Consider deleting `search.py` in a follow-up if truly unused, but the FTS table schema and triggers in cache.py must stay.

---

## 2. MCP Tool Fixes

### 2.1 clerk_reply — route through API layer

**Before:** calls `api.cache.get_message()` and `api.drafts.create_reply()` directly.

**After:** calls `api.get_message()` and `api.create_reply()`.

Add a new `api.create_reply(message_id, body, reply_all=False, account=None)` method that wraps `get_message()` + `drafts.create_reply()` and passes `reply_all` through. The existing `api.create_draft()` handles new compositions; `api.create_reply()` handles replies. This avoids losing `reply_all` support (which `create_draft()` doesn't forward).

This respects the architecture rule that MCP tools call `api.*`, never `api.cache.*` or `api.drafts.*`.

**Note:** `clerk_sql` calling `api.cache.execute_readonly_sql()` is an intentional exception — raw SQL is a cache-level operation by nature, and there's no value in an `api.execute_sql()` passthrough.

### 2.2 clerk_draft — use list params

**Before:**
```python
def clerk_draft(to: str, ..., cc: str | None = None) -> ...:
    to_addrs = [a.strip() for a in to.split(",")]
```

**After:**
```python
def clerk_draft(to: list[str], ..., cc: list[str] | None = None) -> ...:
```

Eliminates fragile comma-splitting that breaks on `"Doe, John" <john@example.com>`.

### 2.3 clerk_reply — drop redundant preview

**Before:** `clerk_reply` generates a preview, then `clerk_send` step 1 generates another preview.

**After:** `clerk_reply` returns `{draft_id, to, cc, subject, message: "call clerk_send to preview and send"}`. No preview text. The preview happens once at `clerk_send` step 1, where it belongs.

Flow: `clerk_reply(message_id, body)` → `clerk_send(draft_id)` → `clerk_send(draft_id, token)`. Two meaningful calls instead of three.

### 2.4 clerk_sync — add sync-all mode

**Before:** Syncs one folder for one account.

**After:** When called with no arguments, syncs INBOX for every configured account. Returns per-account results:

```python
@mcp.tool()
def clerk_sync(
    account: str | None = None,
    folder: str = "INBOX",
    full: bool = False,
) -> dict[str, Any]:
```

When `account` is None, iterate all accounts and sync each. Return:
```json
{
  "accounts": {
    "siue": {"synced": 5, "folder": "INBOX"},
    "gmail": {"synced": 12, "folder": "INBOX"}
  },
  "total_synced": 17
}
```

When `account` is specified, behave as today (single account).

### 2.5 resource_folders — cache with TTL

**Before:** Opens IMAP connection to every account on each resource read.

**After:** Cache folder lists in `cache_meta` table with 1-hour TTL. On read:
1. Check `cache_meta` for `folders_{account}` with timestamp
2. If fresh (< 1 hour), return cached
3. If stale, fetch from IMAP, update cache, return

### 2.6 clerk_reply — sync hint on missing message

Already implemented. Keep as-is after routing through `api.get_message()`.

---

## 3. Architecture After

```
┌─────────────────────────────────────────────────────────────┐
│  Entry Points                                                │
│  ├── mcp_server.py  (8 tools + 3 resources — primary)       │
│  └── cli.py  (~200 lines — setup/auth/debug only)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  api.py - ClerkAPI  (slimmed — mutations + sync + status)   │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ imap_client.py  │  │    cache.py     │  │  smtp_client.py │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│  models.py, config.py, drafts.py, threading.py, search.py  │
└─────────────────────────────────────────────────────────────┘
```

### Estimated LOC

| Module | Before | After | Delta |
|--------|--------|-------|-------|
| cli.py | 1,363 | ~550 | -813 |
| shell.py | 544 | 0 | -544 |
| api.py | 778 | ~450 | -328 |
| cache.py | 798 | ~750 | -48 |
| mcp_server.py | 449 | ~470 | +21 |
| Others | 2,584 | 2,584 | 0 |
| **Total** | **6,516** | **~4,804** | **-1,712** |

---

## 4. Test Impact

### Delete
- Most of `test_cli.py` — tests for removed commands
- Any tests for `shell.py` (none exist)

### Keep as-is
- `test_cache.py`, `test_config.py`, `test_drafts.py`, `test_models.py`
- `test_threading.py`, `test_search.py`, `test_parse_address.py`
- `test_oauth.py`, `test_microsoft365.py`, `test_imap_m365.py`, `test_smtp_m365.py`
- `test_mcp_redesign.py`, `test_mcp_sql.py`
- All integration tests

### Update
- `test_api.py` — remove tests for deleted methods
- `test_cli.py` — keep only tests for retained commands (setup, auth, status, sync, cache)

### Add
- `clerk_sync` with all-accounts mode
- `clerk_draft` with `list[str]` params
- `clerk_reply` routing through API layer
- Cached `resource_folders`

---

## 5. Version & Migration

- Bump to **0.7.0** — breaking CLI changes
- Update `pyproject.toml` version
- Update `CLAUDE.md` architecture diagram to reflect new structure
- Update README to reflect MCP-primary design
- The v0.6.0 plan doc (`docs/plans/2026-02-16-v060-cleanup-mcp-parity.md`) is stale — delete or archive
- Remove `prompt-toolkit>=3.0.0` from `[project.dependencies]` in `pyproject.toml` — only imported by deleted `shell.py`
