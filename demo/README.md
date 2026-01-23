# Clerk Demo Environment

Try out clerk with a local mock email server. No real email account needed.

## Quick Start

```bash
# 1. Start the mock email server
make start

# 2. Configure clerk to use demo server
make setup

# 3. Send some test emails to play with
make send-test

# 4. Try clerk!
clerk inbox --fresh
```

## Available Commands

| Command | Description |
|---------|-------------|
| `make start` | Start mock email server (Greenmail) |
| `make stop` | Stop the server |
| `make setup` | Install demo config for clerk |
| `make send-test` | Populate inbox with sample emails |
| `make shell` | Open clerk interactive shell |
| `make logs` | View server logs |
| `make clean` | Stop server and restore original config |

## Things to Try

### Basic Inbox Operations

```bash
# View inbox
clerk inbox --fresh

# View only unread
clerk inbox --unread

# Get JSON output (what LLMs see)
clerk inbox --json

# Show a specific conversation
clerk show <conv-id>
```

### Search

```bash
# Full-text search
clerk search "project"

# Search by sender
clerk search "from:alice"

# Search with operators
clerk search-advanced "from:bob has:attachment"

# Date filtering
clerk search-advanced "after:2025-01-01 subject:update"
```

### Drafts & Sending

```bash
# Create a draft
clerk draft --to alice@example.com --subject "Hello" --body "Test message"

# List drafts
clerk drafts

# Send a draft (will ask for confirmation)
clerk send <draft-id>

# Reply to a conversation
clerk draft --reply-to <conv-id> --body "Thanks for the update!"
```

### Interactive Shell

```bash
clerk shell
```

In the shell, try:
```
clerk> help
clerk> inbox
clerk> search from:alice
clerk> show <conv-id>
clerk> draft --to bob@example.com --subject "Hi" --body "Hello!"
clerk> drafts
clerk> exit
```

### Folder Operations

```bash
# List folders
clerk folders

# Mark as read
clerk mark-read <message-id>

# Flag/star a message
clerk flag <message-id>
```

### Cache Management

```bash
# View cache stats
clerk cache status

# Force refresh from server
clerk inbox --fresh

# Clear cache
clerk cache clear
```

### Multiple Accounts

The demo includes 3 accounts: `demo`, `alice`, and `bob`.

```bash
# Use a specific account
clerk inbox --account alice --fresh

# Send from different account
clerk draft --account bob --to demo@example.com --subject "From Bob"
```

## Demo Accounts

| Account | Email | Password |
|---------|-------|----------|
| demo | demo@example.com | demo |
| alice | alice@example.com | alice |
| bob | bob@example.com | bob |

## Cleanup

When done, restore your original configuration:

```bash
make clean
```

This stops the Docker container and restores your backed-up config.

## Troubleshooting

**Server won't start?**
```bash
# Check if ports are in use
lsof -i :3143
lsof -i :3025

# Check Docker status
docker ps
make logs
```

**Connection refused?**
```bash
# Wait a few seconds after starting
make status
# Should show "clerk-demo-mail" as "Up"
```

**Want to start fresh?**
```bash
make clean
make start
make setup
make send-test
```
