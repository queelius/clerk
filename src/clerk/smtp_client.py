"""Pure SMTP transport for clerk.

This module knows how to turn a ``Draft`` into MIME and ship it to an SMTP
server. It does not know about the cache, the draft store, or the rate
limiter — those concerns live in ``api.ClerkAPI`` and route through here.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import ClassVar

import aiosmtplib

from .config import AccountConfig
from .models import Draft, SendResult


class SmtpClient:
    """SMTP transport for a single account."""

    # Hostnames for OAuth-authenticated SMTP providers.
    _XOAUTH2_HOSTS: ClassVar[dict[str, str]] = {
        "gmail": "smtp.gmail.com",
        "microsoft365": "smtp.office365.com",
    }

    def __init__(self, account_name: str, account_config: AccountConfig):
        self.account_name = account_name
        self.config = account_config

    def _create_message(self, draft: Draft) -> MIMEMultipart:
        """Create a MIME message from a draft."""
        msg = MIMEMultipart("alternative")

        from_addr = self.config.from_
        msg["From"] = formataddr((from_addr.name, from_addr.address))
        msg["To"] = ", ".join(formataddr((a.name, a.addr)) for a in draft.to)
        if draft.cc:
            msg["Cc"] = ", ".join(formataddr((a.name, a.addr)) for a in draft.cc)

        msg["Subject"] = draft.subject
        msg["Date"] = formatdate(localtime=True)

        domain = from_addr.address.split("@")[1]
        msg["Message-ID"] = make_msgid(domain=domain)

        if draft.in_reply_to:
            msg["In-Reply-To"] = draft.in_reply_to
        if draft.references:
            msg["References"] = " ".join(draft.references)

        msg.attach(MIMEText(draft.body_text, "plain", "utf-8"))
        if draft.body_html:
            msg.attach(MIMEText(draft.body_html, "html", "utf-8"))

        return msg

    async def _send_async(self, draft: Draft) -> SendResult:
        """Dispatch to the right transport and wrap exceptions."""
        msg = self._create_message(draft)
        message_id = msg["Message-ID"]

        try:
            if self.config.protocol in self._XOAUTH2_HOSTS:
                await self._send_xoauth2(msg)
            else:
                await self._send_imap(msg)
            return SendResult(success=True, message_id=message_id)

        except aiosmtplib.SMTPAuthenticationError:
            # Do not include exception details: some SMTP servers echo
            # credentials in error payloads. Canned message only.
            return SendResult(success=False, error="Authentication failed")
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

    def _get_xoauth2_token(self) -> str:
        """Fetch an access token for the configured OAuth protocol."""
        if self.config.protocol == "gmail":
            from .oauth import get_gmail_credentials

            oauth_config = self.config.oauth
            if not oauth_config:
                raise ValueError("OAuth configuration required for Gmail")
            credentials = get_gmail_credentials(
                self.account_name,
                client_id_file=oauth_config.client_id_file,
            )
            token = credentials.token
            if not token:
                raise ValueError("Gmail credentials have no access token — re-authenticate")
            return str(token)
        if self.config.protocol == "microsoft365":
            from .microsoft365 import get_m365_access_token

            return get_m365_access_token(self.account_name)
        raise ValueError(f"Unsupported OAuth protocol: {self.config.protocol}")

    async def _send_xoauth2(self, msg: MIMEMultipart) -> None:
        """Send via SMTP using XOAUTH2 (Gmail and M365 share this path)."""
        from .oauth import get_oauth2_string

        host = self._XOAUTH2_HOSTS[self.config.protocol]
        token = self._get_xoauth2_token()
        oauth2_string = get_oauth2_string(self.config.from_.address, token)

        smtp = aiosmtplib.SMTP(hostname=host, port=587, start_tls=True)
        await smtp.connect()
        response = await smtp.execute_command(
            b"AUTH", b"XOAUTH2", oauth2_string.encode()
        )
        if response.code != 235:
            raise aiosmtplib.SMTPAuthenticationError(response.code, response.message)

        await smtp.send_message(msg)
        await smtp.quit()

    async def send_async(self, draft: Draft) -> SendResult:
        """Send a draft message (pure transport; no side effects)."""
        return await self._send_async(draft)
