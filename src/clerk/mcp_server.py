"""MCP Server for clerk — 10 tools + 3 resources for LLM email agents."""

import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .api import get_api
from .cache import SCHEMA
from .config import ensure_dirs, get_config
from .smtp_client import check_send_allowed, format_draft_preview, send_draft_async

# Store pending M365 device code flows: account -> (app, flow, expires_at)
_pending_device_flows: dict[str, tuple[Any, Any, float]] = {}

mcp = FastMCP(name="clerk")

# Confirmation token storage
_confirmation_tokens: dict[str, tuple[str, float]] = {}
CONFIRMATION_TOKEN_EXPIRY_SECONDS = 300


def _generate_confirmation_token(draft_id: str) -> str:
    token = secrets.token_hex(16)
    _confirmation_tokens[draft_id] = (token, time.time() + CONFIRMATION_TOKEN_EXPIRY_SECONDS)
    return token


def _validate_confirmation_token(draft_id: str, token: str) -> tuple[bool, str | None]:
    if draft_id not in _confirmation_tokens:
        return False, "No confirmation token found. Call clerk_send without token first."
    stored_token, expiry = _confirmation_tokens[draft_id]
    if time.time() > expiry:
        del _confirmation_tokens[draft_id]
        return False, "Confirmation token expired. Call clerk_send again to get a new one."
    if not secrets.compare_digest(token, stored_token):
        return False, "Invalid confirmation token."
    del _confirmation_tokens[draft_id]
    return True, None


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [k for k, (_, exp) in _confirmation_tokens.items() if now > exp]
    for k in expired:
        del _confirmation_tokens[k]


# ============================================================================
# Tools (10)
# ============================================================================


