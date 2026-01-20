# clerk - Email CLI for LLM Agents

Clerk is a thin CLI for interacting with email via IMAP/SMTP, designed for LLM agents.

## Quick Reference

```bash
# List inbox (from cache or fresh)
clerk inbox [--limit N] [--unread] [--fresh] [--json]

# Show a conversation
clerk show <conv-id> [--json]

# Search with operators
clerk search "from:alice subject:meeting has:attachment"
clerk search-advanced "from:alice after:2025-01-01 is:unread"
clerk search-sql "SELECT * FROM messages WHERE subject LIKE '%urgent%'"

# Drafts
clerk draft new --to bob@example.com --subject "Hello" --body "..."
clerk draft list [--json]
clerk draft show <draft-id>
clerk draft delete <draft-id>

# Send (two-step confirmation)
clerk send <draft-id>

# Attachments
clerk attachment <message-id> --list
clerk attachment <message-id> <filename> --save ./downloads/

# Folders
clerk folders [--account name]
clerk move <message-id> <folder>

# Interactive shell
clerk shell

# Account management
clerk accounts list
clerk accounts add [--name NAME] [--protocol imap|gmail]
clerk accounts add-gmail <name>
clerk accounts test <name>
clerk accounts remove <name>

# Cache management
clerk cache status
clerk cache clear
```

## Search Operators

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:alice` | Sender address contains |
| `to:` | `to:bob@example.com` | Recipient contains |
| `subject:` | `subject:meeting` | Subject contains |
| `body:` | `body:quarterly` | Body contains (FTS) |
| `has:attachment` | `has:attachment` | Has attachments |
| `is:unread` | `is:unread` | Unread messages |
| `is:read` | `is:read` | Read messages |
| `is:flagged` | `is:flagged` | Starred/flagged |
| `after:` | `after:2025-01-01` | After date |
| `before:` | `before:2025-01-15` | Before date |
| `date:` | `date:2025-01-10` | On specific date |

Combine operators: `from:alice subject:meeting after:2025-01-01`

## MCP Server

Start the MCP server for programmatic access:

```bash
clerk mcp-server
```

### Available Tools

| Tool | Description |
|------|-------------|
| `clerk_inbox` | List conversations |
| `clerk_show` | Get message details |
| `clerk_conversation` | Get full conversation thread |
| `clerk_search` | Search messages (basic) |
| `clerk_search_sql` | Search with raw SQL |
| `clerk_draft` | Create new draft |
| `clerk_drafts` | List pending drafts |
| `clerk_send` | Send a draft |
| `clerk_delete_draft` | Delete a draft |
| `clerk_folders` | List IMAP folders |
| `clerk_unread` | Get unread counts |
| `clerk_move` | Move message to folder |
| `clerk_attachments` | List/download attachments |

## Workflow Example

```bash
# 1. Check inbox
clerk inbox --unread --json

# 2. Read specific conversation
clerk show <conv-id> --json

# 3. Create reply draft
clerk draft new --reply-to <conv-id> --body "Thanks for the update..."

# 4. Review and send
clerk draft show <draft-id>
clerk send <draft-id>
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

cache:
  window_days: 7
  inbox_freshness_min: 5
```

## Data Locations

- Config: `~/.config/clerk/config.yaml`
- Cache: `~/.local/share/clerk/cache.db`
- Drafts: `~/.local/share/clerk/drafts/`
- Sent log: `~/.local/share/clerk/sent.log`
