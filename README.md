# clerk

A thin MCP server (and small CLI) that lets an LLM agent interact with email over IMAP/SMTP.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Philosophy

Clerk is intentionally dumb. It's a bridge, not a brain.

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
│  • Syncs email into a local cache   │
│  • Exposes it as SQL + MCP tools    │
│  • Sends email (SMTP), paranoidly   │
│  • Knows nothing about content      │
└─────────────────────────────────────┘
```

The LLM provides the intelligence (summarizing, prioritizing, drafting). Clerk
provides safe, structured access to your mail server. The primary interface is an
MCP server; the CLI exists only for setup, auth, and debugging.

## Installation

```bash
pip install email-clerk
```

Or from source:

```bash
git clone https://github.com/queelius/clerk.git
cd clerk
pip install -e .
```

The installed command is `clerk`.

## How it works

1. **Sync** pulls messages from IMAP into a local SQLite cache (with FTS5 full-text
   search). Bodies are fetched eagerly so search is complete; the cache is kept
   faithful to the server (flags, moves, and deletions made on other devices are
   reconciled on each sync).
2. **Read** happens against the cache: the agent runs SQL with `clerk_sql` and pulls
   full message bodies with `clerk_read`. No round-trip to the server on the read path.
3. **Write** (flags, moves, sends) goes to the server first; the cache is updated only
   after the server confirms.

## MCP server

This is the primary interface. Start it with:

```bash
clerk mcp-server
```

Add it to Claude Code's MCP configuration:

```json
{
  "mcpServers": {
    "clerk": {
      "command": "clerk",
      "args": ["mcp-server"]
    }
  }
}
```

### Tools

| Tool | Description |
|------|-------------|
| `clerk_sql` | Run a read-only SQL `SELECT` over the cached messages (the main read path). |
| `clerk_read` | Read one full message by `message_id` (fetches the body from IMAP if needed). |
| `clerk_sync` | Sync a folder from IMAP into the cache (all accounts if none given). |
| `clerk_reply` | Create a reply draft to a message, with headers auto-populated. |
| `clerk_draft` | Create a new (non-reply) draft. |
| `clerk_send` | Send a draft. Two-step: call once for a preview + token, again with the token to send. |
| `clerk_move` | Move a message to another folder. |
| `clerk_flag` | Set a flag: `flag` / `unflag` / `read` / `unread`. |
| `clerk_status` | Version, per-account connection health and sync freshness, and a cache summary. |
| `clerk_auth` | Re-authenticate an account (M365 device code, Gmail refresh, IMAP password). |

### Resources

| Resource | Description |
|----------|-------------|
| `clerk://schema` | Cache DB schema plus example SQL queries for `clerk_sql`. |
| `clerk://config` | Accounts, default account, settings (secrets redacted). |
| `clerk://folders` | Available folders per account (cached for an hour). |

### Reading mail (the SQL model)

Reads are SQL, not a fixed set of verbs. Read `clerk://schema` for the columns and
examples, then query with `clerk_sql`. Flags are an INTEGER bitmask
(`SEEN=1, ANSWERED=2, FLAGGED=4, DELETED=8, DRAFT=16`); unread is `flags & 1 = 0`.

```sql
-- recent inbox
SELECT conv_id, from_addr, subject, date_utc, flags
FROM messages WHERE folder='INBOX' AND account='personal'
ORDER BY date_utc DESC LIMIT 20

-- unread counts by folder
SELECT folder, COUNT(*) AS unread FROM messages WHERE flags & 1 = 0 GROUP BY folder

-- relevance-ranked full-text search with a snippet
SELECT m.message_id, m.subject, snippet(messages_fts, 2, '[', ']', ' ... ', 10) AS preview
FROM messages_fts f JOIN messages m ON m.rowid = f.rowid
WHERE messages_fts MATCH 'quarterly report'
ORDER BY bm25(messages_fts) LIMIT 20
```

Then `clerk_read` a specific `message_id` for the full body.

> Note: attachment download/sending is not yet supported. `clerk_read` reports
> attachment metadata (filename, size, content type) but cannot fetch the bytes.

## CLI reference

The CLI is for setup, auth, and debugging only. All mail operations go through the
MCP tools above.

```bash
clerk mcp-server                # start the MCP server (primary interface)
clerk version                   # print version
clerk status [--json]           # connection status and account info
clerk sync [-a ACCT] [-f FOLDER] [--full]   # sync the cache from IMAP

clerk cache status [--json]     # cache statistics
clerk cache clear               # clear cached messages and drafts (keeps the send audit log)

clerk accounts                  # list configured accounts
clerk accounts add NAME [-p PROTOCOL] [-e EMAIL] [--default]
clerk accounts test NAME        # test IMAP and SMTP connectivity
clerk accounts remove NAME [-y]
clerk accounts auth NAME        # run the OAuth / device-code flow in the terminal
```

## Configuration

Config file: `~/.config/clerk/config.yaml`

```yaml
default_account: personal

accounts:
  personal:
    protocol: imap
    imap:
      host: imap.fastmail.com
      port: 993
      username: user@fastmail.com
    smtp:
      host: smtp.fastmail.com
      port: 587
      username: user@fastmail.com
    from:
      address: user@fastmail.com
      name: "User Name"

  work:
    protocol: gmail        # or microsoft365
    oauth:
      client_id_file: ~/.config/clerk/gmail_client.json

cache:
  window_days: 7           # retention window (only pruned when prune_enabled)
  inbox_freshness_min: 5   # staleness threshold for clerk_status
  body_freshness_min: 60
  body_max_bytes: 1000000  # bodies larger than this are fetched on demand, not cached
  sync_chunk_size: 200     # messages fetched per sync chunk
  reconcile_window: 500    # most-recent cached UIDs re-checked for flag/expunge drift per sync (0 disables)
  prune_enabled: false     # if true, sync deletes cached messages older than window_days

send:
  require_confirmation: true
  rate_limit: 20           # max sends per hour (persistent, survives restarts)
  blocked_recipients: []
```

### Credential storage

Passwords are stored in your system keyring (libsecret, macOS Keychain, Windows
Credential Manager). Alternatives, per account:

- `password_cmd: "pass email/fastmail"` (a command that prints the password)
- `password_file: ~/.secrets/email.txt` (a file with 0600 permissions)

OAuth tokens (Gmail) and the M365 MSAL token cache are also kept in the keyring.

### Sending safety

Sending is paranoid by design: a persistent hourly rate limit, a blocked-recipients
list, mandatory two-step confirmation via `clerk_send` (the token is bound to the
draft's content, so editing the draft invalidates it), a FROM/account check, and an
append-only audit log.

## Data locations

```
~/.config/clerk/
  config.yaml           # configuration
  gmail_client.json     # Gmail OAuth client (optional)

~/.local/share/clerk/
  cache.db              # SQLite cache: messages (+ FTS5), drafts, send audit log
```

## Development

```bash
pip install -e ".[dev]"

pytest                          # unit tests
ruff check src tests            # lint
mypy src                        # type check

# Integration tests (require Docker; use a Greenmail mail server)
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/
docker-compose -f docker-compose.test.yml down
```

## License

MIT License. See [LICENSE](LICENSE) for details.
