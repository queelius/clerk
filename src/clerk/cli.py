"""Clerk CLI — setup, auth, and debug commands. Primary interface is MCP."""

import json
from typing import Annotated, Any

import typer
from rich.console import Console

from . import __version__
from .api import get_api
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
from .imap_client import ImapClient
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
    status_info = get_api().get_status()

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
    result = get_api().sync_folder(account=account, folder=folder, full=full)
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
    stats = get_api().get_cache_stats()

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
    if typer.confirm("Clear all cached messages and drafts?"):
        get_api().clear_cache()
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
