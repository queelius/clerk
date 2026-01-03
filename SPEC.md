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
3. **Stateless** - Server is truth. Minimal local caching.
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

## CLI Commands

All commands support `--json` for structured output. Without it, human-readable formatting.

### Inbox & Fetch

```bash
clerk inbox                         # List recent conversations
clerk inbox --limit 50              # More results
clerk inbox --unread                # Only unread
clerk inbox --account work          # Specific account
clerk inbox --json                  # Structured output for Claude Code

clerk show <conv-id>                # Full conversation (all messages)
clerk show <conv-id> --json         # Structured for Claude Code
clerk show <message-id>             # Single message

clerk unread                        # Quick unread count per folder
clerk unread --json
```

### Search

```bash
clerk search "from:alice project"   # IMAP SEARCH
clerk search "has:attachment"       # Common operators
clerk search "after:2025-01-01"     # Date filters
clerk search "subject:urgent" --json
```

Search is server-side (IMAP SEARCH), not local.

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
- **Store email** - Server is truth, clerk caches minimally
- **Parse attachments** - Returns metadata only
- **Offline mode** - Requires server connectivity

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

### Cache Strategy

- Header-only fetch for inbox listing
- Full fetch on `show`
- 5-minute TTL for cache entries
- Cache is optional - delete anytime

---

## Roadmap

### v0.1 - Core
- [ ] IMAP connect/fetch (imapclient)
- [ ] Conversation threading
- [ ] JSON output for all commands
- [ ] Draft creation with reply headers
- [ ] SMTP send with confirmation

### v0.2 - Accounts
- [ ] Multiple accounts
- [ ] Gmail OAuth flow
- [ ] Keyring integration

### v0.3 - MCP
- [ ] MCP server implementation
- [ ] Resource endpoints
- [ ] Tool confirmation flows

---

## Name

**clerk** - a thin layer that handles correspondence on behalf of the executive (you + Claude Code).
