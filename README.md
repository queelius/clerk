# clerk

A thin CLI for LLM agents to interact with email via IMAP/SMTP.

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
│  • Fetches email (IMAP)             │
│  • Sends email (SMTP)               │
│  • Returns structured JSON          │
│  • Knows nothing about content      │
└─────────────────────────────────────┘
```

## Installation

```bash
pip install clerk
```

Or install from source:

```bash
git clone https://github.com/spinoza/clerk.git
cd clerk
pip install -e .
```

## Quick Start

### 1. Add an account

```bash
# Interactive setup for IMAP/SMTP
clerk accounts add --name personal

# Or for Gmail with OAuth
clerk accounts add-gmail work
```

### 2. Check your inbox

```bash
clerk inbox
clerk inbox --unread --json
```

### 3. Read a conversation

```bash
clerk show <conv-id>
clerk show <conv-id> --json
```

### 4. Search

```bash
clerk search "from:alice project deadline"
clerk search "has:attachment after:2025-01-01"
```

### 5. Compose and send

```bash
# Create a draft
clerk draft new --to bob@example.com --subject "Hello" --body "Hi there!"

# Review it
clerk draft show <draft-id>

# Send it (requires confirmation)
clerk send <draft-id>
```

## CLI Reference

### Inbox & Messages

```bash
clerk inbox                     # List conversations
clerk inbox --limit 50          # More results
clerk inbox --unread            # Only unread
clerk inbox --fresh             # Bypass cache
clerk inbox --json              # JSON output

clerk show <conv-id>            # Show conversation
clerk show <message-id>         # Show single message

clerk unread                    # Unread counts by folder
```

### Search

```bash
# Basic search (FTS on cached messages)
clerk search "quarterly report"

# Advanced search with operators
clerk search "from:alice subject:meeting has:attachment"

# Raw SQL for power users
clerk search-sql "SELECT * FROM messages WHERE from_addr LIKE '%@example.com'"
```

#### Search Operators

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:alice` | Sender contains |
| `to:` | `to:bob@example.com` | Recipient contains |
| `subject:` | `subject:meeting` | Subject contains |
| `body:` | `body:quarterly` | Body contains |
| `has:attachment` | `has:attachment` | Has attachments |
| `is:unread` | `is:unread` | Unread messages |
| `is:read` | `is:read` | Read messages |
| `is:flagged` | `is:flagged` | Starred/flagged |
| `after:` | `after:2025-01-01` | After date |
| `before:` | `before:2025-01-15` | Before date |
| `date:` | `date:2025-01-10` | On specific date |

### Drafts & Sending

```bash
clerk draft new --to bob@example.com --subject "Hi" --body "Hello!"
clerk draft new --reply-to <conv-id> --body "Thanks!"
clerk draft list
clerk draft show <draft-id>
clerk draft delete <draft-id>

clerk send <draft-id>           # Preview and confirm
```

### Attachments

```bash
clerk attachment <message-id> --list
clerk attachment <message-id> document.pdf --save ./downloads/
```

### Folders

```bash
clerk folders                   # List folders
clerk move <message-id> Archive
clerk archive <message-id>      # Move to Archive
```

### Interactive Shell

```bash
clerk shell
```

The shell provides all CLI commands with tab completion and history:

```
clerk> inbox --limit 5
clerk> search from:alice
clerk> sql SELECT * FROM messages LIMIT 10
clerk> exit
```

### Account Management

```bash
clerk accounts list
clerk accounts add --name work
clerk accounts add-gmail personal
clerk accounts test work
clerk accounts remove work
```

### Cache

```bash
clerk cache status
clerk cache clear
clerk cache refresh
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
    protocol: gmail
    oauth:
      client_id_file: ~/.config/clerk/gmail_client.json

cache:
  window_days: 7
  inbox_freshness_min: 5
  body_freshness_min: 60

send:
  require_confirmation: true
  rate_limit: 20
```

### Credential Storage

Passwords are stored in your system keyring (libsecret, macOS Keychain, Windows Credential Manager).

Alternative methods:
- `password_cmd: "pass email/fastmail"` - command that outputs password
- `password_file: ~/.secrets/email.txt` - file containing password

## MCP Server

Clerk includes an MCP (Model Context Protocol) server for LLM integration:

```bash
clerk mcp-server
```

Add to Claude Code's MCP configuration:

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

### Available Tools

| Tool | Description |
|------|-------------|
| `clerk_inbox` | List conversations |
| `clerk_show` | Get message details |
| `clerk_conversation` | Get full thread |
| `clerk_search` | Search messages |
| `clerk_search_sql` | Raw SQL search |
| `clerk_draft` | Create draft |
| `clerk_drafts` | List drafts |
| `clerk_send` | Send draft |
| `clerk_delete_draft` | Delete draft |
| `clerk_folders` | List folders |
| `clerk_unread` | Unread counts |
| `clerk_move` | Move message |
| `clerk_attachments` | List/download attachments |

## Data Locations

```
~/.config/clerk/
  config.yaml           # Configuration
  gmail_client.json     # Gmail OAuth client (optional)

~/.local/share/clerk/
  cache.db              # Message cache (ephemeral)
  drafts/               # Pending drafts
  sent.log              # Audit log
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run integration tests (requires Docker)
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/
docker-compose -f docker-compose.test.yml down

# Lint
ruff check src tests
```

## License

MIT License - see [LICENSE](LICENSE) for details.
