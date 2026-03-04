# MCP Server Redesign — Design Document

> **Goal:** Simplify clerk's MCP server from 17 tools to 8, using SQL as the universal read interface, adding reply capability, incremental sync, and priority config.

## Motivation

Research on MCP server design best practices (2025-2026) shows:

- **5-15 tools per server** is optimal; performance degrades above 30
- **"Outcomes over operations"** — high-level tools that orchestrate internally beat many granular tools
- **Resources for context** — discoverable schema and config, not baked into tool descriptions
- **Flat enums** over separate tools for related operations

Clerk currently has 17 tools and 3 resources. Many read tools are redundant once SQL is the primary read interface. The reply workflow requires Claude to manually extract headers — a dedicated tool should handle this.

Sources:
- [Phil Schmid — MCP Best Practices](https://www.philschmid.de/mcp-best-practices)
- [Speakeasy — MCP Tools: Less Is More](https://www.speakeasy.com/mcp/tool-design/less-is-more)
- [MCP Best Practices: Architecture Guide](https://modelcontextprotocol.info/docs/best-practices/)
- [Docker — Top 5 MCP Server Best Practices](https://www.docker.com/blog/mcp-server-best-practices/)

## Tool Surface (8 tools)

### Read Tools (2)

**`clerk_sql(query, limit=100)`**
- Execute readonly SQL SELECT on the cache database
- Schema discoverable via `clerk://schema` resource
- Returns `{rows: [...], count: N}`
- Forced readonly via SQLite `PRAGMA query_only`
- Replaces: `clerk_inbox`, `clerk_show`, `clerk_search`, `clerk_search_sql`, the old `clerk_sql`, `clerk_attachments`, `clerk_drafts`

**`clerk_sync(account=None, folder="INBOX", full=False)`**
- Refresh cache from IMAP server
- Default: incremental sync (only new messages since last sync via UID tracking)
- `full=True`: re-fetch everything in the folder
- `account=None`: sync all configured accounts
- Returns `{synced: N, account: str, folder: str}`

### Write Tools (5)

**`clerk_reply(message_id, body, reply_all=False, account=None)`**
- Reply to a message with auto-populated headers
- Auto-populates: `To` (original sender), `Cc` (if reply_all), `Subject` (`Re: ...`), `In-Reply-To`, `References`
- Creates a draft internally
- Returns `{draft_id, preview, to, cc, subject}` for Claude to show user for confirmation
- Claude asks user "Shall I send this?" — if yes, calls `clerk_send`

**`clerk_draft(to, subject, body, cc=None, account=None)`**
- Compose a new message (not a reply)
- Returns `{draft_id, preview}` for user confirmation before sending

**`clerk_send(draft_id, token=None)`**
- Send a draft with two-step confirmation token flow
- First call (no token): returns preview + confirmation token
- Second call (with token): actually sends
- Safety gate even after user confirms to Claude

**`clerk_move(message_id, to_folder, from_folder="INBOX", account=None)`**
- Move a message between folders
- Handles archive (move to Archive), trash (move to Trash), folder organization

**`clerk_flag(message_id, action, account=None)`**
- `action: Literal["flag", "unflag", "read", "unread"]`
- Consolidates 4 old tools into 1 with flat enum parameter

### Meta Tool (1)

**`clerk_status()`**
- Account info, connection health, cache stats

## Resources (3)

### `clerk://schema`
Full DDL (CREATE TABLE, indexes, FTS, triggers) plus example queries for common operations:
- Inbox listing
- Thread history (for composing replies)
- Unread counts by folder
- FTS full-text search
- Priority sender filtering

### `clerk://config`
Config as JSON with sensitive fields redacted:
```json
{
  "default_account": "demo",
  "accounts": {
    "siue": {"protocol": "microsoft365", "from": "atowell@siue.edu"},
    "demo": {"protocol": "imap", "from": "demo@example.com"}
  },
  "priorities": {
    "senders": ["hfujino@siue.edu", "@siue.edu"],
    "topics": ["IDOT", "scanner", "dissertation"]
  }
}
```

### `clerk://folders`
Available folders per account, fetched from IMAP. Returns JSON:
```json
{
  "siue": ["INBOX", "Sent Items", "Drafts", "Archive", ...],
  "demo": ["INBOX", "Sent", "Drafts", "Trash", ...]
}
```

## Incremental Sync

### New `sync_state` table
```sql
CREATE TABLE IF NOT EXISTS sync_state (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    last_uid INTEGER DEFAULT 0,
    last_sync_utc TEXT,
    PRIMARY KEY (account, folder)
);
```

### Sync flow
1. Look up `last_uid` for (account, folder)
2. IMAP `UID FETCH <last_uid+1>:*` — only fetches new messages
3. Insert new messages into cache
4. Update `sync_state` with highest UID seen
5. `full=True` bypasses UID tracking and re-fetches everything

### Body fetching
Bodies remain lazily fetched. `clerk_sql` queries return whatever's cached. If a body is NULL and Claude needs it, it can call `clerk_sync` for that folder (or a future per-message body fetch could be added).

## Priority Config

Added to `config.yaml`:
```yaml
priorities:
  senders:
    - "hfujino@siue.edu"     # Exact match
    - "@siue.edu"             # Domain match
  topics:
    - "IDOT"
    - "scanner"
    - "dissertation"
```

Exposed via `clerk://config` resource. Claude uses this to:
- Prioritize which messages to surface in summaries
- Compose SQL filters: `WHERE from_addr LIKE '%@siue.edu%'`
- Highlight important threads

No dedicated tool needed — it's config data Claude reads and acts on.

## Cleanup: Remove `skill` CLI group

Remove:
- `src/clerk/skill.py` — skill content + install/uninstall logic
- `skill_app` typer group from `cli.py`
- `tests/test_skill.py`

The MCP server replaces the skill entirely. Users configure clerk as an MCP server in their Claude Code settings, and the resources provide all context Claude needs.

## Before / After Comparison

### Before (17 tools, 3 resources)
```
READS:  clerk_inbox, clerk_show, clerk_search, clerk_search_sql, clerk_sql,
        clerk_attachments, clerk_drafts
WRITES: clerk_draft, clerk_send, clerk_delete_draft, clerk_mark_read,
        clerk_mark_unread, clerk_archive, clerk_move, clerk_flag
META:   clerk_status
RESOURCES: clerk://inbox, clerk://conversation/{id}, clerk://draft/{id}
```

### After (8 tools, 3 resources)
```
READS:  clerk_sql, clerk_sync
WRITES: clerk_reply, clerk_draft, clerk_send, clerk_move, clerk_flag
META:   clerk_status
RESOURCES: clerk://schema, clerk://config, clerk://folders
```

## Principles Applied
- **Outcomes over operations** — `clerk_reply` orchestrates header population internally
- **SQL as universal read** — one tool replaces seven
- **Resources for context** — schema and config are discoverable without tool calls
- **Flat enums** — `clerk_flag(action="read")` replaces `clerk_mark_read`
- **8 tools** — within the 5-15 optimal range per MCP best practices research
- **Agent-friendly errors** — return "try X instead" messages for self-correction
