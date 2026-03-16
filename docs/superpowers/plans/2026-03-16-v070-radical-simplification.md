# Clerk v0.7.0 Radical Simplification — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe clerk as an MCP server for Claude Code. Delete ~2,000 lines of dead/redundant code, fix 6 MCP tool issues, slim the CLI to setup/debug only.

**Architecture:** MCP server becomes the primary interface. CLI retains only account management, auth, status, sync, and cache diagnostics. API layer slims to mutations + sync + status. All reads go through `clerk_sql`.

**Tech Stack:** Python 3.11+, FastMCP, SQLite, Pydantic, Typer (minimal)

**Spec:** `docs/superpowers/specs/2026-03-16-v070-radical-simplification-design.md`

---

## Chunk 1: Deletions

### Task 1: Delete shell.py

**Files:**
- Delete: `src/clerk/shell.py`

- [ ] **Step 1: Delete the file**

```bash
git rm src/clerk/shell.py
```

- [ ] **Step 2: Remove shell import from cli.py**

In `src/clerk/cli.py`, delete the `shell` command (lines 1178-1197):

```python
@app.command()
def shell() -> None:
```

Delete the entire function.

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -x -q`
Expected: All pass (shell had no tests, no other code imports it).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: delete shell.py — dead interactive REPL (544 lines)"
```

---

### Task 2: Gut cli.py

Rewrite cli.py keeping only: `mcp-server`, `version`, `status`, `accounts` group, `cache` group (status + clear only), and a new `sync` command. Delete all email read/write commands.

**Note:** The existing `cache refresh` subcommand is intentionally removed — replaced by the new top-level `sync` command. `pyproject.toml` (0.4.0) and `__init__.py` (0.6.0) are currently out of sync — both will be updated to 0.7.0 in Task 11.

**Files:**
- Rewrite: `src/clerk/cli.py`

- [ ] **Step 1: Write the trimmed cli.py**

Replace `src/clerk/cli.py` with this content (keeps only setup/auth/debug commands, adds `sync`):

