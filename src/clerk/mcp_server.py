"""MCP Server for clerk - Model Context Protocol integration for LLM agents."""

import hashlib
import secrets
import time
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .api import ClerkAPI, get_api
from .config import ensure_dirs
from .models import Address

# Create the MCP server
mcp = FastMCP(name="clerk")

# Store confirmation tokens with expiry (draft_id -> (token, expiry_timestamp))
_confirmation_tokens: dict[str, tuple[str, float]] = {}
CONFIRMATION_TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


def _generate_confirmation_token(draft_id: str) -> str:
    """Generate a confirmation token for a draft."""
    token = secrets.token_hex(16)
    expiry = time.time() + CONFIRMATION_TOKEN_EXPIRY_SECONDS
    _confirmation_tokens[draft_id] = (token, expiry)
    return token


def _validate_confirmation_token(draft_id: str, token: str) -> tuple[bool, str | None]:
    """Validate a confirmation token.

    Returns (valid, error_message).
    """
    if draft_id not in _confirmation_tokens:
        return False, "No confirmation token found. Call clerk_send with confirm=false first."

    stored_token, expiry = _confirmation_tokens[draft_id]

    if time.time() > expiry:
        del _confirmation_tokens[draft_id]
        return False, "Confirmation token expired. Call clerk_send with confirm=false to get a new one."

    if not secrets.compare_digest(token, stored_token):
        return False, "Invalid confirmation token."

    # Token is valid, remove it (one-time use)
    del _confirmation_tokens[draft_id]
    return True, None


def _cleanup_expired_tokens() -> None:
    """Remove expired confirmation tokens."""
    now = time.time()
    expired = [k for k, (_, expiry) in _confirmation_tokens.items() if now > expiry]
    for k in expired:
        del _confirmation_tokens[k]


# ============================================================================
# Tools
# ============================================================================


