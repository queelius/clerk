"""SMTP client for sending email."""

import asyncio
import time
from collections import deque
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

import aiosmtplib

from .cache import get_cache
from .config import AccountConfig, get_config
from .drafts import get_draft_manager
from .models import Draft, SendResult


class RateLimiter:
    """Simple rate limiter for send operations."""

    def __init__(self, max_per_hour: int = 20):
        self.max_per_hour = max_per_hour
        self.timestamps: deque[float] = deque()

    def can_send(self) -> bool:
        """Check if we can send another message."""
        now = time.time()
        hour_ago = now - 3600

        # Remove timestamps older than an hour
        while self.timestamps and self.timestamps[0] < hour_ago:
            self.timestamps.popleft()

        return len(self.timestamps) < self.max_per_hour

    def record_send(self) -> None:
        """Record a send operation."""
        self.timestamps.append(time.time())

    def remaining(self) -> int:
        """Get remaining sends allowed this hour."""
        now = time.time()
        hour_ago = now - 3600

        while self.timestamps and self.timestamps[0] < hour_ago:
            self.timestamps.popleft()

        return max(0, self.max_per_hour - len(self.timestamps))


# Global rate limiters per account
_rate_limiters: dict[str, RateLimiter] = {}


def get_rate_limiter(account: str) -> RateLimiter:
    """Get or create a rate limiter for an account."""
    config = get_config()
    if account not in _rate_limiters:
        _rate_limiters[account] = RateLimiter(config.send.rate_limit)
    return _rate_limiters[account]


class SmtpClient:
    """SMTP client for sending email."""

    def __init__(self, account_name: str, account_config: AccountConfig):
        self.account_name = account_name
        self.config = account_config

    def _create_message(self, draft: Draft) -> MIMEMultipart:
        """Create a MIME message from a draft."""
        msg = MIMEMultipart("alternative")

        # Set headers
        from_addr = self.config.from_
        msg["From"] = formataddr((from_addr.name, from_addr.address))

        msg["To"] = ", ".join(
            formataddr((a.name, a.addr)) for a in draft.to
        )

        if draft.cc:
            msg["Cc"] = ", ".join(
                formataddr((a.name, a.addr)) for a in draft.cc
            )

        msg["Subject"] = draft.subject
        msg["Date"] = formatdate(localtime=True)

        # Generate message ID
        domain = from_addr.address.split("@")[1]
        msg["Message-ID"] = make_msgid(domain=domain)

        # Threading headers
        if draft.in_reply_to:
            msg["In-Reply-To"] = draft.in_reply_to

        if draft.references:
            msg["References"] = " ".join(draft.references)

        # Attach body
        msg.attach(MIMEText(draft.body_text, "plain", "utf-8"))

        if draft.body_html:
            msg.attach(MIMEText(draft.body_html, "html", "utf-8"))

        return msg

    async def _send_async(self, draft: Draft) -> SendResult:
        """Send a draft message asynchronously."""
        # Create the message
        msg = self._create_message(draft)
        message_id = msg["Message-ID"]

        try:
            if self.config.protocol == "gmail":
                await self._send_gmail(msg)
            elif self.config.protocol == "microsoft365":
                await self._send_microsoft365(msg)
            else:
                await self._send_imap(msg)

            return SendResult(success=True, message_id=message_id)

        except aiosmtplib.SMTPAuthenticationError as e:
            return SendResult(success=False, error=f"Authentication failed: {e}")
        except aiosmtplib.SMTPException as e:
            return SendResult(success=False, error=f"SMTP error: {e}")
        except Exception as e:
            return SendResult(success=False, error=f"Failed to send: {e}")

    async def _send_imap(self, msg: MIMEMultipart) -> None:
        """Send via standard SMTP with password authentication."""
        smtp = self.config.smtp
        if not smtp:
            raise ValueError("SMTP not configured")

        password = self.config.get_password(self.account_name)

        await aiosmtplib.send(
            msg,
            hostname=smtp.host,
            port=smtp.port,
            username=smtp.username,
            password=password,
            start_tls=smtp.starttls,
        )

    async def _send_gmail(self, msg: MIMEMultipart) -> None:
        """Send via Gmail SMTP with OAuth2 authentication."""
        from .oauth import get_gmail_credentials, get_oauth2_string

        oauth_config = self.config.oauth
        if not oauth_config:
            raise ValueError("OAuth configuration required for Gmail")

        # Get credentials, refreshing if needed
        credentials = get_gmail_credentials(
            self.account_name,
            client_id_file=oauth_config.client_id_file,
        )

        email = self.config.from_.address
        oauth2_string = get_oauth2_string(email, credentials.token)  # type: ignore[arg-type]

        # Connect to Gmail SMTP with XOAUTH2
        smtp = aiosmtplib.SMTP(hostname="smtp.gmail.com", port=587, start_tls=True)
        await smtp.connect()

        # Authenticate with XOAUTH2 via raw SMTP command
        response = await smtp.execute_command(
            b"AUTH", b"XOAUTH2", oauth2_string.encode()
        )
        if response.code != 235:
            raise aiosmtplib.SMTPAuthenticationError(response.code, response.message)

        # Send the message
        await smtp.send_message(msg)
        await smtp.quit()

    async def _send_microsoft365(self, msg: MIMEMultipart) -> None:
        """Send via Microsoft 365 SMTP with XOAUTH2 authentication."""
        from .microsoft365 import get_m365_access_token
        from .oauth import get_oauth2_string

        access_token = get_m365_access_token(self.account_name)
        email_addr = self.config.from_.address
        oauth2_string = get_oauth2_string(email_addr, access_token)

        smtp = aiosmtplib.SMTP(hostname="smtp.office365.com", port=587, start_tls=True)
        await smtp.connect()

        # XOAUTH2 auth via raw SMTP command
        response = await smtp.execute_command(
            b"AUTH", b"XOAUTH2", oauth2_string.encode()
        )
        if response.code != 235:
            raise aiosmtplib.SMTPAuthenticationError(response.code, response.message)

        await smtp.send_message(msg)
        await smtp.quit()

    async def send_async(self, draft: Draft) -> SendResult:
        """Send a draft message (async)."""
        return await self._send_async(draft)

    def send(self, draft: Draft) -> SendResult:
        """Send a draft message (synchronous wrapper).

        Only works when no event loop is running.
        Use send_async() from within an async context.
        """
        return asyncio.run(self._send_async(draft))