```python
"""Clerk CLI — setup, auth, and debug commands. Primary interface is MCP."""

import json
from typing import Annotated, Any

import typer
from rich.console import Console

from . import __version__
from .cache import get_cache
from .config import (
    AccountConfig,
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
from .imap_client import ImapClient, get_imap_client
from .models import ExitCode

app = typer.Typer(
    name="clerk",
    help="Email MCP server for LLM agents. Use 'clerk mcp-server' to start.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


def output_json(data: dict[str, Any] | list[Any]) -> None:
    """Output data as JSON."""
    print(json.dumps(data, default=str, indent=2))


def exit_with_code(code: ExitCode, message: str | None = None) -> None:
    """Exit with a specific exit code and optional message."""
    if message:
        err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code.value)


# ============================================================================
# Primary Entry Point
# ============================================================================


@app.command(name="mcp-server")
def mcp_server() -> None:
    """Start the MCP server for LLM integration (primary interface)."""
    ensure_dirs()
    from .mcp_server import run_server

    run_server()


@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"clerk {__version__}")


# ============================================================================
# Status & Sync (debugging)
# ============================================================================


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show connection status and account info."""
    ensure_dirs()
    config = get_config()

    status_info: dict[str, Any] = {
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


@app.command()
def sync(
    account: Annotated[str | None, typer.Option("--account", "-a", help="Account name")] = None,
    folder: Annotated[str, typer.Option("--folder", "-f", help="Folder to sync")] = "INBOX",
    full: Annotated[bool, typer.Option("--full", help="Full re-sync (ignore sync state)")] = False,
) -> None:
    """Sync email cache from IMAP server."""
    ensure_dirs()
    from .api import get_api

    api = get_api()
    result = api.sync_folder(account=account, folder=folder, full=full)
    console.print(
        f"[green]Synced {result['synced']} messages[/green] "
        f"from {result['account']}/{result['folder']}"
    )


# ============================================================================
# Cache Commands
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


# ============================================================================
# Account Management
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
        "protonmail.com": "127.0.0.1",
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
        "protonmail.com": "127.0.0.1",
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
    protocol: Annotated[str, typer.Option("--protocol", "-p", help="Protocol: imap, gmail, or microsoft365")] = "imap",
    email: Annotated[str | None, typer.Option("--email", "-e", help="Email address")] = None,
    set_default: Annotated[bool, typer.Option("--default", help="Set as default account")] = False,
) -> None:
    """Add a new email account interactively."""
    ensure_dirs()
    config = load_config()

    if name in config.accounts:
        exit_with_code(ExitCode.INVALID_INPUT, f"Account '{name}' already exists")

    if protocol not in ("imap", "gmail", "microsoft365"):
        exit_with_code(ExitCode.INVALID_INPUT, f"Unknown protocol: {protocol}. Use 'imap', 'gmail', or 'microsoft365'")

    if not email:
        email = typer.prompt("Email address")

    if "@" not in email:
        exit_with_code(ExitCode.INVALID_INPUT, f"Invalid email address: {email}")

    console.print(f"\n[bold]Setting up {protocol.upper()} account: {name}[/bold]")
    console.print(f"Email: {email}\n")

    if protocol == "gmail":
        account_config = _setup_gmail_account(name, email)
    elif protocol == "microsoft365":
        account_config = _setup_microsoft365_account(name, email)
    else:
        account_config = _setup_imap_account(name, email)

    config.accounts[name] = account_config

    if set_default or not config.default_account:
        config.default_account = name

    save_config(config)

    console.print(f"\n[green]Account '{name}' added successfully![/green]")
    if config.default_account == name:
        console.print("[dim]Set as default account.[/dim]")


def _setup_imap_account(name: str, email: str) -> AccountConfig:
    """Set up an IMAP account interactively."""
    imap_host = typer.prompt("IMAP host", default=_guess_imap_host(email))
    imap_port = typer.prompt("IMAP port", default="993", show_default=True)
    imap_username = typer.prompt("IMAP username", default=email)

    smtp_host = typer.prompt("SMTP host", default=_guess_smtp_host(email))
    smtp_port = typer.prompt("SMTP port", default="587", show_default=True)
    smtp_username = typer.prompt("SMTP username", default=email)

    password = typer.prompt("Password", hide_input=True)
    save_password(name, password)
    console.print("[dim]Password saved to system keyring.[/dim]")

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
        **{"from": FromAddress(address=email, name=display_name)},  # type: ignore[arg-type]
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

    display_name = typer.prompt("Display name (optional)", default="")

    account_config = AccountConfig(
        protocol="gmail",
        oauth=OAuthConfig(client_id_file=client_id_file),
        **{"from": FromAddress(address=email, name=display_name)},  # type: ignore[arg-type]
    )

    if client_id_file.exists() and typer.confirm("\nAuthenticate now?", default=True):
        try:
            from .oauth import run_oauth_flow

            console.print("\n[dim]Opening browser for authentication...[/dim]")
            run_oauth_flow(client_id_file, name)
            console.print("[green]Authentication successful![/green]")
        except Exception as e:
            console.print(f"[yellow]Authentication failed: {e}[/yellow]")
            console.print("You can try again later with 'clerk accounts test'.")

    return account_config


def _setup_microsoft365_account(name: str, email: str) -> AccountConfig:
    """Set up a Microsoft 365 account with device code flow."""
    console.print("[bold]Microsoft 365 OAuth Setup[/bold]")
    console.print(
        "You'll authenticate using your browser.\n"
        "No additional setup is needed — just sign in with your Microsoft account.\n"
    )

    display_name = typer.prompt("Display name (optional)", default="")

    account_config = AccountConfig(
        protocol="microsoft365",
        **{"from": FromAddress(address=email, name=display_name)},  # type: ignore[arg-type]
    )

    if typer.confirm("\nAuthenticate now?", default=True):
        try:
            from .microsoft365 import run_m365_device_code_flow

            console.print()
            run_m365_device_code_flow(name)
            console.print("\n[green]Authentication successful![/green]")
        except Exception as e:
            console.print(f"\n[yellow]Authentication failed: {e}[/yellow]")
            console.print("You can try again later with 'clerk accounts auth'.")

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

    console.print("[dim]Testing IMAP connection...[/dim]")
    try:
        client = ImapClient(name, account_config)
        client.connect()
        folder_count = len(client.list_folders())
        client.disconnect()
        console.print(f"[green]IMAP: Connected ({folder_count} folders)[/green]")
    except Exception as e:
        console.print(f"[red]IMAP: Failed - {e}[/red]")

    if account_config.protocol == "imap":
        console.print("[dim]Testing SMTP connection...[/dim]")
        try:
            import asyncio

            smtp = account_config.smtp
            if smtp:
                password = account_config.get_password(name)

                async def test_smtp() -> None:
                    import aiosmtplib

                    smtp_client = aiosmtplib.SMTP(
                        hostname=smtp.host,
                        port=smtp.port,
                        start_tls=smtp.starttls,
                    )
                    await smtp_client.connect()
                    await smtp_client.login(smtp.username, password)
                    await smtp_client.quit()

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

    if account_config.protocol == "gmail":
        delete_oauth_token(name)
    elif account_config.protocol == "microsoft365":
        from .config import delete_m365_token_cache
        delete_m365_token_cache(name)
    else:
        delete_password(name)

    del config.accounts[name]

    if config.default_account == name:
        config.default_account = next(iter(config.accounts), "")

    save_config(config)
    console.print(f"[green]Account '{name}' removed.[/green]")


@accounts_app.command(name="auth")
def accounts_auth(
    name: Annotated[str, typer.Argument(help="Account name to authenticate")],
) -> None:
    """Re-authenticate an account (run OAuth flow again)."""
    ensure_dirs()
    config = get_config()

    if name not in config.accounts:
        exit_with_code(ExitCode.NOT_FOUND, f"Account '{name}' not found")

    account_config = config.accounts[name]

    if account_config.protocol == "gmail":
        from .oauth import run_oauth_flow

        if not account_config.oauth:
            exit_with_code(ExitCode.INVALID_INPUT, f"Gmail account '{name}' missing OAuth configuration")

        console.print("[dim]Opening browser for Google authentication...[/dim]")
        try:
            run_oauth_flow(account_config.oauth.client_id_file, name)  # type: ignore[union-attr]
            console.print("[green]Authentication successful![/green]")
        except Exception as e:
            exit_with_code(ExitCode.CONNECTION_ERROR, f"Authentication failed: {e}")

    elif account_config.protocol == "microsoft365":
        from .microsoft365 import run_m365_device_code_flow

        console.print("[bold]Microsoft 365 Re-authentication[/bold]\n")
        try:
            run_m365_device_code_flow(name)
            console.print("\n[green]Authentication successful![/green]")
        except Exception as e:
            exit_with_code(ExitCode.CONNECTION_ERROR, f"Authentication failed: {e}")

    else:
        exit_with_code(
            ExitCode.INVALID_INPUT,
            f"Account '{name}' uses password authentication. "
            "Use 'clerk accounts add' to reconfigure.",
        )
```

