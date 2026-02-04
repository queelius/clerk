"""Clerk skill management for Claude Code integration."""

import shutil
from pathlib import Path
from typing import NamedTuple

SKILL_CONTENT = '''---
name: clerk
description: clerk - Email CLI for LLM Agents
---

# clerk - Email CLI for LLM Agents

Use this skill when the user wants to check, read, search, or manage their email. Invoke for reading inbox, searching messages, viewing conversations, composing drafts, sending emails, or managing folders.

## Quick Reference

```bash
# List inbox conversations
clerk inbox [--limit N] [--unread] [--account NAME]

# Show a conversation or message
clerk show <conv-id>

# Search messages (FTS5)
clerk search "query" [--limit N]

# Advanced search with operators
clerk search-advanced "from:alice has:attachment after:2025-01-01"

# Unread counts by folder
clerk unread

# List folders
clerk folders
```

## Search Operators

The `search-advanced` command supports:
- `from:alice`, `to:bob` - sender/recipient
- `subject:meeting`, `body:quarterly` - content search
- `has:attachment` - attachment filter
- `is:unread`, `is:read`, `is:flagged` - status filters
- `after:2025-01-01`, `before:2025-12-31`, `date:2025-06-15` - date filters

## Composing & Sending Email

**Draft workflow (recommended for safety):**
```bash
# Create a draft
clerk draft create --to "user@example.com" --subject "Subject" --body "Message"

# List drafts
clerk draft list

# Preview a draft
clerk draft show <draft-id>

# Send a draft (requires confirmation)
clerk send <draft-id>

# Delete a draft without sending
clerk draft delete <draft-id>
```

**Reply to a conversation:**
```bash
clerk draft create --reply-to <conv-id> --body "Reply message"
```

## Message Actions

```bash
# Move to folder
clerk move <msg-id> "Archive" --from INBOX

# Archive (shortcut)
clerk archive <msg-id>

# Flag/star
clerk flag <msg-id>

# Mark read/unread
clerk mark-read <msg-id>
clerk mark-unread <msg-id>
```

## Attachments

```bash
# List attachments
clerk attachment <msg-id> --list

# Download an attachment
clerk attachment <msg-id> "document.pdf" --save ./downloads/
```

## Safety Features

Clerk has 5 safety layers for sending:
1. Rate limiting (configurable)
2. Blocked recipients list
3. Mandatory confirmation (CLI prompt or two-step MCP)
4. FROM address verification
5. Audit logging

**Never skip confirmation when sending emails from an LLM agent.**

## Tips for LLM Agents

1. **Always use `--json` for programmatic access:**
   ```bash
   clerk inbox --json | jq '.[0].conv_id'
   ```

2. **Use conversation IDs, not message IDs** for most operations. Conversation IDs are 12-char SHA256 prefixes.

3. **Prefix matching works** - you can use shorter prefixes if unambiguous:
   ```bash
   clerk show abc123  # Works if "abc123" uniquely matches
   ```

4. **Cache-first architecture** - use `--fresh` to bypass cache when needed:
   ```bash
   clerk inbox --fresh
   ```

5. **Two-step send for MCP** - when using the MCP server, sending requires explicit confirmation with the draft ID.
'''


class SkillStatus(NamedTuple):
    """Status of skill installation."""

    global_installed: bool
    global_path: Path | None
    local_installed: bool
    local_path: Path | None


def _get_skill_path(local: bool) -> Path:
    """Get the skill installation path."""
    base = Path.cwd() if local else Path.home()
    return base / ".claude" / "skills" / "clerk"


def get_global_skill_path() -> Path:
    """Get the global skill installation path."""
    return _get_skill_path(local=False)


def get_local_skill_path() -> Path:
    """Get the local (project) skill installation path."""
    return _get_skill_path(local=True)


def install_skill(local: bool = False) -> Path:
    """Install the clerk skill.

    Args:
        local: If True, install to .claude/skills/clerk/ in current directory.
               If False, install to ~/.claude/skills/clerk/ (global).

    Returns:
        Path to the installed SKILL.md file.
    """
    skill_dir = _get_skill_path(local)
    skill_file = skill_dir / "SKILL.md"

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(SKILL_CONTENT)

    return skill_file


def uninstall_skill(local: bool = False) -> bool:
    """Uninstall the clerk skill.

    Args:
        local: If True, uninstall from .claude/skills/clerk/ in current directory.
               If False, uninstall from ~/.claude/skills/clerk/ (global).

    Returns:
        True if skill was uninstalled, False if it wasn't installed.
    """
    skill_dir = _get_skill_path(local)

    if not skill_dir.exists():
        return False

    shutil.rmtree(skill_dir)

    # Clean up empty parent directories
    skills_dir = skill_dir.parent
    if skills_dir.exists() and not any(skills_dir.iterdir()):
        skills_dir.rmdir()
        claude_dir = skills_dir.parent
        if claude_dir.name == ".claude" and not any(claude_dir.iterdir()):
            claude_dir.rmdir()

    return True


def get_skill_status() -> SkillStatus:
    """Get the current skill installation status."""
    global_path = get_global_skill_path()
    local_path = get_local_skill_path()

    global_installed = (global_path / "SKILL.md").exists()
    local_installed = (local_path / "SKILL.md").exists()

    return SkillStatus(
        global_installed=global_installed,
        global_path=global_path if global_installed else None,
        local_installed=local_installed,
        local_path=local_path if local_installed else None,
    )