def check_send_allowed(draft: Draft, account_name: str) -> tuple[bool, str | None]:
    """Check if sending is allowed based on safety rules.

    Returns (allowed, error_message).
    """
    config = get_config()

    # Check rate limit
    limiter = get_rate_limiter(account_name)
    if not limiter.can_send():
        return False, f"Rate limit exceeded. {limiter.remaining()} sends remaining this hour."

    # Check blocked recipients
    blocked = {addr.lower() for addr in config.send.blocked_recipients}
    for addr in draft.to + draft.cc + draft.bcc:
        if addr.addr.lower() in blocked:
            return False, f"Recipient {addr.addr} is blocked"

    # Check account matches draft
    if draft.account != account_name:
        return False, f"Draft account '{draft.account}' doesn't match '{account_name}'"

    return True, None


async def send_draft_async(
    draft_id: str,
    account_name: str | None = None,
) -> SendResult:
    """Send a draft by ID (async version for use within an event loop)."""
    config = get_config()
    cache = get_cache()
    manager = get_draft_manager()

    draft = manager.get(draft_id)
    if not draft:
        return SendResult(success=False, error=f"Draft not found: {draft_id}")

    if account_name is None:
        account_name = draft.account

    try:
        name, account_config = config.get_account(account_name)
    except ValueError as e:
        return SendResult(success=False, error=str(e))

    allowed, error = check_send_allowed(draft, name)
    if not allowed:
        return SendResult(success=False, error=error)

    client = SmtpClient(name, account_config)
    result = await client.send_async(draft)

    if result.success:
        limiter = get_rate_limiter(name)
        limiter.record_send()

        cache.log_send(
            account=name,
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            message_id=result.message_id,
        )

        manager.delete(draft_id)

    return result


def send_draft(
    draft_id: str,
    account_name: str | None = None,
    skip_confirmation: bool = False,
) -> SendResult:
    """Send a draft by ID (sync version, fails inside a running event loop)."""
    config = get_config()
    cache = get_cache()
    manager = get_draft_manager()

    draft = manager.get(draft_id)
    if not draft:
        return SendResult(success=False, error=f"Draft not found: {draft_id}")

    if account_name is None:
        account_name = draft.account

    try:
        name, account_config = config.get_account(account_name)
    except ValueError as e:
        return SendResult(success=False, error=str(e))

    allowed, error = check_send_allowed(draft, name)
    if not allowed:
        return SendResult(success=False, error=error)

    client = SmtpClient(name, account_config)
    result = client.send(draft)

    if result.success:
        # Record the send for rate limiting
        limiter = get_rate_limiter(name)
        limiter.record_send()

        # Log the send
        cache.log_send(
            account=name,
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            message_id=result.message_id,
        )

        # Delete the draft
        manager.delete(draft_id)

    return result


def format_draft_preview(draft: Draft) -> str:
    """Format a draft for preview before sending."""
    lines = []
    lines.append(f"From: {draft.account}")
    lines.append(f"To: {', '.join(str(a) for a in draft.to)}")

    if draft.cc:
        lines.append(f"Cc: {', '.join(str(a) for a in draft.cc)}")

    if draft.bcc:
        lines.append(f"Bcc: {', '.join(str(a) for a in draft.bcc)}")

    lines.append(f"Subject: {draft.subject}")
    lines.append("")
    lines.append(draft.body_text)

    return "\n".join(lines)