@mcp.tool()
def clerk_read(
    message_id: str,
) -> dict[str, Any]:
    """Read a full email message, fetching body from IMAP if not cached.

    Use clerk_sql to find message_ids, then clerk_read to get the full
    body text for messages you want to read.

    Args:
        message_id: Message ID to read

    Returns:
        Dictionary with message fields including body_text, or error
    """
    ensure_dirs()
    api = get_api()

    try:
        msg = api.get_message(message_id)
        if not msg:
            return {"error": f"Message not found: {message_id}. Try clerk_sync first."}

        return {
            "message_id": msg.message_id,
            "from": str(msg.from_),
            "to": [str(a) for a in msg.to],
            "cc": [str(a) for a in msg.cc],
            "subject": msg.subject,
            "date": msg.date.isoformat(),
            "body_text": msg.body_text,
            "flags": [f.value for f in msg.flags],
            "attachments": [
                {"filename": a.filename, "size": a.size, "content_type": a.content_type}
                for a in msg.attachments
            ],
            "in_reply_to": msg.in_reply_to,
            "conv_id": msg.conv_id,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_sql(
    query: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Execute a readonly SQL SELECT query on the clerk email cache.

    Use the clerk://schema resource to discover tables and columns.
    Returns raw rows as JSON dicts.

    Args:
        query: SQL SELECT query (only SELECT is allowed)
        limit: Maximum results (default: 100)

    Returns:
        Dictionary with rows (list of dicts) and count, or error
    """
    ensure_dirs()
    api = get_api()
    try:
        rows = api.cache.execute_readonly_sql(query, limit=limit)
        return {"rows": rows, "count": len(rows)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"SQL error: {e}"}


@mcp.tool()
def clerk_sync(
    account: str | None = None,
    folder: str = "INBOX",
    full: bool = False,
) -> dict[str, Any]:
    """Sync email cache from IMAP server.

    When called with no account, syncs all configured accounts.
    By default, only fetches new messages since last sync (incremental).

    Args:
        account: Account name (syncs all accounts if not specified)
        folder: Folder to sync (default: INBOX)
        full: Re-fetch all messages instead of incremental sync

    Returns:
        Per-account sync results with counts
    """
    ensure_dirs()
    api = get_api()

    if account is not None:
        # Single account mode
        try:
            return api.sync_folder(account=account, folder=folder, full=full)
        except Exception as e:
            return {"error": str(e)}

    # Sync all accounts
    config = get_config()
    results: dict[str, Any] = {"accounts": {}, "total_synced": 0}

    for acct_name in config.accounts:
        try:
            result = api.sync_folder(account=acct_name, folder=folder, full=full)
            results["accounts"][acct_name] = result
            results["total_synced"] += result["synced"]
        except Exception as e:
            results["accounts"][acct_name] = {"error": str(e)}

    return results


@mcp.tool()
def clerk_reply(
    message_id: str,
    body: str,
    reply_all: bool = False,
    account: str | None = None,
) -> dict[str, Any]:
    """Reply to an email message.

    Creates a reply draft with auto-populated To, Cc, Subject, In-Reply-To,
    and References headers. Call clerk_send with the returned draft_id to
    preview and send.

    Args:
        message_id: Message ID to reply to
        body: Reply body text
        reply_all: Include all original recipients in reply
        account: Account to send from (uses default if not specified)

    Returns:
        Dictionary with draft_id, to, cc, subject for user confirmation,
        or error if message not found
    """
    ensure_dirs()
    api = get_api()

    try:
        draft = api.create_reply(
            message_id=message_id,
            body=body,
            reply_all=reply_all,
            account=account,
        )

        return {
            "draft_id": draft.draft_id,
            "to": [str(a) for a in draft.to],
            "cc": [str(a) for a in draft.cc],
            "subject": draft.subject,
            "message": "Draft created. Call clerk_send to preview and send.",
        }
    except ValueError as e:
        return {"error": f"{e}. Try running clerk_sync first."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_draft(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Compose a new email (not a reply).

    Creates a draft and returns metadata for user confirmation.
    If the user approves, call clerk_send with the draft_id to send.

    Args:
        to: Recipient email addresses
        subject: Subject line
        body: Message body text
        cc: CC recipients (optional)
        account: Account to send from (uses default if not specified)

    Returns:
        Dictionary with draft_id and metadata for user confirmation
    """
    ensure_dirs()
    api = get_api()

    try:
        draft = api.create_draft(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            account=account,
        )

        return {
            "draft_id": draft.draft_id,
            "to": [str(a) for a in draft.to],
            "cc": [str(a) for a in draft.cc],
            "subject": draft.subject,
            "message": "Draft created. Call clerk_send to preview and send.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def clerk_send(
    draft_id: str,
    token: str | None = None,
) -> dict[str, Any]:
    """Send a draft email with two-step confirmation.

    Step 1: Call without token — returns preview and a confirmation token.
    Step 2: Call with the token — actually sends the email.

    Args:
        draft_id: ID of the draft to send
        token: Confirmation token from step 1 (required for step 2)

    Returns:
        Step 1: Preview + token (valid 5 minutes)
        Step 2: Send result with message_id
    """
    ensure_dirs()
    _cleanup_expired_tokens()
    api = get_api()

    draft = api.get_draft(draft_id)
    if not draft:
        return {"error": f"Draft not found: {draft_id}"}

    if token is None:
        # Step 1: generate preview and token
        allowed, error = check_send_allowed(draft, draft.account)
        if not allowed:
            return {"error": error}

        confirmation_token = _generate_confirmation_token(draft_id)

        return {
            "status": "pending_confirmation",
            "preview": format_draft_preview(draft),
            "token": confirmation_token,
            "expires_in_seconds": CONFIRMATION_TOKEN_EXPIRY_SECONDS,
            "message": "Call clerk_send again with this token to send.",
        }

    # Step 2: validate and send
    valid, error = _validate_confirmation_token(draft_id, token)
    if not valid:
        return {"error": error}

    result = await send_draft_async(draft_id)

    if result.success:
        return {
            "status": "sent",
            "message_id": result.message_id,
            "timestamp": result.timestamp.isoformat(),
        }
    return {"status": "failed", "error": result.error}


@mcp.tool()
def clerk_move(
    message_id: str,
    to_folder: str,
    from_folder: str = "INBOX",
    account: str | None = None,
) -> dict[str, Any]:
    """Move an email message to another folder.

    Use clerk://folders resource to see available folders.

    Args:
        message_id: Message ID to move
        to_folder: Destination folder (e.g., "Archive", "Trash")
        from_folder: Source folder (default: INBOX)
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()
    try:
        api.move_message(message_id, to_folder, from_folder=from_folder, account=account)
        return {"status": "success", "message_id": message_id, "folder": to_folder}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_flag(
    message_id: str,
    action: Literal["flag", "unflag", "read", "unread"],
    account: str | None = None,
) -> dict[str, Any]:
    """Flag/unflag or mark read/unread on an email message.

    Args:
        message_id: Message ID
        action: One of "flag", "unflag", "read", "unread"
        account: Account name (uses default if not specified)

    Returns:
        Dictionary with success status
    """
    ensure_dirs()
    api = get_api()
    try:
        if action == "flag":
            api.flag_message(message_id, account=account)
        elif action == "unflag":
            api.unflag_message(message_id, account=account)
        elif action == "read":
            api.mark_read(message_id, account=account)
        elif action == "unread":
            api.mark_unread(message_id, account=account)
        else:
            return {"error": f"Invalid action: {action}. Use flag, unflag, read, or unread."}
        return {"status": "success", "message_id": message_id, "action": action}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clerk_status() -> dict[str, Any]:
    """Get clerk status — version, accounts, connection health.

    Returns:
        Dictionary with version and per-account connection status
    """
    ensure_dirs()
    api = get_api()
    return api.get_status()


@mcp.tool()
async def clerk_auth(
    account: str,
    confirm: bool = False,
    password: str | None = None,
) -> dict[str, Any]:
    """Re-authenticate an account when credentials expire.

    Microsoft 365 (two-step device code flow):
      Step 1: Call without confirm. Returns URL and user code.
              Show these to the user so they can authenticate in a browser.
      Step 2: Call with confirm=True. Polls until auth completes.

    Gmail: Attempts silent token refresh. If that fails, returns
    instructions for the user to run 'clerk accounts auth' in terminal
    (Gmail OAuth requires a browser callback).

    IMAP: Pass the new password to update credentials and test connection.
    Note: the password will be visible in conversation history.

    Args:
        account: Account name to re-authenticate
        confirm: Set True after user has completed browser auth (M365 only)
        password: New password for IMAP accounts (ask the user for it)

    Returns:
        Auth instructions or success/failure status
    """
    ensure_dirs()
    config = get_config()

    if account not in config.accounts:
        return {"error": f"Account '{account}' not found. Available: {list(config.accounts)}"}

    acct_config = config.accounts[account]
    protocol = acct_config.protocol

    if protocol == "microsoft365":
        return await _auth_m365(account, confirm)
    elif protocol == "gmail":
        return _auth_gmail(account, acct_config)
    else:
        return _auth_imap(account, acct_config, password)


async def _auth_m365(account: str, confirm: bool) -> dict[str, Any]:
    """Handle M365 device code auth flow."""
    import asyncio

    from .microsoft365 import M365_SCOPES, _build_app, save_m365_token_cache

    # Clean up expired flows
    now = time.time()
    expired = [k for k, (_, _, exp) in _pending_device_flows.items() if now > exp]
    for k in expired:
        del _pending_device_flows[k]

    if not confirm:
        # Step 1: Initiate device code flow
        app = _build_app(account)
        flow = app.initiate_device_flow(scopes=M365_SCOPES)

        if "error" in flow:
            return {"error": f"Failed to initiate auth: {flow.get('error_description', flow['error'])}"}

        # Store for step 2 (device codes expire in ~15 min)
        expires_at = now + flow.get("expires_in", 900)
        _pending_device_flows[account] = (app, flow, expires_at)

        return {
            "status": "awaiting_user",
            "protocol": "microsoft365",
            "url": flow.get("verification_uri", "https://microsoft.com/devicelogin"),
            "user_code": flow.get("user_code", ""),
            "message": flow.get("message", ""),
            "instruction": "Show the URL and code to the user. Once they authenticate, call clerk_auth again with confirm=True.",
        }

    # Step 2: Complete the flow (polls until user finishes)
    if account not in _pending_device_flows:
        return {"error": "No pending auth flow. Call clerk_auth without confirm=True first."}

    app, flow, _ = _pending_device_flows.pop(account)

    # Run the blocking MSAL poll in a thread to avoid freezing the event loop
    result = await asyncio.to_thread(app.acquire_token_by_device_flow, flow)

    if "error" in result:
        return {
            "status": "failed",
            "error": f"Authentication failed: {result.get('error_description', result['error'])}",
        }

    save_m365_token_cache(account, app.token_cache.serialize())

    return {
        "status": "success",
        "protocol": "microsoft365",
        "message": f"Account '{account}' re-authenticated successfully.",
    }


def _auth_gmail(account: str, acct_config: Any) -> dict[str, Any]:
    """Handle Gmail auth — try silent refresh first."""
    try:
        from google.auth.transport.requests import Request

        from .oauth import _load_credentials, _save_credentials, get_oauth_token
    except ImportError:
        return {"error": "Google auth libraries not installed. Run: pip install google-auth google-auth-oauthlib"}

    token_json = get_oauth_token(account)
    if not token_json:
        return {
            "status": "manual_required",
            "protocol": "gmail",
            "message": (
                f"No stored credentials for Gmail account '{account}'. "
                "Ask the user to run: clerk accounts auth " + account
            ),
        }

    credentials = _load_credentials(token_json)

    if credentials.valid:
        return {
            "status": "success",
            "protocol": "gmail",
            "message": f"Gmail account '{account}' credentials are still valid.",
        }

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _save_credentials(account, credentials)
            return {
                "status": "success",
                "protocol": "gmail",
                "message": f"Gmail account '{account}' token refreshed successfully.",
            }
        except Exception as e:
            return {
                "status": "manual_required",
                "protocol": "gmail",
                "message": (
                    f"Token refresh failed: {e}. "
                    "Ask the user to run: clerk accounts auth " + account
                ),
            }

    return {
        "status": "manual_required",
        "protocol": "gmail",
        "message": (
            f"Gmail account '{account}' has no refresh token. "
            "Ask the user to run: clerk accounts auth " + account
        ),
    }


def _auth_imap(account: str, acct_config: Any, password: str | None) -> dict[str, Any]:
    """Handle IMAP password auth — update password and test connection."""
    from .config import save_password
    from .imap_client import ImapClient

    if password is None:
        return {
            "status": "needs_password",
            "protocol": "imap",
            "message": (
                f"IMAP account '{account}' needs a password. "
                "Ask the user for their password, then call clerk_auth again with the password parameter."
            ),
        }

    # Save the new password
    save_password(account, password)

    # Test the connection
    try:
        client = ImapClient(account, acct_config)
        client.connect()
        folder_count = len(client.list_folders())
        client.disconnect()
        return {
            "status": "success",
            "protocol": "imap",
            "message": f"Password updated and connection verified ({folder_count} folders).",
        }
    except Exception as e:
        return {
            "status": "failed",
            "protocol": "imap",
            "error": f"Password saved but connection failed: {e}",
            "message": "The password was saved to keyring but the connection test failed. Check the credentials.",
        }


# ============================================================================
# Resources (3)
# ============================================================================

EXAMPLE_QUERIES = """
## Example Queries

```sql
-- Inbox: recent conversations
SELECT conv_id, from_addr, from_name, subject, date_utc, flags
FROM messages WHERE folder='INBOX' AND account='siue'
ORDER BY date_utc DESC LIMIT 20

-- Thread history (for context before replying)
SELECT message_id, from_addr, from_name, subject, date_utc, body_text
FROM messages WHERE conv_id = 'abc123def456'
ORDER BY date_utc ASC

-- Unread counts by folder
SELECT folder, COUNT(*) as unread
FROM messages WHERE flags NOT LIKE '%"seen"%'
GROUP BY folder

-- Full-text search
SELECT m.message_id, m.from_addr, m.subject, m.date_utc
FROM messages_fts f
JOIN messages m ON m.rowid = f.rowid
WHERE messages_fts MATCH 'quarterly report'
ORDER BY m.date_utc DESC LIMIT 20

-- Priority senders (combine with clerk://config priorities)
SELECT message_id, from_addr, subject, date_utc
FROM messages
WHERE from_addr LIKE '%@siue.edu%' AND flags NOT LIKE '%"seen"%'
ORDER BY date_utc DESC

-- Attachments for a message
SELECT attachments_json FROM messages WHERE message_id = '<msg-id>'

-- Pending drafts
SELECT * FROM drafts ORDER BY updated_at DESC

-- Send audit log
SELECT * FROM send_log ORDER BY timestamp DESC LIMIT 10
```
"""


@mcp.resource("clerk://schema")
def resource_schema() -> str:
    """Database schema and example queries for clerk_sql."""
    return SCHEMA + "\n" + EXAMPLE_QUERIES


@mcp.resource("clerk://config")
def resource_config() -> str:
    """Clerk configuration: accounts, priorities, settings (sensitive fields redacted)."""
    config = get_config()
    data: dict[str, Any] = {
        "default_account": config.default_account,
        "accounts": {},
        "priorities": {
            "senders": config.priorities.senders,
            "topics": config.priorities.topics,
        },
        "cache": {
            "window_days": config.cache.window_days,
            "inbox_freshness_min": config.cache.inbox_freshness_min,
        },
    }
    for name, acct in config.accounts.items():
        data["accounts"][name] = {
            "protocol": acct.protocol,
            "from": acct.from_.address,
        }
    return json.dumps(data, indent=2)


_FOLDER_CACHE_TTL_SECONDS = 3600  # 1 hour


@mcp.resource("clerk://folders")
def resource_folders() -> str:
    """Available email folders per account (cached 1 hour)."""
    ensure_dirs()
    api = get_api()
    config = get_config()
    result: dict[str, list[str]] = {}

    for name in config.accounts:
        cache_key = f"folders_{name}"
        cached_json = api.cache.get_meta(cache_key)
        cached_at_str = api.cache.get_meta(f"{cache_key}_at")

        if cached_json and cached_at_str:
            cached_at = datetime.fromisoformat(cached_at_str)
            age = (datetime.now(UTC) - cached_at).total_seconds()
            if age < _FOLDER_CACHE_TTL_SECONDS:
                result[name] = json.loads(cached_json)
                continue

        try:
            folders = api.list_folders(account=name)
            folder_names = [f.name for f in folders]
            result[name] = folder_names
            api.cache.set_meta(cache_key, json.dumps(folder_names))
            api.cache.set_meta(f"{cache_key}_at", datetime.now(UTC).isoformat())
        except Exception as e:
            result[name] = [f"Error: {e}"]

    return json.dumps(result, indent=2)


# ============================================================================
# Server Entry Point
# ============================================================================


def run_server() -> None:
    """Run the MCP server."""
    mcp.run()
