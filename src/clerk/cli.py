"""Clerk CLI - A thin CLI for LLM agents to interact with email."""

import json
import sys
from datetime import datetime
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .cache import get_cache
from .config import (
    AccountConfig,
    ClerkConfig,
    FromAddress,
    ImapConfig,
    OAuthConfig,
    SmtpConfig,
    delete_oauth_token,
    delete_password,
    ensure_dirs,
    get_config,
    load_config,
    save_config,
    save_password,
)
from .drafts import get_draft_manager
from .imap_client import get_imap_client, ImapClient
from .models import Address, ExitCode, MessageFlag
from .smtp_client import check_send_allowed, format_draft_preview, send_draft, SmtpClient

app = typer.Typer(
    name="clerk",
    help="A thin CLI for LLM agents to interact with email.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


def output_json(data: dict | list) -> None:
    """Output data as JSON."""
    print(json.dumps(data, default=str, indent=2))


def exit_with_code(code: ExitCode, message: str | None = None) -> None:
    """Exit with a specific exit code and optional message."""
    if message:
        err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code.value)


# ============================================================================
# Inbox & Fetch Commands
# ============================================================================


@app.command()
def inbox(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of conversations")] = 20,
    unread: Annotated[bool, typer.Option("--unread", "-u", help="Only show unread")] = False,
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    folder: Annotated[str, typer.Option("--folder", "-f", help="Folder to list")] = "INBOX",
    fresh: Annotated[bool, typer.Option("--fresh", help="Bypass cache, fetch from server")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List recent conversations in inbox."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()

    account_name, account_config = config.get_account(account)

    # Check cache freshness
    if not fresh and cache.is_inbox_fresh(account_name, config.cache.inbox_freshness_min):
        # Serve from cache
        conversations = cache.list_conversations(
            account=account_name,
            folder=folder,
            unread_only=unread,
            limit=limit,
        )
    else:
        # Fetch from server
        with get_imap_client(account_name) as client:
            messages = client.fetch_messages(
                folder=folder,
                limit=limit * 3,  # Fetch more to account for threading
                unread_only=unread,
                fetch_bodies=False,  # Headers only for listing
            )

            # Store in cache
            for msg in messages:
                cache.store_message(msg)

            cache.mark_inbox_synced(account_name)

        # Prune old messages
        cache.prune_old_messages(config.cache.window_days)

        # Get conversations from cache
        conversations = cache.list_conversations(
            account=account_name,
            folder=folder,
            unread_only=unread,
            limit=limit,
        )

    if as_json:
        output_json([c.model_dump() for c in conversations])
        return

    # Human-readable output
    if not conversations:
        console.print("[dim]No conversations found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=12)
    table.add_column("From", width=25)
    table.add_column("Subject", width=40)
    table.add_column("Date", width=12)
    table.add_column("Msgs", justify="right", width=4)

    for conv in conversations:
        # Get primary participant (not self)
        participants = [p for p in conv.participants if p != account_config.from_.address]
        from_str = participants[0] if participants else conv.participants[0] if conv.participants else ""

        # Format date
        date_str = conv.latest_date.strftime("%b %d")

        # Unread indicator
        subject = conv.subject
        if conv.unread_count > 0:
            subject = f"[bold]{subject}[/bold]"

        table.add_row(
            conv.conv_id,
            from_str[:25],
            subject[:40],
            date_str,
            str(conv.message_count),
        )

    console.print(table)


@app.command()
def show(
    conv_or_msg_id: Annotated[str, typer.Argument(help="Conversation or message ID")],
    fresh: Annotated[bool, typer.Option("--fresh", help="Bypass cache, fetch from server")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show a conversation or message."""
    ensure_dirs()
    from .api import get_api

    api = get_api()
    cache = get_cache()
    config = get_config()

    # Try as conversation first (with prefix matching support)
    result = api.resolve_conversation_id(conv_or_msg_id, fresh=fresh)

    if result.conversation:
        conv = result.conversation
        if as_json:
            output_json(conv.model_dump())
            return

        # Human-readable output
        console.print(f"[bold]Subject:[/bold] {conv.subject}")
        console.print(f"[bold]Participants:[/bold] {', '.join(conv.participants)}")
        console.print(f"[bold]Messages:[/bold] {conv.message_count}")
        console.print()

        for i, msg in enumerate(conv.messages, 1):
            console.print(f"[bold cyan]--- Message {i} ---[/bold cyan]")
            console.print(f"[bold]From:[/bold] {msg.from_}")
            console.print(f"[bold]Date:[/bold] {msg.date}")
            console.print()
            console.print(msg.body_text or "[dim](no body)[/dim]")
            console.print()

        return

    if result.matches:
        # Ambiguous prefix - show summaries for disambiguation
        if as_json:
            output_json({
                "error": "ambiguous_prefix",
                "prefix": conv_or_msg_id,
                "matches": [m.model_dump() for m in result.matches],
            })
            raise typer.Exit(ExitCode.INVALID_INPUT.value)

        console.print(f"[yellow]Multiple conversations match '{conv_or_msg_id}':[/yellow]")
        console.print()

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="dim", width=12)
        table.add_column("From", width=25)
        table.add_column("Subject", width=40)
        table.add_column("Date", width=12)

        for m in result.matches:
            from_str = m.participants[0] if m.participants else ""
            table.add_row(
                m.conv_id,
                from_str[:25],
                m.subject[:40],
                m.latest_date.strftime("%b %d"),
            )

        console.print(table)
        console.print()
        console.print("[dim]Use a longer prefix to uniquely identify the conversation.[/dim]")
        raise typer.Exit(ExitCode.INVALID_INPUT.value)

    # Try as message ID
    msg = cache.get_message(conv_or_msg_id)
    if msg:
        if msg.body_text is None:
            if fresh or not cache.is_fresh(msg.message_id, config.cache.body_freshness_min, check_body=True):
                with get_imap_client(msg.account) as client:
                    body_text, body_html = client.fetch_message_body(msg.folder, msg.message_id)
                    cache.update_body(msg.message_id, body_text, body_html)
                    msg.body_text = body_text
                    msg.body_html = body_html

        if as_json:
            output_json(msg.model_dump())
            return

        console.print(f"[bold]From:[/bold] {msg.from_}")
        console.print(f"[bold]To:[/bold] {', '.join(str(a) for a in msg.to)}")
        console.print(f"[bold]Date:[/bold] {msg.date}")
        console.print(f"[bold]Subject:[/bold] {msg.subject}")
        console.print()
        console.print(msg.body_text or "[dim](no body)[/dim]")
        return

    exit_with_code(ExitCode.NOT_FOUND, f"Not found: {conv_or_msg_id}")


@app.command(name="unread")
def unread_cmd(
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show unread message counts by folder."""
    ensure_dirs()
    config = get_config()
    account_name, _ = config.get_account(account)

    with get_imap_client(account_name) as client:
        counts = client.get_unread_counts()

    if as_json:
        output_json(counts.model_dump())
        return

    if counts.total == 0:
        console.print("[green]No unread messages.[/green]")
        return

    console.print(f"[bold]Total unread:[/bold] {counts.total}")
    for folder, count in sorted(counts.folders.items()):
        console.print(f"  {folder}: {count}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 20,
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Search messages in cache.

    Uses FTS5 for full-text search. Supports operators like:
    - from:alice
    - subject:meeting
    - body:quarterly (searches cached bodies)
    """
    ensure_dirs()
    cache = get_cache()

    messages = cache.search(query, account=account, limit=limit)

    if as_json:
        output_json([m.model_dump() for m in messages])
        return

    if not messages:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=12)
    table.add_column("From", width=25)
    table.add_column("Subject", width=40)
    table.add_column("Date", width=12)

    for msg in messages:
        table.add_row(
            msg.conv_id,
            msg.from_.addr[:25],
            msg.subject[:40],
            msg.date.strftime("%b %d"),
        )

    console.print(table)


# ============================================================================
# Compose & Send Commands
# ============================================================================

draft_app = typer.Typer(help="Draft management commands")
app.add_typer(draft_app, name="draft")


@draft_app.command(name="create")
def draft_create(
    to: Annotated[str, typer.Option("--to", "-t", help="Recipient email address")],
    subject: Annotated[str, typer.Option("--subject", "-s", help="Subject line")],
    body: Annotated[str, typer.Option("--body", "-b", help="Message body")],
    cc: Annotated[Optional[str], typer.Option("--cc", help="CC recipients (comma-separated)")] = None,
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    reply_to: Annotated[Optional[str], typer.Option("--reply-to", help="Conversation ID to reply to")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Create a new draft message."""
    ensure_dirs()
    config = get_config()
    manager = get_draft_manager()

    account_name, _ = config.get_account(account)

    # Parse addresses
    to_addrs = [Address(addr=a.strip(), name="") for a in to.split(",")]
    cc_addrs = [Address(addr=a.strip(), name="") for a in cc.split(",")] if cc else []

    if reply_to:
        # Create a reply
        draft = manager.create_reply(
            account=account_name,
            conv_id=reply_to,
            body_text=body,
        )
    else:
        draft = manager.create(
            account=account_name,
            to=to_addrs,
            cc=cc_addrs,
            subject=subject,
            body_text=body,
        )

    if as_json:
        output_json({"draft_id": draft.draft_id})
        return

    console.print(f"[green]Draft created:[/green] {draft.draft_id}")


@draft_app.command(name="list")
def draft_list(
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List pending drafts."""
    ensure_dirs()
    manager = get_draft_manager()

    drafts = manager.list(account=account)

    if as_json:
        output_json([d.model_dump() for d in drafts])
        return

    if not drafts:
        console.print("[dim]No drafts.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=20)
    table.add_column("To", width=30)
    table.add_column("Subject", width=35)
    table.add_column("Created", width=12)

    for draft in drafts:
        to_str = ", ".join(a.addr for a in draft.to)
        table.add_row(
            draft.draft_id,
            to_str[:30],
            draft.subject[:35],
            draft.created_at.strftime("%b %d %H:%M"),
        )

    console.print(table)


@draft_app.command(name="show")
def draft_show(
    draft_id: Annotated[str, typer.Argument(help="Draft ID")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show a draft."""
    ensure_dirs()
    manager = get_draft_manager()

    draft = manager.get(draft_id)
    if not draft:
        exit_with_code(ExitCode.NOT_FOUND, f"Draft not found: {draft_id}")

    if as_json:
        output_json(draft.model_dump())
        return

    console.print(format_draft_preview(draft))


@draft_app.command(name="delete")
def draft_delete(
    draft_id: Annotated[str, typer.Argument(help="Draft ID")],
) -> None:
    """Delete a draft without sending."""
    ensure_dirs()
    manager = get_draft_manager()

    if manager.delete(draft_id):
        console.print(f"[green]Deleted draft:[/green] {draft_id}")
    else:
        exit_with_code(ExitCode.NOT_FOUND, f"Draft not found: {draft_id}")


@app.command()
def send(
    draft_id: Annotated[str, typer.Argument(help="Draft ID to send")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Send a draft message."""
    ensure_dirs()
    config = get_config()
    manager = get_draft_manager()

    draft = manager.get(draft_id)
    if not draft:
        exit_with_code(ExitCode.NOT_FOUND, f"Draft not found: {draft_id}")

    # Check if sending is allowed
    allowed, error = check_send_allowed(draft, draft.account)
    if not allowed:
        exit_with_code(ExitCode.SEND_BLOCKED, error)

    # Show preview and confirm
    if not yes and config.send.require_confirmation:
        console.print("[bold]Preview:[/bold]")
        console.print(format_draft_preview(draft))
        console.print()

        if not typer.confirm("Send this message?"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    # Send it
    result = send_draft(draft_id)

    if as_json:
        output_json(result.model_dump())
        if not result.success:
            raise typer.Exit(ExitCode.CONNECTION_ERROR.value)
        return

    if result.success:
        console.print(f"[green]Sent![/green] Message-ID: {result.message_id}")
    else:
        exit_with_code(ExitCode.CONNECTION_ERROR, result.error)


# ============================================================================
# Folder Operations
# ============================================================================


@app.command()
def folders(
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List folders/labels."""
    ensure_dirs()
    config = get_config()
    account_name, _ = config.get_account(account)

    with get_imap_client(account_name) as client:
        folder_list = client.list_folders()

    if as_json:
        output_json([f.model_dump() for f in folder_list])
        return

    for folder in folder_list:
        flags = " ".join(f"[dim]{f}[/dim]" for f in folder.flags)
        console.print(f"{folder.name} {flags}")


@app.command()
def move(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    to_folder: Annotated[str, typer.Argument(help="Destination folder")],
    from_folder: Annotated[str, typer.Option("--from", help="Source folder")] = "INBOX",
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Move a message to another folder."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()
    account_name, _ = config.get_account(account)

    with get_imap_client(account_name) as client:
        client.move_message(message_id, from_folder, to_folder)

    # Update cache
    cache.move_message(message_id, to_folder)
    console.print(f"[green]Moved to {to_folder}[/green]")


@app.command()
def archive(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Archive a message."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()
    account_name, _ = config.get_account(account)

    with get_imap_client(account_name) as client:
        client.archive_message(message_id)

    # Update cache
    cache.move_message(message_id, "Archive")
    console.print("[green]Archived.[/green]")


@app.command()
def flag(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Flag/star a message."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()
    account_name, _ = config.get_account(account)

    msg = cache.get_message(message_id)
    folder = msg.folder if msg else "INBOX"

    with get_imap_client(account_name) as client:
        client.add_flags(folder, message_id, [MessageFlag.FLAGGED])

    # Update cache
    if msg:
        flags = list(msg.flags)
        if MessageFlag.FLAGGED not in flags:
            flags.append(MessageFlag.FLAGGED)
        cache.update_flags(message_id, flags)

    console.print("[green]Flagged.[/green]")


@app.command(name="mark-read")
def mark_read(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Mark a message as read."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()
    account_name, _ = config.get_account(account)

    msg = cache.get_message(message_id)
    folder = msg.folder if msg else "INBOX"

    with get_imap_client(account_name) as client:
        client.add_flags(folder, message_id, [MessageFlag.SEEN])

    # Update cache
    if msg:
        flags = list(msg.flags)
        if MessageFlag.SEEN not in flags:
            flags.append(MessageFlag.SEEN)
        cache.update_flags(message_id, flags)

    console.print("[green]Marked as read.[/green]")


@app.command(name="mark-unread")
def mark_unread(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Mark a message as unread."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()
    account_name, _ = config.get_account(account)

    msg = cache.get_message(message_id)
    folder = msg.folder if msg else "INBOX"

    with get_imap_client(account_name) as client:
        client.remove_flags(folder, message_id, [MessageFlag.SEEN])

    # Update cache
    if msg:
        flags = [f for f in msg.flags if f != MessageFlag.SEEN]
        cache.update_flags(message_id, flags)

    console.print("[green]Marked as unread.[/green]")


# ============================================================================
# Cache Management
# ============================================================================

cache_app = typer.Typer(help="Cache management commands")
app.add_typer(cache_app, name="cache")


@cache_app.command(name="status")
def cache_status(
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show cache statistics."""
    ensure_dirs()
    cache = get_cache()
    stats = cache.get_stats()

    if as_json:
        output_json(stats.model_dump())
        return

    console.print(f"[bold]Messages:[/bold] {stats.message_count}")
    console.print(f"[bold]Conversations:[/bold] {stats.conversation_count}")

    if stats.oldest_message:
        console.print(f"[bold]Oldest:[/bold] {stats.oldest_message.strftime('%Y-%m-%d')}")
    if stats.newest_message:
        console.print(f"[bold]Newest:[/bold] {stats.newest_message.strftime('%Y-%m-%d')}")

    size_mb = stats.cache_size_bytes / (1024 * 1024)
    console.print(f"[bold]Size:[/bold] {size_mb:.2f} MB")

    if stats.last_sync:
        console.print(f"[bold]Last sync:[/bold] {stats.last_sync.strftime('%Y-%m-%d %H:%M')}")


@cache_app.command(name="clear")
def cache_clear() -> None:
    """Clear all cached data."""
    ensure_dirs()
    cache = get_cache()

    if typer.confirm("Clear all cached messages and drafts?"):
        cache.clear()
        console.print("[green]Cache cleared.[/green]")


@cache_app.command(name="refresh")
def cache_refresh(
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Force full refresh from server."""
    ensure_dirs()
    config = get_config()
    cache = get_cache()

    account_name, _ = config.get_account(account)

    console.print(f"[dim]Refreshing from {account_name}...[/dim]")

    with get_imap_client(account_name) as client:
        messages = client.fetch_messages(
            folder="INBOX",
            limit=200,
            fetch_bodies=True,
        )

        for msg in messages:
            cache.store_message(msg)

        cache.mark_inbox_synced(account_name)

    cache.prune_old_messages(config.cache.window_days)
    console.print(f"[green]Refreshed {len(messages)} messages.[/green]")


# ============================================================================
# Account & Status Commands
# ============================================================================


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show connection status and account info."""
    ensure_dirs()
    config = get_config()

    status_info = {
        "version": __version__,
        "accounts": {},
    }

    for name in config.accounts:
        try:
            with get_imap_client(name) as client:
                status_info["accounts"][name] = {
                    "connected": True,
                    "folders": len(client.list_folders()),
                }
        except Exception as e:
            status_info["accounts"][name] = {
                "connected": False,
                "error": str(e),
            }

    if as_json:
        output_json(status_info)
        return

    console.print(f"[bold]Clerk v{__version__}[/bold]")
    console.print()

    for name, info in status_info["accounts"].items():
        if info["connected"]:
            console.print(f"[green]✓[/green] {name} - {info['folders']} folders")
        else:
            console.print(f"[red]✗[/red] {name} - {info.get('error', 'Unknown error')}")


# ============================================================================
# Account Management Commands
# ============================================================================

accounts_app = typer.Typer(help="Account management commands")
app.add_typer(accounts_app, name="accounts")


def _guess_imap_host(email: str) -> str:
    """Guess IMAP host from email domain."""
    domain = email.split("@")[1].lower()
    known_hosts = {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "live.com": "outlook.office365.com",
        "yahoo.com": "imap.mail.yahoo.com",
        "fastmail.com": "imap.fastmail.com",
        "fastmail.fm": "imap.fastmail.com",
        "icloud.com": "imap.mail.me.com",
        "me.com": "imap.mail.me.com",
        "protonmail.com": "127.0.0.1",  # ProtonMail Bridge
        "proton.me": "127.0.0.1",
    }
    return known_hosts.get(domain, f"imap.{domain}")


def _guess_smtp_host(email: str) -> str:
    """Guess SMTP host from email domain."""
    domain = email.split("@")[1].lower()
    known_hosts = {
        "gmail.com": "smtp.gmail.com",
        "googlemail.com": "smtp.gmail.com",
        "outlook.com": "smtp.office365.com",
        "hotmail.com": "smtp.office365.com",
        "live.com": "smtp.office365.com",
        "yahoo.com": "smtp.mail.yahoo.com",
        "fastmail.com": "smtp.fastmail.com",
        "fastmail.fm": "smtp.fastmail.com",
        "icloud.com": "smtp.mail.me.com",
        "me.com": "smtp.mail.me.com",
        "protonmail.com": "127.0.0.1",  # ProtonMail Bridge
        "proton.me": "127.0.0.1",
    }
    return known_hosts.get(domain, f"smtp.{domain}")


@accounts_app.callback(invoke_without_command=True)
def accounts_list(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """List configured accounts."""
    if ctx.invoked_subcommand is not None:
        return

    ensure_dirs()
    config = get_config()

    if as_json:
        account_list = []
        for name, acc in config.accounts.items():
            account_list.append({
                "name": name,
                "protocol": acc.protocol,
                "email": acc.from_.address,
                "default": name == config.default_account,
            })
        output_json(account_list)
        return

    if not config.accounts:
        console.print("[yellow]No accounts configured.[/yellow]")
        console.print("Run 'clerk accounts add' to configure an account.")
        return

    for name, acc in config.accounts.items():
        default = " [dim](default)[/dim]" if name == config.default_account else ""
        console.print(f"[bold]{name}[/bold]{default}")
        console.print(f"  Email: {acc.from_.address}")
        console.print(f"  Protocol: {acc.protocol}")


@accounts_app.command(name="add")
def accounts_add(
    name: Annotated[str, typer.Argument(help="Account name")],
    protocol: Annotated[str, typer.Option("--protocol", "-p", help="Protocol: imap or gmail")] = "imap",
    email: Annotated[Optional[str], typer.Option("--email", "-e", help="Email address")] = None,
    set_default: Annotated[bool, typer.Option("--default", help="Set as default account")] = False,
) -> None:
    """Add a new email account interactively."""
    ensure_dirs()
    config = load_config()

    if name in config.accounts:
        exit_with_code(ExitCode.INVALID_INPUT, f"Account '{name}' already exists")

    if protocol not in ("imap", "gmail"):
        exit_with_code(ExitCode.INVALID_INPUT, f"Unknown protocol: {protocol}. Use 'imap' or 'gmail'")

    # Get email address
    if not email:
        email = typer.prompt("Email address")

    # Validate email format (basic check)
    if "@" not in email:
        exit_with_code(ExitCode.INVALID_INPUT, f"Invalid email address: {email}")

    console.print(f"\n[bold]Setting up {protocol.upper()} account: {name}[/bold]")
    console.print(f"Email: {email}\n")

    if protocol == "gmail":
        account_config = _setup_gmail_account(name, email)
    else:
        account_config = _setup_imap_account(name, email)

    # Add to config
    config.accounts[name] = account_config

    # Set as default if requested or if it's the first account
    if set_default or not config.default_account:
        config.default_account = name

    # Save config
    save_config(config)

    console.print(f"\n[green]Account '{name}' added successfully![/green]")
    if config.default_account == name:
        console.print("[dim]Set as default account.[/dim]")


def _setup_imap_account(name: str, email: str) -> AccountConfig:
    """Set up an IMAP account interactively."""
    # IMAP settings
    imap_host = typer.prompt("IMAP host", default=_guess_imap_host(email))
    imap_port = typer.prompt("IMAP port", default="993", show_default=True)
    imap_username = typer.prompt("IMAP username", default=email)

    # SMTP settings
    smtp_host = typer.prompt("SMTP host", default=_guess_smtp_host(email))
    smtp_port = typer.prompt("SMTP port", default="587", show_default=True)
    smtp_username = typer.prompt("SMTP username", default=email)

    # Password
    password = typer.prompt("Password", hide_input=True)

    # Store password in keyring
    save_password(name, password)
    console.print("[dim]Password saved to system keyring.[/dim]")

    # Display name
    display_name = typer.prompt("Display name (optional)", default="")

    return AccountConfig(
        protocol="imap",
        imap=ImapConfig(
            host=imap_host,
            port=int(imap_port),
            username=imap_username,
        ),
        smtp=SmtpConfig(
            host=smtp_host,
            port=int(smtp_port),
            username=smtp_username,
        ),
        **{"from": FromAddress(address=email, name=display_name)},
    )


def _setup_gmail_account(name: str, email: str) -> AccountConfig:
    """Set up a Gmail account with OAuth."""
    from pathlib import Path

    console.print("[bold]Gmail OAuth Setup[/bold]")
    console.print(
        "You'll need a Google Cloud OAuth client ID file.\n"
        "Get one from: https://console.cloud.google.com/apis/credentials\n"
    )

    client_id_path = typer.prompt(
        "Path to client_id.json",
        default="~/.config/clerk/credentials.json",
    )
    client_id_file = Path(client_id_path).expanduser()

    if not client_id_file.exists():
        console.print(f"[yellow]Warning: File not found: {client_id_file}[/yellow]")
        console.print("You can add it later and run 'clerk accounts test' to authenticate.")

    # Display name
    display_name = typer.prompt("Display name (optional)", default="")

    # Create account config
    account_config = AccountConfig(
        protocol="gmail",
        oauth=OAuthConfig(client_id_file=client_id_file),
        **{"from": FromAddress(address=email, name=display_name)},
    )

    # Try to authenticate now if the file exists
    if client_id_file.exists():
        if typer.confirm("\nAuthenticate now?", default=True):
            try:
                from .oauth import run_oauth_flow

                console.print("\n[dim]Opening browser for authentication...[/dim]")
                run_oauth_flow(client_id_file, name)
                console.print("[green]Authentication successful![/green]")
            except Exception as e:
                console.print(f"[yellow]Authentication failed: {e}[/yellow]")
                console.print("You can try again later with 'clerk accounts test'.")

    return account_config


@accounts_app.command(name="test")
def accounts_test(
    name: Annotated[str, typer.Argument(help="Account name to test")],
) -> None:
    """Test account connectivity (IMAP and SMTP)."""
    ensure_dirs()
    config = get_config()

    if name not in config.accounts:
        exit_with_code(ExitCode.NOT_FOUND, f"Account '{name}' not found")

    account_config = config.accounts[name]
    console.print(f"[bold]Testing account: {name}[/bold]\n")

    # Test IMAP
    console.print("[dim]Testing IMAP connection...[/dim]")
    try:
        client = ImapClient(name, account_config)
        client.connect()
        folder_count = len(client.list_folders())
        client.disconnect()
        console.print(f"[green]IMAP: Connected ({folder_count} folders)[/green]")
    except Exception as e:
        console.print(f"[red]IMAP: Failed - {e}[/red]")

    # Test SMTP (only for IMAP protocol, Gmail uses same OAuth)
    if account_config.protocol == "imap":
        console.print("[dim]Testing SMTP connection...[/dim]")
        try:
            import asyncio

            smtp = account_config.smtp
            if smtp:
                password = account_config.get_password(name)

                async def test_smtp() -> None:
                    import aiosmtplib

                    client = aiosmtplib.SMTP(
                        hostname=smtp.host,
                        port=smtp.port,
                        start_tls=smtp.starttls,
                    )
                    await client.connect()
                    await client.login(smtp.username, password)
                    await client.quit()

                asyncio.run(test_smtp())
                console.print("[green]SMTP: Connected[/green]")
            else:
                console.print("[yellow]SMTP: Not configured[/yellow]")
        except Exception as e:
            console.print(f"[red]SMTP: Failed - {e}[/red]")
    else:
        console.print("[green]SMTP: Uses same OAuth credentials[/green]")

    console.print("\n[bold]Test complete.[/bold]")


@accounts_app.command(name="remove")
def accounts_remove(
    name: Annotated[str, typer.Argument(help="Account name to remove")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
) -> None:
    """Remove an account and its stored credentials."""
    ensure_dirs()
    config = load_config()

    if name not in config.accounts:
        exit_with_code(ExitCode.NOT_FOUND, f"Account '{name}' not found")

    account_config = config.accounts[name]

    if not yes:
        console.print(f"[bold]Remove account: {name}[/bold]")
        console.print(f"Email: {account_config.from_.address}")
        console.print(f"Protocol: {account_config.protocol}")
        console.print("\n[yellow]This will delete stored credentials.[/yellow]")

        if not typer.confirm("Are you sure?"):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    # Delete credentials
    if account_config.protocol == "gmail":
        delete_oauth_token(name)
    else:
        delete_password(name)

    # Remove from config
    del config.accounts[name]

    # Update default if needed
    if config.default_account == name:
        config.default_account = next(iter(config.accounts), "")

    # Save config
    save_config(config)

    console.print(f"[green]Account '{name}' removed.[/green]")


@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"clerk {__version__}")


@app.command(name="mcp-server")
def mcp_server() -> None:
    """Start the MCP (Model Context Protocol) server for LLM integration.

    This starts a stdio-based MCP server that allows LLM agents to interact
    with email through structured tool calls.

    Example Claude Desktop configuration:
    {
        "mcpServers": {
            "clerk": {
                "command": "clerk",
                "args": ["mcp-server"]
            }
        }
    }
    """
    from .mcp_server import run_server

    run_server()


@app.command()
def shell() -> None:
    """Start an interactive shell/REPL.

    Provides a readline-like experience with:
    - Command history (persisted)
    - Tab completion for commands and options
    - All CLI commands available
    - Extra: sql command for raw queries

    Example:
        clerk shell
        clerk> inbox --limit 5
        clerk> search from:alice subject:meeting
        clerk> sql SELECT * FROM messages LIMIT 5
        clerk> exit
    """
    from .shell import run_shell

    run_shell()


@app.command()
def attachment(
    message_id: Annotated[str, typer.Argument(help="Message ID")],
    filename: Annotated[Optional[str], typer.Argument(help="Attachment filename")] = None,
    save: Annotated[Optional[str], typer.Option("--save", "-s", help="Save to path")] = None,
    list_only: Annotated[bool, typer.Option("--list", "-l", help="List attachments only")] = False,
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
) -> None:
    """Download or list attachments from a message.

    Examples:
        clerk attachment <msg-id> --list
        clerk attachment <msg-id> document.pdf --save ./downloads/
    """
    ensure_dirs()
    from .api import get_api

    api = get_api()

    if list_only or filename is None:
        # List attachments
        attachments = api.list_attachments(message_id)

        if not attachments:
            msg = api.get_message(message_id)
            if not msg:
                exit_with_code(ExitCode.NOT_FOUND, f"Message not found: {message_id}")
            console.print("[dim]No attachments.[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Filename", width=40)
        table.add_column("Size", justify="right", width=12)
        table.add_column("Type", width=30)

        for att in attachments:
            size_kb = att.get("size", 0) / 1024
            table.add_row(
                att.get("filename", "unknown"),
                f"{size_kb:.1f} KB",
                att.get("content_type", "unknown"),
            )

        console.print(table)
        return

    # Download attachment
    if not save:
        save = "."

    try:
        from pathlib import Path

        dest = api.download_attachment(message_id, filename, Path(save))
        console.print(f"[green]Saved to:[/green] {dest}")
    except FileNotFoundError as e:
        exit_with_code(ExitCode.NOT_FOUND, str(e))


@app.command(name="search-sql")
def search_sql(
    query: Annotated[str, typer.Argument(help="SQL SELECT query")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 100,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Execute a raw SQL query on the messages table.

    Only SELECT queries are allowed. Use this for complex queries that
    can't be expressed with the regular search operators.

    Examples:
        clerk search-sql "SELECT * FROM messages WHERE from_addr LIKE '%alice%'"
        clerk search-sql "SELECT * FROM messages ORDER BY date_utc DESC LIMIT 10"
    """
    ensure_dirs()
    from .api import get_api

    api = get_api()

    try:
        messages = api.search_sql(query, limit=limit)
    except ValueError as e:
        exit_with_code(ExitCode.INVALID_INPUT, str(e))

    if as_json:
        output_json([m.model_dump() for m in messages])
        return

    if not messages:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=12)
    table.add_column("From", width=25)
    table.add_column("Subject", width=40)
    table.add_column("Date", width=12)

    for msg in messages:
        table.add_row(
            msg.conv_id,
            msg.from_.addr[:25] if msg.from_ else "",
            msg.subject[:40] if msg.subject else "",
            msg.date.strftime("%b %d") if msg.date else "",
        )

    console.print(table)


@app.command(name="search-advanced")
def search_advanced(
    query: Annotated[str, typer.Argument(help="Search query with operators")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max results")] = 20,
    account: Annotated[Optional[str], typer.Option("--account", "-a", help="Account name")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Advanced search with operator support.

    Supports operators like:
    - from:alice, to:bob
    - subject:meeting, body:quarterly
    - has:attachment
    - is:unread, is:read, is:flagged
    - after:2025-01-01, before:2025-12-31, date:2025-06-15

    Examples:
        clerk search-advanced "from:alice has:attachment after:2025-01-01"
        clerk search-advanced "is:unread subject:urgent" --json
    """
    ensure_dirs()
    from .api import get_api

    api = get_api()

    result = api.search_advanced(query, account=account, limit=limit)

    if as_json:
        output_json([m.model_dump() for m in result.messages])
        return

    if not result.messages:
        console.print("[dim]No results found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=12)
    table.add_column("From", width=25)
    table.add_column("Subject", width=40)
    table.add_column("Date", width=12)

    for msg in result.messages:
        table.add_row(
            msg.conv_id,
            msg.from_.addr[:25] if msg.from_ else "",
            msg.subject[:40] if msg.subject else "",
            msg.date.strftime("%b %d") if msg.date else "",
        )

    console.print(table)


if __name__ == "__main__":
    app()