- [ ] **Step 2: Run lint**

Run: `ruff check src/clerk/cli.py`
Expected: Clean (or fix any issues).

- [ ] **Step 3: Run remaining CLI tests**

Run: `pytest tests/test_cli.py -x -q`
Expected: Some tests fail (they test deleted commands). That's expected — we'll clean tests in Task 5.

- [ ] **Step 4: Commit**

```bash
git add src/clerk/cli.py
git commit -m "refactor(cli): gut to ~350 lines — setup/auth/debug only

Remove all email read/write commands. MCP server is the primary interface.
Keep: mcp-server, version, status, sync, cache, accounts."
```

---

### Task 3: Slim api.py

Remove read-side dataclasses and methods. Add `create_reply()` method for MCP.

**Files:**
- Modify: `src/clerk/api.py`

- [ ] **Step 1: Remove dead dataclasses and imports**

Delete `InboxResult`, `SearchResult`, `SendPreview`, `ConversationLookupResult` dataclasses (lines 33-73).

Remove `SearchQuery` from imports (line 28):
```python
# Delete this line:
from .search import SearchQuery
```

- [ ] **Step 2: Remove dead methods**

Delete these methods from `ClerkAPI`:
- `list_inbox()` (lines 135-205)
- `search()` (lines 299-316)
- `search_advanced()` (lines 318-347)
- `search_sql()` (lines 349-368)
- `resolve_conversation_id()` (lines 264-293)
- `refresh_cache()` (lines 718-741)
- `list_attachments()` (lines 583-600)
- `download_attachment()` (lines 602-649)

