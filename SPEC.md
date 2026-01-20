# clerk

**A thin CLI for LLM agents to interact with email.**

Clerk is not smart. Claude Code is smart. Clerk just provides clean, structured access to your email servers via IMAP/SMTP, designed for LLM agents to consume and act upon.

---

## Philosophy

### The Division of Labor

```
┌─────────────────────────────────────┐
│         Claude Code (LLM)           │
│  • Decides what's important         │
│  • Summarizes conversations         │
│  • Drafts replies                   │
│  • Orchestrates workflows           │
└─────────────────────────────────────┘
                 │ uses
                 ▼
┌─────────────────────────────────────┐
│              clerk                   │
│  • Fetches email (IMAP)             │
│  • Sends email (SMTP)               │
│  • Returns structured JSON          │
│  • Knows nothing about content      │
└─────────────────────────────────────┘
                 │ connects to
                 ▼
┌─────────────────────────────────────┐
│          Email Servers              │
│  (Gmail, Fastmail, self-hosted)     │
└─────────────────────────────────────┘
```

Clerk is intentionally dumb. It's a bridge, not a brain.

### Design Principles

1. **Thin client** - Minimal logic. Fetch, format, send.
2. **JSON-native** - Every command has `--json` output
3. **Server is truth** - Email servers are authoritative. Local cache accelerates, never diverges.
4. **No embedded LLM** - Claude Code provides intelligence
5. **Paranoid sending** - Multiple safeguards on outbound

---

## Core Data Model

### Conversation

A conversation (thread) is the primary unit:

```json
{
  "conv_id": "abc123",
  "subject": "Q1 Planning",
  "participants": ["alice@example.com", "bob@example.com"],
  "message_count": 5,
  "unread_count": 2,
  "latest_date": "2025-01-03T10:30:00Z",
  "messages": [
    {
      "message_id": "<msg1@example.com>",
      "from": {"addr": "alice@example.com", "name": "Alice"},
      "to": [{"addr": "bob@example.com", "name": "Bob"}],
      "cc": [],
      "date": "2025-01-02T09:00:00Z",
      "subject": "Q1 Planning",
      "body_text": "...",
      "body_html": "...",
      "attachments": [
        {"filename": "plan.pdf", "size": 102400, "content_type": "application/pdf"}
      ],
      "flags": ["seen"]
    }
  ]
}
```

### Message (standalone)

```json
{
  "message_id": "<msg1@example.com>",
  "conv_id": "abc123",
  "from": {"addr": "alice@example.com", "name": "Alice"},
  "to": [...],
  "date": "...",
  "subject": "...",
  "body_text": "...",
  "flags": ["seen", "flagged"]
}
```

---

## Cache Accelerator

### Why Cache?

IMAP is slow. Every operation involves:
- TLS handshake
- Authentication
- Folder selection
- Data transfer

A typical Claude Code session might issue 10-20 commands. Without caching, that's 10-20 round trips to Gmail/Fastmail, each taking 500ms-2s. Worse, providers like Gmail have aggressive rate limits that throttle repeated connections.

IMAP SEARCH is also limited. Most servers don't support full-text body search, complex boolean queries, or sorting by relevance.

### The Solution: Transparent Cache

Clerk maintains a rolling-window cache in SQLite:

```
┌─────────────────────────────────────────────────────────────┐
│                 Cache (rolling window)                       │
│                                                              │
│   ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐    │
│   │   Headers   │  │   Bodies    │  │   Thread Index   │    │
│   │  (always)   │  │ (on-demand) │  │   (computed)     │    │
│   └─────────────┘  └─────────────┘  └──────────────────┘    │
│                                                              │
│   Window: 7 days (configurable)                              │
│   Freshness: 5 min inbox, 1 hour older messages             │
│   Storage: ~/.local/share/clerk/cache.db                     │
└─────────────────────────────────────────────────────────────┘
```

### Cache Behavior

The cache is **transparent** - commands work identically whether hitting cache or server:

```bash
clerk inbox --json        # Returns from cache if fresh, else fetches
clerk inbox --fresh       # Bypasses cache, fetches from server, updates cache
clerk show conv123        # Fetches bodies if not cached
```

