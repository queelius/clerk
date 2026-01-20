"""Fixtures for integration tests with Greenmail."""

import socket
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from smtplib import SMTP

import pytest

from clerk.api import ClerkAPI
from clerk.cache import Cache
from clerk.config import AccountConfig, ClerkConfig, FromAddress, ImapConfig, SmtpConfig
from clerk.drafts import DraftManager

# Greenmail test server config
GREENMAIL_HOST = "localhost"
GREENMAIL_IMAP_PORT = 3143
GREENMAIL_SMTP_PORT = 3025
GREENMAIL_USER = "test"
GREENMAIL_PASSWORD = "password"
GREENMAIL_EMAIL = "test@localhost"


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for a port to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (socket.error, socket.timeout):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def greenmail_server():
    """Ensure Greenmail server is available.

    Assumes docker-compose.test.yml has been started.
    """
    if not wait_for_port(GREENMAIL_HOST, GREENMAIL_IMAP_PORT, timeout=5):
        pytest.skip("Greenmail server not available. Run: docker-compose -f docker-compose.test.yml up -d")

    if not wait_for_port(GREENMAIL_HOST, GREENMAIL_SMTP_PORT, timeout=5):
        pytest.skip("Greenmail SMTP not available")

    return {
        "host": GREENMAIL_HOST,
        "imap_port": GREENMAIL_IMAP_PORT,
        "smtp_port": GREENMAIL_SMTP_PORT,
        "user": GREENMAIL_USER,
        "password": GREENMAIL_PASSWORD,
        "email": GREENMAIL_EMAIL,
    }


@pytest.fixture
def test_config(greenmail_server, tmp_path):
    """Create a test configuration for Greenmail."""
    return ClerkConfig(
        accounts={
            "test": AccountConfig(
                protocol="imap",
                imap=ImapConfig(
                    host=greenmail_server["host"],
                    port=greenmail_server["imap_port"],
                    username=greenmail_server["user"],
                    ssl=False,
                ),
                smtp=SmtpConfig(
                    host=greenmail_server["host"],
                    port=greenmail_server["smtp_port"],
                    username=greenmail_server["user"],
                    starttls=False,
                    ssl=False,
                ),
                **{"from": FromAddress(
                    address=greenmail_server["email"],
                    name="Test User",
                )},
            ),
        },
        default_account="test",
    )


@pytest.fixture
def test_cache(tmp_path):
    """Create a test cache."""
    return Cache(tmp_path / "cache.db")


@pytest.fixture
def test_draft_manager(tmp_path, test_cache, monkeypatch):
    """Create a test draft manager."""
    monkeypatch.setattr("clerk.drafts.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("clerk.drafts.get_cache", lambda: test_cache)
    return DraftManager()


@pytest.fixture
def api_with_greenmail(test_config, test_cache, test_draft_manager, monkeypatch):
    """Create a ClerkAPI configured for Greenmail."""
    # Mock password retrieval
    monkeypatch.setattr(
        "clerk.config.AccountConfig.get_password",
        lambda self, name: GREENMAIL_PASSWORD,
    )

    return ClerkAPI(
        config=test_config,
        cache=test_cache,
        draft_manager=test_draft_manager,
    )


def send_test_email(
    greenmail_server: dict,
    to: str,
    subject: str,
    body: str,
    from_addr: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    """Send a test email directly to Greenmail via SMTP.

    Args:
        greenmail_server: Server config dict
        to: Recipient address
        subject: Subject line
        body: Message body
        from_addr: Sender address (optional)
        attachments: List of (filename, content, mime_type) tuples

    Returns:
        Message-ID of sent message
    """
    msg = EmailMessage()
    msg["From"] = from_addr or f"sender@{greenmail_server['host']}"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.set_content(body)

    # Add attachments
    if attachments:
        for filename, content, mime_type in attachments:
            maintype, subtype = mime_type.split("/")
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

    # Send via SMTP
    with SMTP(greenmail_server["host"], greenmail_server["smtp_port"]) as smtp:
        smtp.send_message(msg)

    return msg["Message-ID"]


@pytest.fixture
def populated_mailbox(greenmail_server):
    """Populate the test mailbox with sample emails."""
    messages = []

    # Send a few test emails
    for i in range(3):
        msg_id = send_test_email(
            greenmail_server,
            to=greenmail_server["email"],
            subject=f"Test Email {i + 1}",
            body=f"This is test email number {i + 1}.\n\nIt has some content.",
            from_addr=f"sender{i}@example.com",
        )
        messages.append(msg_id)

    # Send one with attachment
    msg_id = send_test_email(
        greenmail_server,
        to=greenmail_server["email"],
        subject="Email with Attachment",
        body="This email has an attachment.",
        from_addr="sender@example.com",
        attachments=[
            ("test.txt", b"Hello, this is a test file!", "text/plain"),
        ],
    )
    messages.append(msg_id)

    # Allow time for messages to be processed
    time.sleep(0.5)

    return messages