- [ ] **Step 3: Add create_reply() method**

Add this method to `ClerkAPI` in the Draft Operations section, after `create_draft()`:

```python
def create_reply(
    self,
    message_id: str,
    body: str,
    reply_all: bool = False,
    account: str | None = None,
) -> Draft:
    """Create a reply draft to an existing message.

    Args:
        message_id: Message ID to reply to
        body: Reply body text
        reply_all: Include all original recipients
        account: Account name (uses message's account if not provided)

    Returns:
        Created Draft

    Raises:
        ValueError: If original message not found
    """
    # Use cache lookup — we only need metadata (conv_id, account), not body
    msg = self.cache.get_message(message_id)
    if not msg:
        raise ValueError(f"Message not found: {message_id}")

    reply_account = account or msg.account
    account_name, _ = self.config.get_account(reply_account)

    return self.drafts.create_reply(
        account=account_name,
        conv_id=msg.conv_id,
        body_text=body,
        reply_all=reply_all,
    )
```

- [ ] **Step 4: Remove unused Path import if no longer needed**

Check if `Path` is still used in api.py. After removing `download_attachment()`, it may not be. If unused, remove `from pathlib import Path`.

- [ ] **Step 5: Add tests for create_reply() in test_api.py**

Add to `tests/test_api.py`:

```python
class TestCreateReply:
    def test_create_reply_success(self, api, cache, sample_message):
        cache.store_message(sample_message)

        with patch.object(api.drafts, "create_reply") as mock_create:
            mock_create.return_value = MagicMock(draft_id="d1")
            draft = api.create_reply(
                message_id=sample_message.message_id,
                body="Thanks!",
            )
            mock_create.assert_called_once_with(
                account=sample_message.account,
                conv_id=sample_message.conv_id,
                body_text="Thanks!",
                reply_all=False,
            )
            assert draft.draft_id == "d1"

    def test_create_reply_message_not_found(self, api, cache):
        with pytest.raises(ValueError, match="not found"):
            api.create_reply(message_id="<nonexistent>", body="Hello")

    def test_create_reply_passes_reply_all(self, api, cache, sample_message):
        cache.store_message(sample_message)

        with patch.object(api.drafts, "create_reply") as mock_create:
            mock_create.return_value = MagicMock(draft_id="d1")
            api.create_reply(
                message_id=sample_message.message_id,
                body="Thanks!",
                reply_all=True,
            )
            mock_create.assert_called_once_with(
                account=sample_message.account,
                conv_id=sample_message.conv_id,
                body_text="Thanks!",
                reply_all=True,
            )
```

- [ ] **Step 6: Run lint and type check**

Run: `ruff check src/clerk/api.py && mypy src/clerk/api.py`
Expected: Clean.

- [ ] **Step 7: Commit**