**Freshness rules:**
- Inbox listing: 5 minutes (new mail might arrive)
- Message bodies: 1 hour (content doesn't change)
- Thread structure: recomputed on header updates

**Automatic maintenance:**
- Messages older than window (default 7 days) pruned on each sync
- Cache can be deleted anytime: `clerk cache clear`
- System works without cache (just slower)

### What Cache Enables

**Fast repeated queries:**
```bash
# First call: fetches from server (~1s)
clerk inbox --json

# Second call within 5 min: instant from cache
clerk inbox --json
```

**Rich local search:**
```bash
# SQLite FTS on cached bodies - queries IMAP can't do:
clerk search "from:alice deadline project"
clerk search "body:quarterly revenue"
```

**Stable session state:**
During a Claude Code conversation, the inbox doesn't shift unexpectedly mid-analysis.

### Cache Schema

```sql
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    conv_id TEXT,
    account TEXT,
    folder TEXT,
    from_addr TEXT,
    from_name TEXT,
    to_json TEXT,           -- JSON array
    cc_json TEXT,
    subject TEXT,
    date_utc TEXT,
    flags TEXT,             -- JSON: ["seen", "flagged"]
    body_text TEXT,         -- NULL until fetched
    body_html TEXT,
    attachments_json TEXT,  -- Metadata only, not content
    headers_fetched_at TEXT,
    body_fetched_at TEXT
);

CREATE INDEX idx_conv ON messages(conv_id);
CREATE INDEX idx_date ON messages(date_utc DESC);
CREATE INDEX idx_from ON messages(from_addr);
CREATE INDEX idx_folder ON messages(folder);

-- Full-text search on cached content
CREATE VIRTUAL TABLE messages_fts USING fts5(
    subject, body_text, from_name, from_addr
);
```

### Not an Archive

The cache is explicitly **not** an archive:

| Cache (clerk) | Archive (mtk) |
|---------------|---------------|
| 7-day window | All history |
| Ephemeral | Permanent |
| Performance optimization | Source of truth |
| Delete anytime | Backup/preserve |
| No attachments | Full extraction |

For long-term email preservation and deep analysis, use mtk.

---

## CLI Commands

All commands support `--json` for structured output. Without it, human-readable formatting.

### Inbox & Fetch

```bash
clerk inbox                         # List recent conversations
clerk inbox --limit 50              # More results
clerk inbox --unread                # Only unread
clerk inbox --account work          # Specific account
clerk inbox --json                  # Structured output for Claude Code
clerk inbox --fresh                 # Bypass cache, fetch from server

clerk show <conv-id>                # Full conversation (all messages)
clerk show <conv-id> --json         # Structured for Claude Code
clerk show <conv-id> --fresh        # Force fetch from server
clerk show <message-id>             # Single message

clerk unread                        # Quick unread count per folder
clerk unread --json
```

All read commands support `--fresh` to bypass cache and fetch directly from the server. The fresh data is written back to the cache.

### Search

```bash
clerk search "from:alice project"   # Search within cache window
clerk search "has:attachment"       # Common operators
clerk search "after:2025-01-01"     # Date filters
clerk search "body:quarterly"       # Full-text body search (cache only)
clerk search "subject:urgent" --json
```

Search uses SQLite FTS on the local cache, enabling queries IMAP can't do (like body full-text). Results are limited to the cache window (default 7 days). For older messages, use `--server` to fall back to IMAP SEARCH (with its limitations).

### Compose & Send

```bash
# Create a draft (does NOT send)
clerk draft --to bob@example.com \
            --subject "Quick question" \
            --body "What's the status on X?"
# Returns: draft_id

clerk draft --reply-to <conv-id> \
            --body "Thanks, that works for me."
# Returns: draft_id (with proper In-Reply-To/References headers)

# List pending drafts
clerk drafts                        # Show all drafts
clerk drafts --json

# Send a draft
clerk send <draft-id>               # Shows preview, asks confirmation
clerk send <draft-id> --yes         # Skip confirmation (dangerous)

# Delete without sending
clerk draft delete <draft-id>
```

### Account Management

```bash
clerk status                        # Connection status, account info
clerk accounts                      # List configured accounts
clerk accounts add                  # Interactive setup (IMAP/SMTP/OAuth)
clerk accounts test <name>          # Verify connectivity
```

### Folder Operations

```bash
clerk folders                       # List folders/labels
clerk move <message-id> <folder>    # Move message
clerk archive <message-id>          # Archive (Gmail) or move to Archive
clerk flag <message-id>             # Star/flag
clerk mark-read <message-id>        # Mark as read
clerk mark-unread <message-id>
```

### Cache Management

```bash
clerk cache status                  # Show cache stats (size, age, entries)
clerk cache clear                   # Delete all cached data
clerk cache refresh                 # Force full refresh from server
```

Cache operations are rarely needed - the cache is self-maintaining.

---

## Exit Codes

```
0 = Success
1 = Not found (message, conversation)
2 = Invalid input (bad query, missing args)
3 = Connection error (IMAP/SMTP failure)
4 = Auth error (credentials invalid)
5 = Send blocked (rate limit, safety check)
```

---

## Configuration

### ~/.config/clerk/config.yaml

```yaml
default_account: personal

accounts:
  personal:
    protocol: imap
    imap:
      host: imap.fastmail.com
      port: 993
      username: user@fastmail.com
      # Password from keyring, or:
      password_cmd: "pass email/fastmail"  # Command to get password
    smtp:
      host: smtp.fastmail.com
      port: 587
      username: user@fastmail.com
    from:
      address: user@fastmail.com
      name: "User Name"

  work:
    protocol: gmail
    oauth:
      client_id_file: ~/.config/clerk/gmail_client.json
      # Tokens stored in keyring

# Cache
cache:
  window_days: 7               # How far back to cache (default: 7)
  inbox_freshness_min: 5       # Inbox stale after N minutes (default: 5)
  body_freshness_min: 60       # Bodies stale after N minutes (default: 60)

# Safety
send:
  require_confirmation: true   # Always preview before send
  rate_limit: 20               # Max sends per hour
  blocked_recipients: []       # Never send to these addresses
```

### Credential Storage

Priority order:
1. System keyring (libsecret, Keychain, Windows Credential Manager)
2. `password_cmd` - shell command that outputs password
3. `password_file` - file containing password (600 permissions)

Never stored in config.yaml directly.

---

## MCP Interface

```bash
clerk mcp-server    # Start MCP server (stdio)
```

### Tools

| Tool | Description |
|------|-------------|
| `clerk_inbox(limit?, unread?, account?)` | List conversations |
| `clerk_show(conv_id)` | Get full conversation |
| `clerk_search(query, limit?)` | Search messages |
| `clerk_draft(to, subject, body, reply_to?)` | Create draft |
| `clerk_drafts()` | List pending drafts |
| `clerk_send(draft_id, confirm?)` | Send draft |
| `clerk_delete_draft(draft_id)` | Delete draft |
| `clerk_mark_read(message_id)` | Mark as read |
| `clerk_archive(message_id)` | Archive message |

### Resources

| Resource | Description |
|----------|-------------|
| `clerk://inbox` | Current inbox state |
| `clerk://conversation/{id}` | Specific thread |
| `clerk://draft/{id}` | Pending draft content |

---

## Example: Claude Code Workflow

This is how Claude Code would use clerk:

```
User: What needs my attention in email?

Claude Code thinking:
  1. Call clerk inbox --unread --json
  2. For important-looking threads, call clerk show <id> --json
  3. Analyze content and summarize for user

Claude: *runs: clerk inbox --unread --json*

You have 8 unread conversations:

**Needs response:**
- Alice Chen (VP) - "Headcount request" - asking for Q2 numbers
- Bob from DevOps - "Prod deploy blocked" - needs PR approval

**FYI only:**
- GitHub notifications (5)
- Newsletter from Hacker News

Want me to draft a reply to Alice?

User: Yes, tell her we need 2 senior engineers

Claude Code thinking:
  1. clerk show <alice-conv-id> --json  # Get context
  2. Compose reply based on context + user intent
  3. clerk draft --reply-to <conv-id> --body "..."
  4. Show user the draft
  5. If approved, clerk send <draft-id>

Claude: *runs: clerk draft --reply-to abc123 --body "Hi Alice,\n\nFor Q2, we're requesting 2 senior engineers...*

Here's the draft:
---
To: alice@company.com
Subject: Re: Headcount request

Hi Alice,

For Q2, we're requesting 2 senior engineers. Happy to discuss if adjustments are needed.

Best,
[Your name]
---

Send this?

User: Yes

Claude: *runs: clerk send draft_xyz --yes*

Sent!
```

---

## Security Model

### Sending Safeguards

1. **Confirmation by default** - `clerk send` shows preview, requires Enter
2. **Rate limiting** - Configurable sends per hour (default: 20)
3. **Audit log** - All sent messages logged to `~/.local/share/clerk/sent.log`
4. **FROM verification** - Refuses to send if FROM doesn't match account
5. **No --yes in MCP** - MCP tools always require explicit confirmation flow

### Audit Log

Append-only log of all sends:

```
~/.local/share/clerk/sent.log
```

```json
{"timestamp": "2025-01-03T10:30:00Z", "to": ["bob@example.com"], "subject": "Re: Project", "account": "work"}
```

---

## What Clerk Doesn't Do

- **Summarize** - Claude Code does this
- **Prioritize** - Claude Code does this
- **Draft content** - Claude Code composes, clerk just stores/sends
- **Archive email** - Server is truth, cache is ephemeral (use mtk for archiving)
- **Parse attachments** - Returns metadata only
- **Offline mode** - Cache helps with recent data, but sending requires connectivity

---

## File Locations

```
~/.config/clerk/
  config.yaml           # Configuration
  gmail_client.json     # OAuth client (if using Gmail)

~/.local/share/clerk/
  sent.log              # Audit log (append-only)
  cache.db              # Optional message cache (ephemeral)
  oauth_tokens/         # OAuth refresh tokens (encrypted)
```

---

## Implementation

### Python Libraries

```
imapclient       # IMAP operations
aiosmtplib       # Async SMTP
keyring          # Credential storage
typer            # CLI framework
rich             # Terminal formatting
pydantic         # Config validation
google-auth      # Gmail OAuth (optional)
```

### Threading Algorithm

1. Use IMAP THREAD extension if available
2. Fall back to References/In-Reply-To header walking
3. conv_id = hash of thread root message_id

### Cache Implementation

See [Cache Accelerator](#cache-accelerator) for detailed design. Key points:
- SQLite with FTS5 for full-text search
- Headers fetched eagerly, bodies lazily
- Automatic pruning of messages outside window
- Connection pooling to reduce IMAP overhead

---

## Roadmap

### v0.1 - Core (Complete)
- [x] IMAP connect/fetch (imapclient)
- [x] SQLite cache with FTS5
- [x] Conversation threading
- [x] JSON output for all commands
- [x] Draft creation with reply headers
- [x] SMTP send with confirmation

### v0.2 - Accounts & Polish (Complete)
- [x] Multiple accounts
- [x] Gmail OAuth flow
- [x] Keyring integration
- [x] Cache management commands

### v0.3 - MCP (Complete)
- [x] MCP server implementation
- [x] Resource endpoints
- [x] Tool confirmation flows

### v0.4 - API Layer & Advanced Features (Complete)
- [x] ClerkAPI - centralized business logic layer
- [x] Advanced search parser with operators (from:, to:, subject:, body:, has:, is:, after:, before:, date:)
- [x] Raw SQL search for power users
- [x] Interactive shell with tab completion
- [x] Attachment download support
- [x] Integration tests with Greenmail
- [x] MCP tools: clerk_search_sql, clerk_attachments

### v0.5 - Future
- [ ] Batch operations
- [ ] Email rules/filters
- [ ] Calendar integration
- [ ] Contact management

---

## Name

**clerk** - a thin layer that handles correspondence on behalf of the executive (you + Claude Code).
