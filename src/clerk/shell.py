"""Interactive shell/REPL for clerk.

Provides a readline-like experience using prompt_toolkit with:
- Command history (persisted)
- Tab completion for commands and options
- All CLI commands available
- Extra: sql command for raw queries
"""

import json
import shlex
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from . import __version__
from .api import ClerkAPI, get_api
from .config import ensure_dirs, get_data_dir
from .models import MessageFlag


# Shell styling
STYLE = Style.from_dict({
    "prompt": "bold #00aa00",
    "error": "#ff0000",
    "success": "#00ff00",
    "info": "#0088ff",
    "warning": "#ffaa00",
})


# Available shell commands
COMMANDS = {
    "help": "Show available commands",
    "inbox": "List recent conversations [--limit N] [--unread]",
    "show": "Show conversation/message <id>",
    "search": "Search messages <query> [--limit N] [--advanced]",
    "sql": "Execute raw SQL query <query>",
    "drafts": "List pending drafts",
    "draft": "Show draft <id>",
    "folders": "List folders",
    "unread": "Show unread counts",
    "status": "Show connection status",
    "cache": "Show cache statistics",
    "refresh": "Refresh cache from server",
    "clear": "Clear screen",
    "exit": "Exit the shell",
    "quit": "Exit the shell",
}