```bash
git add src/clerk/api.py tests/test_api.py
git commit -m "refactor(api): remove read-side methods, add create_reply()

Delete: list_inbox, search, search_advanced, search_sql,
resolve_conversation_id, refresh_cache, list_attachments,
download_attachment, and 4 CLI-only dataclasses.
Add: create_reply() for MCP clerk_reply routing."
```

---

### Task 4: Clean cache.py

Remove methods that are no longer called by anything.

**Files:**
- Modify: `src/clerk/cache.py`

- [ ] **Step 1: Delete dead methods**

Delete these methods from `Cache`:
- `search_advanced()` (only caller was `api.search_advanced()`)
- `execute_raw_query()` (only caller was `api.search_sql()`)
- `search()` (only caller was `api.search()`)

- [ ] **Step 2: Clean up imports**

Remove unused imports from the top of cache.py. After deleting `search_advanced()`, these become unused:
```python
# Remove from the import line:
from .search import SearchQuery, build_fts_query, build_where_clauses, parse_search_query
```

Keep the `search` module file itself — the FTS table schema and triggers remain in `SCHEMA`. Note: after this task, `search.py` (402 lines) has zero internal callers. It's intentionally retained because the FTS infrastructure it documents is used by `clerk_sql` users writing MATCH queries. Consider deleting in a follow-up if truly unused, but `test_search.py` stays to keep the FTS query builder tested.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_cache.py -x -q`
Expected: All pass (cache tests don't test deleted methods directly — `execute_readonly_sql` has its own tests in `test_mcp_sql.py`).

- [ ] **Step 4: Commit**

```bash
git add src/clerk/cache.py
git commit -m "refactor(cache): remove dead methods — search, search_advanced, execute_raw_query"
```

---

### Task 5: Clean tests

Remove tests for deleted code, keep tests for surviving code.

**Files:**
- Rewrite: `tests/test_cli.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Rewrite test_cli.py**

Keep only test classes for surviving commands. Delete test classes for removed commands.

**Keep these classes** (they test retained CLI commands):
- `TestVersion`
- `TestStatus`
- `TestAccounts` (renamed from `TestAccountsCommands` if needed — check)
- `TestCacheCommands`
- `TestMicrosoft365Accounts`
- `TestHostGuessing`

**Delete these classes** (they test removed commands):
- `TestDraftCommands`
- `TestInboxCommand`
- `TestSearchCommand`
- `TestShowCommand`
- `TestSendCommand`

Also update imports at top of test_cli.py to remove references to deleted code (e.g., `Address`, `MessageFlag`, `Conversation`, etc. if they were only used in deleted tests).

- [ ] **Step 2: Clean test_api.py**

Delete these test classes (test deleted api methods):
- `TestSearch` (tests `search`, `search_advanced`, `search_sql`)
- `TestAttachments` (tests `list_attachments`)
- `TestResolveConversationId` (tests `resolve_conversation_id`)

Delete from `TestInbox`:
- `test_list_inbox_from_cache` (tests `list_inbox`)

Keep from `TestInbox` (rename class to `TestMessages`):
- `test_get_conversation`
- `test_get_conversation_not_found`
- `test_get_message`
- `test_get_message_not_found`

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass.

- [ ] **Step 4: Run coverage**

Run: `pytest --cov=clerk --cov-report=term-missing -q`
Expected: Coverage should improve (deleted low-coverage code).

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py tests/test_api.py
git commit -m "test: remove tests for deleted CLI/API methods"
```

---

## Chunk 2: MCP Fixes

### Task 6: Fix clerk_reply — route through API, drop preview

**Files:**
- Modify: `src/clerk/mcp_server.py`
- Modify: `tests/test_mcp_redesign.py`

- [ ] **Step 1: Delete stale test classes**

In `tests/test_mcp_redesign.py`, delete the existing `TestClerkReply` class — it tests the old routing behavior (expects `"preview" in result`). Also delete `TestClerkDraft` — it passes `to="bob@example.com"` (string), which will break after Task 7 changes `clerk_draft` to take `list[str]`. Both are replaced by new tests below.

- [ ] **Step 2: Write test for updated clerk_reply**

Add to `tests/test_mcp_redesign.py`:

```python
class TestClerkReplyRouting:
    """Test that clerk_reply routes through api.create_reply()."""

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_calls_api_create_reply(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=False,
            account=None,
        )
        assert result["draft_id"] == "d1"
        assert "preview" not in result  # no redundant preview

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_with_reply_all(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_reply.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="Alice")],
            cc=[Address(addr="bob@example.com", name="Bob")],
            subject="Re: Test",
            body_text="reply body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body", reply_all=True)

        mock_api.create_reply.assert_called_once_with(
            message_id="<msg1>",
            body="reply body",
            reply_all=True,
            account=None,
        )

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_reply_message_not_found(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_reply

        mock_api = MagicMock()
        mock_api.create_reply.side_effect = ValueError("Message not found: <msg1>")
        mock_get_api.return_value = mock_api

        result = clerk_reply(message_id="<msg1>", body="reply body")
        assert "error" in result
        assert "not found" in result["error"].lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_mcp_redesign.py::TestClerkReplyRouting -v`
Expected: Fails (clerk_reply still uses old routing).

- [ ] **Step 4: Update clerk_reply in mcp_server.py**

Replace the `clerk_reply` function:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_mcp_redesign.py::TestClerkReplyRouting -v`
Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/clerk/mcp_server.py tests/test_mcp_redesign.py
git commit -m "fix(mcp): route clerk_reply through api.create_reply(), drop redundant preview"
```

---

### Task 7: Fix clerk_draft — list params

**Files:**
- Modify: `src/clerk/mcp_server.py`
- Modify: `tests/test_mcp_redesign.py`

- [ ] **Step 1: Write test for list params**

Add to `tests/test_mcp_redesign.py`:

```python
class TestClerkDraftListParams:
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_draft_with_list_params(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_draft
        from clerk.models import Address, Draft

        mock_api = MagicMock()
        mock_api.create_draft.return_value = Draft(
            draft_id="d1",
            account="test",
            to=[Address(addr="alice@example.com", name="")],
            cc=[Address(addr="bob@example.com", name="")],
            subject="Test",
            body_text="body",
        )
        mock_get_api.return_value = mock_api

        result = clerk_draft(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
        )

        assert result["draft_id"] == "d1"
        mock_api.create_draft.assert_called_once_with(
            to=["alice@example.com"],
            subject="Test",
            body="body",
            cc=["bob@example.com"],
            account=None,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_redesign.py::TestClerkDraftListParams -v`
Expected: Fails (clerk_draft still takes `str`).

- [ ] **Step 3: Update clerk_draft in mcp_server.py**

Replace the `clerk_draft` function:

```python
@mcp.tool()
def clerk_draft(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Compose a new email (not a reply).

    Creates a draft and returns a preview for user confirmation.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_redesign.py::TestClerkDraftListParams -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/mcp_server.py tests/test_mcp_redesign.py
git commit -m "fix(mcp): clerk_draft takes list[str] params instead of comma-separated strings"
```

---

### Task 8: Fix clerk_sync — sync-all mode

**Files:**
- Modify: `src/clerk/mcp_server.py`
- Modify: `tests/test_mcp_redesign.py`

- [ ] **Step 1: Write test for sync-all**

Add to `tests/test_mcp_redesign.py`:

```python
class TestClerkSyncAll:
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_all_accounts(self, mock_dirs, mock_get_api, mock_get_config):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.side_effect = [
            {"synced": 5, "account": "siue", "folder": "INBOX"},
            {"synced": 12, "account": "gmail", "folder": "INBOX"},
        ]
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"siue": MagicMock(), "gmail": MagicMock()}
        mock_get_config.return_value = mock_config

        result = clerk_sync()

        assert result["total_synced"] == 17
        assert result["accounts"]["siue"]["synced"] == 5
        assert result["accounts"]["gmail"]["synced"] == 12
        assert mock_api.sync_folder.call_count == 2

    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_sync_single_account(self, mock_dirs, mock_get_api):
        from clerk.mcp_server import clerk_sync

        mock_api = MagicMock()
        mock_api.sync_folder.return_value = {"synced": 5, "account": "siue", "folder": "INBOX"}
        mock_get_api.return_value = mock_api

        result = clerk_sync(account="siue")

        assert result["synced"] == 5
        mock_api.sync_folder.assert_called_once_with(account="siue", folder="INBOX", full=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_redesign.py::TestClerkSyncAll -v`
Expected: Fails (sync-all not implemented).

- [ ] **Step 3: Update clerk_sync in mcp_server.py**

Add `get_config` to the imports at the top (it's already there for `resource_config`). Replace `clerk_sync`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_redesign.py::TestClerkSyncAll -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clerk/mcp_server.py tests/test_mcp_redesign.py
git commit -m "feat(mcp): clerk_sync syncs all accounts when called without account param"
```

---

### Task 9: Fix resource_folders — cache with TTL

**Files:**
- Modify: `src/clerk/cache.py`
- Modify: `src/clerk/mcp_server.py`
- Modify: `tests/test_mcp_redesign.py`

- [ ] **Step 1: Add get_meta/set_meta to Cache**

In `src/clerk/cache.py`, add after `mark_inbox_synced()`:

```python
def get_meta(self, key: str) -> str | None:
    """Get a value from cache_meta."""
    with self._connect() as conn:
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

def set_meta(self, key: str, value: str) -> None:
    """Set a value in cache_meta."""
    with self._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
```

- [ ] **Step 2: Add datetime import to mcp_server.py module level**

Add at the top of `src/clerk/mcp_server.py`:

```python
from datetime import UTC, datetime
```

- [ ] **Step 3: Update resource_folders with caching**

Replace `resource_folders` in mcp_server.py:

```python
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
```

- [ ] **Step 4: Write test for folder caching**

Add to `tests/test_mcp_redesign.py`:

```python
class TestResourceFoldersCaching:
    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_caches_folder_list(self, mock_dirs, mock_get_api, mock_get_config):
        from clerk.mcp_server import resource_folders

        mock_api = MagicMock()
        mock_folder = MagicMock()
        mock_folder.name = "INBOX"
        mock_api.list_folders.return_value = [mock_folder]
        mock_api.cache.get_meta.return_value = None  # no cache yet
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"test": MagicMock()}
        mock_get_config.return_value = mock_config

        # First call hits IMAP
        result = resource_folders()
        assert '"INBOX"' in result
        mock_api.list_folders.assert_called_once()
        mock_api.cache.set_meta.assert_called()  # caches result

    @patch("clerk.mcp_server.get_config")
    @patch("clerk.mcp_server.get_api")
    @patch("clerk.mcp_server.ensure_dirs")
    def test_uses_cache_within_ttl(self, mock_dirs, mock_get_api, mock_get_config):
        import json as json_mod
        from datetime import UTC, datetime

        from clerk.mcp_server import resource_folders

        mock_api = MagicMock()
        # Return cached data
        mock_api.cache.get_meta.side_effect = lambda k: {
            "folders_test": json_mod.dumps(["INBOX", "Sent"]),
            "folders_test_at": datetime.now(UTC).isoformat(),
        }.get(k)
        mock_get_api.return_value = mock_api

        mock_config = MagicMock()
        mock_config.accounts = {"test": MagicMock()}
        mock_get_config.return_value = mock_config

        result = resource_folders()
        assert "INBOX" in result
        mock_api.list_folders.assert_not_called()  # did NOT hit IMAP
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_mcp_redesign.py::TestResourceFoldersCaching -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/clerk/cache.py src/clerk/mcp_server.py tests/test_mcp_redesign.py
git commit -m "fix(mcp): cache resource_folders with 1-hour TTL

Avoids hitting IMAP on every resource read. Uses cache_meta table."
```

---

## Chunk 3: Finalization

### Task 10: Remove prompt-toolkit dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove dependency**

In `pyproject.toml`, remove this line from `dependencies`:
```
"prompt-toolkit>=3.0.0",
```

- [ ] **Step 2: Verify no remaining imports**

Run: `grep -r "prompt.toolkit\|prompt_toolkit" src/`
Expected: No results (shell.py was the only user).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: remove prompt-toolkit dependency (only used by deleted shell.py)"
```

---

### Task 11: Version bump and docs

**Files:**
- Modify: `src/clerk/__init__.py`
- Modify: `pyproject.toml`
- Modify: `CLAUDE.md`
- Delete: `docs/plans/2026-02-16-v060-cleanup-mcp-parity.md`

- [ ] **Step 1: Bump version to 0.7.0**

In `src/clerk/__init__.py`:
```python
__version__ = "0.7.0"
```

In `pyproject.toml`:
```
version = "0.7.0"
```

- [ ] **Step 2: Update CLAUDE.md architecture diagram**

Replace the layer diagram in `CLAUDE.md` with:

```
### Layer Diagram

\```
┌─────────────────────────────────────────────────────────────┐
│  Entry Points                                                │
│  ├── mcp_server.py  (8 tools + 3 resources — primary)       │
│  └── cli.py  (~350 lines — setup/auth/debug only)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  api.py - ClerkAPI                                          │
│  (Mutations, sync, status — reads via clerk_sql)            │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ imap_client.py  │  │    cache.py     │  │  smtp_client.py │
│ (IMAP protocol) │  │ (SQLite + FTS5) │  │  (SMTP sends)   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│  models.py (Pydantic models: Message, Conversation, etc.)   │
│  config.py (YAML config + credential management)            │
└─────────────────────────────────────────────────────────────┘
\```
```

Also update the entry points description to say:
- **cli.py** — Account setup, auth, status, sync, cache diagnostics. Not an email client.
- **mcp_server.py** — Primary interface. 8 tools + 3 resources for LLM agents.

Remove `shell.py` from the Key Modules list.

- [ ] **Step 3: Delete stale plan doc**

```bash
git rm docs/plans/2026-02-16-v060-cleanup-mcp-parity.md
```

- [ ] **Step 4: Run full test suite + lint + mypy**

Run: `pytest tests/ -x -q && ruff check src tests && mypy src`
Expected: All clean.

- [ ] **Step 5: Run coverage**

Run: `pytest --cov=clerk --cov-report=term-missing -q`
Expected: Higher coverage than before (deleted low-coverage code).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: bump to v0.7.0, update CLAUDE.md, delete stale plan doc"
```

---

### Task 12: Final verification

- [ ] **Step 1: Verify MCP server starts**

Run: `timeout 3 clerk mcp-server 2>&1; echo "exit: $?"`
Expected: Starts and gets killed by timeout (exit 124). No import errors.

- [ ] **Step 2: Verify tool count**

Run:
```python
python -c "
from clerk.mcp_server import mcp
tools = mcp._tool_manager._tools
resources = mcp._resource_manager._resources
print(f'Tools: {len(tools)} — {sorted(tools)}')
print(f'Resources: {len(resources)} — {sorted(resources)}')
"
```
Expected: 8 tools, 3 resources.

- [ ] **Step 3: Verify line count reduction**

Run: `wc -l src/clerk/*.py | sort -n`
Expected: Total ~4,500 or less (down from ~6,500).

- [ ] **Step 4: Verify CLI commands**

Run: `clerk --help`
Expected: Shows only: `mcp-server`, `version`, `status`, `sync`, `cache`, `accounts`.