@mcp.tool()
def clerk_inbox(
    limit: int = 20,
    unread: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """List recent email conversations in inbox.

    Args:
        limit: Maximum number of conversations to return (default: 20)
        unread: If true, only show unread conversations
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with list of conversation summaries
    """
    ensure_dirs()
    api = get_api()

    result = api.list_inbox(account=account, limit=limit, unread_only=unread)

    return {
        "account": result.account,
        "conversations": [c.model_dump() for c in result.conversations],
        "count": result.count,
    }


@mcp.tool()
def clerk_show(conv_id: str) -> dict[str, Any]:
    """Get full conversation or message details.

    Supports prefix matching - if the prefix uniquely identifies a conversation,
    it will be returned. If multiple conversations match, summaries are returned
    for disambiguation.

    Args:
        conv_id: Conversation ID, unique prefix, or message ID

    Returns:
        Dictionary with one of:
        - type="conversation" with full conversation details
        - type="ambiguous" with list of matching conversation summaries
        - type="message" with message details
        - error if not found
    """
    ensure_dirs()
    api = get_api()

    # Try as conversation first (with prefix matching support)
    result = api.resolve_conversation_id(conv_id)

    if result.conversation:
        return {
            "type": "conversation",
            "conversation": result.conversation.model_dump(),
        }

    if result.matches:
        # Ambiguous prefix - return summaries for disambiguation
        return {
            "type": "ambiguous",
            "prefix": conv_id,
            "matches": [m.model_dump() for m in result.matches],
            "count": len(result.matches),
            "hint": "Use a longer prefix to uniquely identify the conversation.",
        }

    # Try as message ID
    msg = api.get_message(conv_id)
    if msg:
        return {
            "type": "message",
            "message": msg.model_dump(),
        }

    return {"error": f"Not found: {conv_id}"}


@mcp.tool()
def clerk_search(
    query: str,
    limit: int = 20,
    account: str | None = None,
    advanced: bool = False,
) -> dict[str, Any]:
    """Search messages in cache using full-text search.

    Supports operators like:
    - from:alice
    - subject:meeting
    - body:quarterly
    - has:attachment
    - is:unread, is:read, is:flagged
    - after:2025-01-01, before:2025-12-31

    Args:
        query: Search query string
        limit: Maximum number of results (default: 20)
        account: Account name (uses default if not specified)
        advanced: Use advanced search with operator support (default: false)

    Returns:
        Dictionary with matching messages
    """
    ensure_dirs()
    api = get_api()

    if advanced:
        result = api.search_advanced(query, account=account, limit=limit)
    else:
        result = api.search(query, account=account, limit=limit)

    return {
        "query": result.query,
        "results": [m.model_dump() for m in result.messages],
        "count": result.count,
    }


@mcp.tool()
def clerk_search_sql(
    sql: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Execute a raw SQL query on the messages table (power users).

    Only SELECT queries are allowed. Use this for complex queries that
    can't be expressed with the regular search operators.

    Args:
        sql: SQL SELECT query
        limit: Maximum results (default: 100)

    Returns:
        Dictionary with matching messages or error
    """
    ensure_dirs()
    api = get_api()

    try:
        messages = api.search_sql(sql, limit=limit)
        return {
            "results": [m.model_dump() for m in messages],
            "count": len(messages),
        }
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Create a new email draft.

    Args:
        to: Recipient email address (or comma-separated list)
        subject: Subject line
        body: Message body text
        cc: CC recipients (comma-separated, optional)
        reply_to: Conversation ID to reply to (optional)
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with draft_id of created draft
    """
    ensure_dirs()
    api = get_api()

    # Parse addresses
    to_addrs = [a.strip() for a in to.split(",")]
    cc_addrs = [a.strip() for a in cc.split(",")] if cc else None

    draft = api.create_draft(
        to=to_addrs,
        subject=subject,
        body=body,
        cc=cc_addrs,
        reply_to_conv_id=reply_to,
        account=account,
    )

    return {
        "draft_id": draft.draft_id,
        "account": draft.account,
        "to": [str(a) for a in draft.to],
        "subject": draft.subject,
        "created_at": draft.created_at.isoformat(),
    }


@mcp.tool()
def clerk_drafts(account: str | None = None) -> dict[str, Any]:
    """List all pending drafts.

    Args:
        account: Account name to filter by (optional)

    Returns:
        Dictionary with list of drafts
    """
    ensure_dirs()
    api = get_api()

    drafts = api.list_drafts(account=account)

    return {
        "drafts": [
            {
                "draft_id": d.draft_id,
                "account": d.account,
                "to": [str(a) for a in d.to],
                "subject": d.subject,
                "created_at": d.created_at.isoformat(),
            }
            for d in drafts
        ],
        "count": len(drafts),
    }


@mcp.tool()
def clerk_send(
    draft_id: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    """Send a draft message with two-step confirmation.

    Step 1: Call with confirm=false to get a preview and confirmation token.
    Step 2: Call with confirm=true and the token to actually send.

    Args:
        draft_id: ID of the draft to send
        confirm: If true, confirms and sends (requires token)
        token: Confirmation token (required when confirm=true)

    Returns:
        If confirm=false: Preview and confirmation token (valid for 5 minutes)
        If confirm=true: Send result with message_id or error
    """
    ensure_dirs()
    _cleanup_expired_tokens()

    api = get_api()
    draft = api.get_draft(draft_id)

    if not draft:
        return {"error": f"Draft not found: {draft_id}"}

    if not confirm:
        # Step 1: Generate preview and token
        from .smtp_client import check_send_allowed, format_draft_preview

        allowed, error = check_send_allowed(draft, draft.account)
        if not allowed:
            return {"error": error}

        confirmation_token = _generate_confirmation_token(draft_id)

        return {
            "status": "pending_confirmation",
            "preview": format_draft_preview(draft),
            "token": confirmation_token,
            "expires_in_seconds": CONFIRMATION_TOKEN_EXPIRY_SECONDS,
            "message": "Call clerk_send again with confirm=true and this token to send.",
        }

    # Step 2: Validate token and send
    if not token:
        return {"error": "Token required when confirm=true"}

    valid, error = _validate_confirmation_token(draft_id, token)
    if not valid:
        return {"error": error}

    # Send the draft
    result = api.send_draft(draft_id, skip_confirmation=True)

    if result.success:
        return {
            "status": "sent",
            "message_id": result.message_id,
            "timestamp": result.timestamp.isoformat(),
        }
    else:
        return {
            "status": "failed",
            "error": result.error,
        }


@mcp.tool()
def clerk_delete_draft(draft_id: str) -> dict[str, Any]:
    """Delete a draft without sending.

    Args:
        draft_id: ID of the draft to delete

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()

    if api.delete_draft(draft_id):
        # Also cleanup any pending confirmation token
        if draft_id in _confirmation_tokens:
            del _confirmation_tokens[draft_id]

        return {"status": "deleted", "draft_id": draft_id}
    else:
        return {"error": f"Draft not found: {draft_id}"}


@mcp.tool()
def clerk_mark_read(message_id: str, account: str | None = None) -> dict[str, Any]:
    """Mark a message as read.

    Args:
        message_id: ID of the message to mark as read
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()

    try:
        api.mark_read(message_id, account=account)
        return {"status": "success", "message_id": message_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_archive(message_id: str, account: str | None = None) -> dict[str, Any]:
    """Archive a message.

    Args:
        message_id: ID of the message to archive
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()

    try:
        api.archive_message(message_id, account=account)
        return {"status": "success", "message_id": message_id, "folder": "Archive"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_attachments(message_id: str) -> dict[str, Any]:
    """List attachments for a message.

    Args:
        message_id: ID of the message

    Returns:
        Dictionary with list of attachments
    """
    ensure_dirs()
    api = get_api()

    attachments = api.list_attachments(message_id)

    if not attachments:
        msg = api.get_message(message_id)
        if not msg:
            return {"error": f"Message not found: {message_id}"}

    return {
        "message_id": message_id,
        "attachments": attachments,
        "count": len(attachments),
    }


@mcp.tool()
def clerk_status() -> dict[str, Any]:
    """Get clerk status and connection info.

    Returns:
        Dictionary with version and account connection status
    """
    ensure_dirs()
    api = get_api()

    return api.get_status()


# ============================================================================
# Resources
# ============================================================================


@mcp.resource("clerk://inbox")
def resource_inbox() -> str:
    """Current inbox state as JSON."""
    result = clerk_inbox()
    import json

    return json.dumps(result, default=str, indent=2)


@mcp.resource("clerk://conversation/{conv_id}")
def resource_conversation(conv_id: str) -> str:
    """Specific conversation thread as JSON."""
    result = clerk_show(conv_id)
    import json

    return json.dumps(result, default=str, indent=2)


@mcp.resource("clerk://draft/{draft_id}")
def resource_draft(draft_id: str) -> str:
    """Pending draft content as JSON."""
    ensure_dirs()
    api = get_api()

    draft = api.get_draft(draft_id)
    if not draft:
        import json

        return json.dumps({"error": f"Draft not found: {draft_id}"})

    import json

    return json.dumps(draft.model_dump(), default=str, indent=2)


# ============================================================================
# Server Entry Point
# ============================================================================


def run_server() -> None:
    """Run the MCP server."""
    mcp.run()