class ClerkCompleter(Completer):
    """Tab completion for shell commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        if not words or (len(words) == 1 and not text.endswith(" ")):
            # Complete command name
            word = words[0] if words else ""
            for cmd in COMMANDS:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
        elif len(words) >= 1:
            # Complete options based on command
            cmd = words[0]
            current = words[-1] if not text.endswith(" ") else ""

            options = []
            if cmd in ("inbox", "search"):
                options = ["--limit", "--unread", "--advanced", "--account"]
            elif cmd in ("show", "draft"):
                options = ["--json"]
            elif cmd == "drafts":
                options = ["--account"]

            for opt in options:
                if opt.startswith(current):
                    yield Completion(opt, start_position=-len(current))


def format_conversations(conversations: list[Any], api: ClerkAPI) -> str:
    """Format conversation list for display."""
    if not conversations:
        return "No conversations found."

    lines = []
    lines.append(f"{'ID':<12} {'From':<25} {'Subject':<40} {'Date':<10}")
    lines.append("-" * 90)

    for conv in conversations:
        conv_id = conv.conv_id[:12]
        participants = conv.participants
        # Get primary participant (not self)
        from_str = participants[0] if participants else ""
        if len(from_str) > 25:
            from_str = from_str[:22] + "..."

        subject = conv.subject
        if len(subject) > 40:
            subject = subject[:37] + "..."

        # Add unread indicator
        if conv.unread_count > 0:
            subject = f"* {subject}"

        date_str = conv.latest_date.strftime("%b %d")

        lines.append(f"{conv_id:<12} {from_str:<25} {subject:<40} {date_str:<10}")

    return "\n".join(lines)


def format_messages(messages: list[Any]) -> str:
    """Format message list for display."""
    if not messages:
        return "No messages found."

    lines = []
    lines.append(f"{'Conv ID':<12} {'From':<25} {'Subject':<40} {'Date':<10}")
    lines.append("-" * 90)

    for msg in messages:
        conv_id = msg.conv_id[:12]
        from_str = msg.from_.addr[:25] if msg.from_ else ""

        subject = msg.subject or ""
        if len(subject) > 40:
            subject = subject[:37] + "..."

        date_str = msg.date.strftime("%b %d") if msg.date else ""

        lines.append(f"{conv_id:<12} {from_str:<25} {subject:<40} {date_str:<10}")

    return "\n".join(lines)


def format_conversation_detail(conv: Any) -> str:
    """Format a conversation with all messages."""
    lines = []
    lines.append(f"Subject: {conv.subject}")
    lines.append(f"Participants: {', '.join(conv.participants)}")
    lines.append(f"Messages: {conv.message_count}")
    lines.append("")

    for i, msg in enumerate(conv.messages, 1):
        lines.append(f"--- Message {i} ---")
        lines.append(f"From: {msg.from_}")
        lines.append(f"Date: {msg.date}")
        lines.append("")
        lines.append(msg.body_text or "(no body)")
        lines.append("")

    return "\n".join(lines)


def format_drafts(drafts: list[Any]) -> str:
    """Format draft list for display."""
    if not drafts:
        return "No drafts."

    lines = []
    lines.append(f"{'ID':<20} {'To':<30} {'Subject':<35}")
    lines.append("-" * 90)

    for draft in drafts:
        draft_id = draft.draft_id[:20]
        to_str = ", ".join(a.addr for a in draft.to)[:30]
        subject = draft.subject[:35]

        lines.append(f"{draft_id:<20} {to_str:<30} {subject:<35}")

    return "\n".join(lines)


class ClerkShell:
    """Interactive shell for clerk."""

    def __init__(self):
        ensure_dirs()
        self.api = get_api()

        # Set up history file
        history_dir = get_data_dir()
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / "shell_history"

        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=ClerkCompleter(),
            style=STYLE,
        )

        # Command handlers
        self.handlers: dict[str, Callable[[list[str]], str | None]] = {
            "help": self.cmd_help,
            "inbox": self.cmd_inbox,
            "show": self.cmd_show,
            "search": self.cmd_search,
            "sql": self.cmd_sql,
            "drafts": self.cmd_drafts,
            "draft": self.cmd_draft,
            "folders": self.cmd_folders,
            "unread": self.cmd_unread,
            "status": self.cmd_status,
            "cache": self.cmd_cache,
            "refresh": self.cmd_refresh,
            "clear": self.cmd_clear,
            "exit": self.cmd_exit,
            "quit": self.cmd_exit,
        }

    def run(self) -> None:
        """Run the interactive shell."""
        print(f"Clerk v{__version__} - Interactive Shell")
        print("Type 'help' for available commands, 'exit' to quit.\n")

        while True:
            try:
                text = self.session.prompt("clerk> ")
                if not text.strip():
                    continue

                result = self.execute(text.strip())
                if result:
                    print(result)

            except KeyboardInterrupt:
                print("\nUse 'exit' or Ctrl-D to quit.")
            except EOFError:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}")

    def execute(self, text: str) -> str | None:
        """Execute a shell command.

        Returns output string or None.
        """
        try:
            parts = shlex.split(text)
        except ValueError as e:
            return f"Parse error: {e}"

        if not parts:
            return None

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in self.handlers:
            return self.handlers[cmd](args)
        else:
            return f"Unknown command: {cmd}. Type 'help' for available commands."

    def _parse_options(self, args: list[str], valid_options: dict[str, bool]) -> tuple[dict[str, Any], list[str]]:
        """Parse command line options.

        Args:
            args: Command arguments
            valid_options: Dict of option name -> takes_value

        Returns:
            (options dict, remaining positional args)
        """
        options: dict[str, Any] = {}
        positional: list[str] = []
        i = 0

        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                opt_name = arg[2:]
                if opt_name in valid_options:
                    if valid_options[opt_name]:
                        # Takes a value
                        if i + 1 < len(args):
                            options[opt_name] = args[i + 1]
                            i += 2
                            continue
                    else:
                        # Flag only
                        options[opt_name] = True
                        i += 1
                        continue
            positional.append(arg)
            i += 1

        return options, positional

    # Command handlers

    def cmd_help(self, args: list[str]) -> str:
        """Show help."""
        lines = ["Available commands:", ""]
        for cmd, desc in sorted(COMMANDS.items()):
            lines.append(f"  {cmd:<12} {desc}")
        return "\n".join(lines)

    def cmd_inbox(self, args: list[str]) -> str:
        """List inbox conversations."""
        options, _ = self._parse_options(args, {
            "limit": True,
            "unread": False,
            "account": True,
        })

        limit = int(options.get("limit", 20))
        unread = options.get("unread", False)
        account = options.get("account")

        result = self.api.list_inbox(
            account=account,
            limit=limit,
            unread_only=unread,
        )

        return format_conversations(result.conversations, self.api)

    def cmd_show(self, args: list[str]) -> str:
        """Show a conversation or message."""
        options, positional = self._parse_options(args, {"json": False})

        if not positional:
            return "Usage: show <conversation-id or message-id>"

        conv_id = positional[0]

        # Try as conversation first
        conv = self.api.get_conversation(conv_id)
        if conv:
            if options.get("json"):
                return json.dumps(conv.model_dump(), default=str, indent=2)
            return format_conversation_detail(conv)

        # Try as message
        msg = self.api.get_message(conv_id)
        if msg:
            if options.get("json"):
                return json.dumps(msg.model_dump(), default=str, indent=2)
            lines = [
                f"From: {msg.from_}",
                f"To: {', '.join(str(a) for a in msg.to)}",
                f"Date: {msg.date}",
                f"Subject: {msg.subject}",
                "",
                msg.body_text or "(no body)",
            ]
            return "\n".join(lines)

        return f"Not found: {conv_id}"

    def cmd_search(self, args: list[str]) -> str:
        """Search messages."""
        options, positional = self._parse_options(args, {
            "limit": True,
            "account": True,
            "advanced": False,
        })

        if not positional:
            return "Usage: search <query> [--limit N] [--advanced]"

        query = " ".join(positional)
        limit = int(options.get("limit", 20))
        account = options.get("account")
        advanced = options.get("advanced", False)

        if advanced:
            result = self.api.search_advanced(query, account=account, limit=limit)
        else:
            result = self.api.search(query, account=account, limit=limit)

        return format_messages(result.messages)

    def cmd_sql(self, args: list[str]) -> str:
        """Execute raw SQL query."""
        if not args:
            return "Usage: sql <SELECT query>"

        query = " ".join(args)

        try:
            messages = self.api.search_sql(query)
            return format_messages(messages)
        except ValueError as e:
            return f"Error: {e}"

    def cmd_drafts(self, args: list[str]) -> str:
        """List drafts."""
        options, _ = self._parse_options(args, {"account": True})
        account = options.get("account")

        drafts = self.api.list_drafts(account=account)
        return format_drafts(drafts)

    def cmd_draft(self, args: list[str]) -> str:
        """Show a draft."""
        options, positional = self._parse_options(args, {"json": False})

        if not positional:
            return "Usage: draft <draft-id>"

        draft_id = positional[0]
        draft = self.api.get_draft(draft_id)

        if not draft:
            return f"Draft not found: {draft_id}"

        if options.get("json"):
            return json.dumps(draft.model_dump(), default=str, indent=2)

        lines = [
            f"Draft ID: {draft.draft_id}",
            f"Account: {draft.account}",
            f"To: {', '.join(str(a) for a in draft.to)}",
            f"Subject: {draft.subject}",
            f"Created: {draft.created_at}",
            "",
            "Body:",
            draft.body_text or "(empty)",
        ]
        return "\n".join(lines)

    def cmd_folders(self, args: list[str]) -> str:
        """List folders."""
        options, _ = self._parse_options(args, {"account": True})
        account = options.get("account")

        folders = self.api.list_folders(account=account)

        lines = []
        for folder in folders:
            flags = " ".join(f"[{f}]" for f in folder.flags)
            lines.append(f"{folder.name} {flags}")

        return "\n".join(lines) if lines else "No folders."

    def cmd_unread(self, args: list[str]) -> str:
        """Show unread counts."""
        options, _ = self._parse_options(args, {"account": True})
        account = options.get("account")

        counts = self.api.get_unread_counts(account=account)

        if counts.total == 0:
            return "No unread messages."

        lines = [f"Total unread: {counts.total}"]
        for folder, count in sorted(counts.folders.items()):
            lines.append(f"  {folder}: {count}")

        return "\n".join(lines)

    def cmd_status(self, args: list[str]) -> str:
        """Show status."""
        status = self.api.get_status()

        lines = [f"Clerk v{status['version']}", ""]

        for name, info in status["accounts"].items():
            if info["connected"]:
                lines.append(f"  [OK] {name} - {info['folders']} folders")
            else:
                lines.append(f"  [ERR] {name} - {info.get('error', 'Unknown error')}")

        return "\n".join(lines)

    def cmd_cache(self, args: list[str]) -> str:
        """Show cache statistics."""
        stats = self.api.get_cache_stats()

        lines = [
            f"Messages: {stats.message_count}",
            f"Conversations: {stats.conversation_count}",
        ]

        if stats.oldest_message:
            lines.append(f"Oldest: {stats.oldest_message.strftime('%Y-%m-%d')}")
        if stats.newest_message:
            lines.append(f"Newest: {stats.newest_message.strftime('%Y-%m-%d')}")

        size_mb = stats.cache_size_bytes / (1024 * 1024)
        lines.append(f"Size: {size_mb:.2f} MB")

        if stats.last_sync:
            lines.append(f"Last sync: {stats.last_sync.strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(lines)

    def cmd_refresh(self, args: list[str]) -> str:
        """Refresh cache."""
        options, _ = self._parse_options(args, {"account": True})
        account = options.get("account")

        count = self.api.refresh_cache(account=account)
        return f"Refreshed {count} messages."

    def cmd_clear(self, args: list[str]) -> str:
        """Clear screen."""
        print("\033[2J\033[H", end="")
        return ""

    def cmd_exit(self, args: list[str]) -> str:
        """Exit shell."""
        print("Goodbye!")
        raise EOFError()


def run_shell() -> None:
    """Entry point for the shell."""
    shell = ClerkShell()
    shell.run()
