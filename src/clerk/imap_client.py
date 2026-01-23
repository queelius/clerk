"""IMAP client for fetching email."""

import email
import email.header
import email.utils
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.message import Message as EmailMessage
from typing import Any

from imapclient import IMAPClient

from .config import AccountConfig, get_config
from .models import Address, Attachment, FolderInfo, Message, MessageFlag, UnreadCounts


def decode_header_value(value: str | bytes | None) -> str:
    """Decode an email header value."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    # Decode RFC 2047 encoded words
    decoded_parts = []
    for part, charset in email.header.decode_header(value):
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                decoded_parts.append(part.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(part)

    return " ".join(decoded_parts)


def parse_address(addr_tuple: tuple | None) -> Address | None:
    """Parse an address tuple from email.utils.parseaddr."""
    if not addr_tuple or not addr_tuple[1]:
        return None
    name, addr = addr_tuple
    return Address(addr=addr, name=decode_header_value(name))


def parse_address_list(header: str | None) -> list[Address]:
    """Parse a comma-separated address header."""
    if not header:
        return []
    addresses = []
    for addr_str in header.split(","):
        addr_str = addr_str.strip()
        if addr_str:
            parsed = parse_address(email.utils.parseaddr(addr_str))
            if parsed:
                addresses.append(parsed)
    return addresses


def extract_body(msg: EmailMessage) -> tuple[str | None, str | None]:
    """Extract text and HTML body from email message."""
    text_body = None
    html_body = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disp = str(part.get("Content-Disposition", ""))

            # Skip attachments
            if "attachment" in content_disp:
                continue

            if content_type == "text/plain" and text_body is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        text_body = payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        text_body = payload.decode("utf-8", errors="replace")

            elif content_type == "text/html" and html_body is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_body = payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        html_body = payload.decode("utf-8", errors="replace")
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                text_body = decoded
            elif content_type == "text/html":
                html_body = decoded

    return text_body, html_body


def extract_attachments(msg: EmailMessage) -> list[Attachment]:
    """Extract attachment metadata from email message."""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_disp = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disp:
                filename = part.get_filename()
                if filename:
                    filename = decode_header_value(filename)
                else:
                    filename = "unnamed"

                payload = part.get_payload(decode=True)
                size = len(payload) if payload else 0

                attachments.append(
                    Attachment(
                        filename=filename,
                        size=size,
                        content_type=part.get_content_type(),
                    )
                )

    return attachments


def imap_flags_to_model(flags: tuple) -> list[MessageFlag]:
    """Convert IMAP flags to model flags."""
    result = []
    flag_map = {
        b"\\Seen": MessageFlag.SEEN,
        b"\\Answered": MessageFlag.ANSWERED,
        b"\\Flagged": MessageFlag.FLAGGED,
        b"\\Deleted": MessageFlag.DELETED,
        b"\\Draft": MessageFlag.DRAFT,
        "\\Seen": MessageFlag.SEEN,
        "\\Answered": MessageFlag.ANSWERED,
        "\\Flagged": MessageFlag.FLAGGED,
        "\\Deleted": MessageFlag.DELETED,
        "\\Draft": MessageFlag.DRAFT,
    }
    for flag in flags:
        if flag in flag_map:
            result.append(flag_map[flag])
    return result


def model_flags_to_imap(flags: list[MessageFlag]) -> list[str]:
    """Convert model flags to IMAP flags."""
    flag_map = {
        MessageFlag.SEEN: "\\Seen",
        MessageFlag.ANSWERED: "\\Answered",
        MessageFlag.FLAGGED: "\\Flagged",
        MessageFlag.DELETED: "\\Deleted",
        MessageFlag.DRAFT: "\\Draft",
    }
    return [flag_map[f] for f in flags if f in flag_map]


def compute_conv_id(message_id: str, references: list[str], in_reply_to: str | None) -> str:
    """Compute conversation ID from threading headers.

    Uses the root message ID (first in references chain, or in_reply_to, or self).
    """
    # Find the root message ID
    if references:
        root = references[0]
    elif in_reply_to:
        root = in_reply_to
    else:
        root = message_id

    # Hash it for a stable, compact ID
    return hashlib.sha256(root.encode()).hexdigest()[:12]


class ImapClient:
    """IMAP client for a single account."""

    def __init__(self, account_name: str, account_config: AccountConfig):
        self.account_name = account_name
        self.config = account_config
        self._client: IMAPClient | None = None

    def connect(self) -> None:
        """Connect to the IMAP server."""
        if self._client is not None:
            return

        if self.config.protocol == "gmail":
            self._connect_gmail()
        else:
            self._connect_imap()

    def _connect_imap(self) -> None:
        """Connect using standard IMAP with password authentication."""
        imap = self.config.imap
        if not imap:
            raise ValueError("IMAP configuration required")

        self._client = IMAPClient(imap.host, port=imap.port, ssl=imap.ssl)

        password = self.config.get_password(self.account_name)
        self._client.login(imap.username, password)

    def _connect_gmail(self) -> None:
        """Connect to Gmail using OAuth2 XOAUTH2 authentication."""
        from .oauth import get_gmail_credentials, get_oauth2_string

        oauth_config = self.config.oauth
        if not oauth_config:
            raise ValueError("OAuth configuration required for Gmail")

        # Get credentials, refreshing if needed
        credentials = get_gmail_credentials(
            self.account_name,
            client_id_file=oauth_config.client_id_file,
        )

        # Connect to Gmail IMAP
        self._client = IMAPClient("imap.gmail.com", port=993, ssl=True)

        # Authenticate with XOAUTH2
        email = self.config.from_.address
        self._client.oauth2_login(email, credentials.token)

    def disconnect(self) -> None:
        """Disconnect from the IMAP server."""
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()

    @property
    def client(self) -> IMAPClient:
        """Get the connected IMAP client."""
        if self._client is None:
            raise RuntimeError("Not connected to IMAP server")
        return self._client

    def list_folders(self) -> list[FolderInfo]:
        """List all folders/labels."""
        folders = []
        for flags, delimiter, name in self.client.list_folders():
            # Decode folder name if needed
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            if isinstance(delimiter, bytes):
                delimiter = delimiter.decode("utf-8")

            folders.append(
                FolderInfo(
                    name=name,
                    flags=[f.decode() if isinstance(f, bytes) else f for f in flags],
                    delimiter=delimiter or "/",
                )
            )
        return folders

    def get_folder_status(self, folder: str) -> tuple[int, int]:
        """Get message count and unread count for a folder."""
        status = self.client.folder_status(folder, ["MESSAGES", "UNSEEN"])
        return status.get(b"MESSAGES", 0), status.get(b"UNSEEN", 0)

    def get_unread_counts(self) -> UnreadCounts:
        """Get unread counts for all folders."""
        folders: dict[str, int] = {}
        total = 0

        for folder_info in self.list_folders():
            # Skip special folders
            if "\\Noselect" in folder_info.flags:
                continue

            try:
                _, unread = self.get_folder_status(folder_info.name)
                if unread > 0:
                    folders[folder_info.name] = unread
                    total += unread
            except Exception:
                # Some folders may not be accessible
                pass

        return UnreadCounts(account=self.account_name, folders=folders, total=total)

    def fetch_messages(
        self,
        folder: str = "INBOX",
        limit: int = 50,
        since: datetime | None = None,
        unread_only: bool = False,
        fetch_bodies: bool = False,
    ) -> list[Message]:
        """Fetch messages from a folder."""
        self.client.select_folder(folder, readonly=True)

        # Build search criteria
        criteria: list[Any] = ["ALL"]
        if since:
            criteria = ["SINCE", since.date()]
        if unread_only:
            if criteria == ["ALL"]:
                criteria = ["UNSEEN"]
            else:
                criteria.append("UNSEEN")

        # Search for messages
        message_ids = self.client.search(criteria)

        # Get the most recent messages
        message_ids = sorted(message_ids, reverse=True)[:limit]

        if not message_ids:
            return []

        # Determine what to fetch
        fetch_items = ["FLAGS", "ENVELOPE", "INTERNALDATE", "RFC822.SIZE"]
        if fetch_bodies:
            fetch_items.append("BODY.PEEK[]")
        else:
            fetch_items.append("BODY.PEEK[HEADER]")

        # Fetch message data
        fetch_data = self.client.fetch(message_ids, fetch_items)

        messages = []
        now = datetime.now(timezone.utc)

        for uid, data in fetch_data.items():
            try:
                msg = self._parse_message(uid, data, folder, fetch_bodies, now)
                if msg:
                    messages.append(msg)
            except Exception as e:
                # Log but continue with other messages
                print(f"Warning: Failed to parse message {uid}: {e}")

        return messages

    def _parse_message(
        self,
        uid: int,
        data: dict,
        folder: str,
        has_body: bool,
        fetch_time: datetime,
    ) -> Message | None:
        """Parse a single message from IMAP fetch data."""
        envelope = data.get(b"ENVELOPE")
        if not envelope:
            return None

        flags = imap_flags_to_model(data.get(b"FLAGS", ()))

        # Parse envelope
        date = envelope.date
        if date:
            date = date.replace(tzinfo=None) if date.tzinfo else date
        else:
            date = data.get(b"INTERNALDATE", datetime.now(timezone.utc))

        subject = decode_header_value(envelope.subject) if envelope.subject else ""

        # Parse from address
        from_addr = None
        if envelope.from_:
            env_from = envelope.from_[0]
            mailbox = env_from.mailbox.decode() if env_from.mailbox else ""
            host = env_from.host.decode() if env_from.host else ""
            name = decode_header_value(env_from.name) if env_from.name else ""
            if mailbox and host:
                from_addr = Address(addr=f"{mailbox}@{host}", name=name)

        if not from_addr:
            from_addr = Address(addr="unknown@unknown", name="")

        # Parse message ID
        message_id = envelope.message_id
        if message_id:
            message_id = message_id.decode() if isinstance(message_id, bytes) else message_id
        else:
            message_id = f"<{uid}@local>"

        # Parse In-Reply-To and References from headers
        in_reply_to = None
        references: list[str] = []

        if has_body:
            raw = data.get(b"BODY[]") or data.get(b"RFC822")
        else:
            raw = data.get(b"BODY[HEADER]")

        body_text = None
        body_html = None
        attachments: list[Attachment] = []
        to_addrs: list[Address] = []
        cc_addrs: list[Address] = []
        reply_to_addrs: list[Address] = []

        if raw:
            email_msg = email.message_from_bytes(raw)

            # Parse threading headers
            in_reply_to_header = email_msg.get("In-Reply-To", "")
            if in_reply_to_header:
                in_reply_to = in_reply_to_header.strip()

            references_header = email_msg.get("References", "")
            if references_header:
                # References is space-separated message IDs
                references = [r.strip() for r in references_header.split() if r.strip()]

            # Parse addresses from headers (more reliable than envelope)
            to_addrs = parse_address_list(email_msg.get("To"))
            cc_addrs = parse_address_list(email_msg.get("Cc"))
            reply_to_addrs = parse_address_list(email_msg.get("Reply-To"))

            if has_body:
                body_text, body_html = extract_body(email_msg)
                attachments = extract_attachments(email_msg)

        # Compute conversation ID
        conv_id = compute_conv_id(message_id, references, in_reply_to)

        return Message(
            message_id=message_id,
            conv_id=conv_id,
            folder=folder,
            account=self.account_name,
            **{"from": from_addr},
            to=to_addrs,
            cc=cc_addrs,
            reply_to=reply_to_addrs,
            date=date,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            flags=flags,
            in_reply_to=in_reply_to,
            references=references,
            headers_fetched_at=fetch_time,
            body_fetched_at=fetch_time if has_body else None,
        )

    def fetch_message_body(self, folder: str, message_id: str) -> tuple[str | None, str | None]:
        """Fetch just the body of a specific message."""
        self.client.select_folder(folder, readonly=True)

        # Check if this is a synthetic message_id (e.g., <123@local>)
        import re
        synthetic_match = re.match(r"<(\d+)@local>", message_id)
        if synthetic_match:
            # Fetch directly by UID
            uid = int(synthetic_match.group(1))
            results = [uid]
        else:
            # Search for the message by Message-ID header
            results = self.client.search(["HEADER", "Message-ID", message_id])

        if not results:
            return None, None

        uid = results[0]
        fetch_data = self.client.fetch([uid], ["BODY.PEEK[]"])

        if uid not in fetch_data:
            return None, None

        raw = fetch_data[uid].get(b"BODY[]")
        if not raw:
            return None, None

        email_msg = email.message_from_bytes(raw)
        return extract_body(email_msg)

    def fetch_attachment(self, folder: str, message_id: str, filename: str) -> bytes:
        """Fetch a specific attachment from a message.

        Args:
            folder: Folder containing the message
            message_id: Message ID
            filename: Attachment filename to fetch

        Returns:
            Attachment content as bytes

        Raises:
            FileNotFoundError: If message or attachment not found
        """
        self.client.select_folder(folder, readonly=True)

        # Check if this is a synthetic message_id (e.g., <123@local>)
        import re
        synthetic_match = re.match(r"<(\d+)@local>", message_id)
        if synthetic_match:
            # Fetch directly by UID
            results = [int(synthetic_match.group(1))]
        else:
            # Search for the message by Message-ID header
            results = self.client.search(["HEADER", "Message-ID", message_id])

        if not results:
            raise FileNotFoundError(f"Message not found: {message_id}")

        uid = results[0]
        fetch_data = self.client.fetch([uid], ["BODY.PEEK[]"])

        if uid not in fetch_data:
            raise FileNotFoundError(f"Message not found: {message_id}")

        raw = fetch_data[uid].get(b"BODY[]")
        if not raw:
            raise FileNotFoundError(f"Message body not found: {message_id}")

        email_msg = email.message_from_bytes(raw)

        # Find the attachment by filename
        for part in email_msg.walk():
            content_disp = str(part.get("Content-Disposition", ""))
            if "attachment" not in content_disp:
                continue

            part_filename = part.get_filename()
            if part_filename:
                # Decode filename if needed
                decoded_filename = decode_header_value(part_filename)
                if decoded_filename == filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload

        raise FileNotFoundError(f"Attachment not found: {filename}")

    def set_flags(self, folder: str, message_id: str, flags: list[MessageFlag]) -> None:
        """Set flags on a message."""
        self.client.select_folder(folder)

        results = self.client.search(["HEADER", "Message-ID", message_id])
        if not results:
            raise ValueError(f"Message not found: {message_id}")

        uid = results[0]
        imap_flags = model_flags_to_imap(flags)
        self.client.set_flags([uid], imap_flags)

    def add_flags(self, folder: str, message_id: str, flags: list[MessageFlag]) -> None:
        """Add flags to a message."""
        self.client.select_folder(folder)

        results = self.client.search(["HEADER", "Message-ID", message_id])
        if not results:
            raise ValueError(f"Message not found: {message_id}")

        uid = results[0]
        imap_flags = model_flags_to_imap(flags)
        self.client.add_flags([uid], imap_flags)

    def remove_flags(self, folder: str, message_id: str, flags: list[MessageFlag]) -> None:
        """Remove flags from a message."""
        self.client.select_folder(folder)

        results = self.client.search(["HEADER", "Message-ID", message_id])
        if not results:
            raise ValueError(f"Message not found: {message_id}")

        uid = results[0]
        imap_flags = model_flags_to_imap(flags)
        self.client.remove_flags([uid], imap_flags)

    def move_message(self, message_id: str, from_folder: str, to_folder: str) -> None:
        """Move a message to another folder."""
        self.client.select_folder(from_folder)

        results = self.client.search(["HEADER", "Message-ID", message_id])
        if not results:
            raise ValueError(f"Message not found: {message_id}")

        uid = results[0]

        # Copy to destination
        self.client.copy([uid], to_folder)

        # Mark as deleted in source
        self.client.add_flags([uid], ["\\Deleted"])
        self.client.expunge()

    def archive_message(self, message_id: str, from_folder: str = "INBOX") -> None:
        """Archive a message (move to Archive folder or All Mail for Gmail)."""
        # Try common archive folder names
        folders = self.list_folders()
        folder_names = [f.name for f in folders]

        archive_folder = None
        for name in ["Archive", "[Gmail]/All Mail", "All Mail", "Archives"]:
            if name in folder_names:
                archive_folder = name
                break

        if not archive_folder:
            raise ValueError("Could not find archive folder")

        self.move_message(message_id, from_folder, archive_folder)


def get_imap_client(account_name: str | None = None) -> ImapClient:
    """Get an IMAP client for the specified or default account."""
    config = get_config()
    name, account_config = config.get_account(account_name)
    return ImapClient(name, account_config)
