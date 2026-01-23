#!/usr/bin/env python3
"""Send test emails to populate the demo mailbox."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import make_msgid
from datetime import datetime, timedelta

SMTP_HOST = "localhost"
SMTP_PORT = 3025

# Store message IDs for threading
message_ids: dict[str, str] = {}


def send_email(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list = None,
    in_reply_to: str = None,
    references: list[str] = None,
    message_id_key: str = None,
    hours_ago: int = 0,
):
    """Send a test email with optional threading support."""
    if attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain"))
        for filename, content, content_type in attachments:
            if content_type.startswith("text/"):
                part = MIMEBase("text", content_type.split("/")[1])
            else:
                main_type, sub_type = content_type.split("/")
                part = MIMEBase(main_type, sub_type)
            part.set_payload(content.encode() if isinstance(content, str) else content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)
    else:
        msg = MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    # Generate and store message ID
    msg_id = make_msgid()
    msg["Message-ID"] = msg_id
    if message_id_key:
        message_ids[message_id_key] = msg_id

    # Set threading headers
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)

    # Set date (offset for realistic ordering)
    date = datetime.now() - timedelta(hours=hours_ago)
    msg["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S +0000")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.send_message(msg)
    print(f"  Sent: {subject[:50]}...")

    return msg_id


def main():
    print("Sending test emails...")

    # ========================================================================
    # Basic Emails (1-3)
    # ========================================================================

    # Email 1: Simple message from Alice
    send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="Welcome to the team!",
        body="""Hi there,

Welcome to the team! I wanted to reach out and say hello.

Let me know if you need anything to get started.

Best,
Alice""",
        message_id_key="welcome",
        hours_ago=48,
    )

    # Email 2: Project update from Bob (thread starter)
    q1_id = send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Q1 Project Update",
        body="""Hi,

Here's the Q1 project update:

- Phase 1: Complete
- Phase 2: In progress (80%)
- Phase 3: Starting next week

We're on track for the deadline. Let me know if you have questions.

Thanks,
Bob""",
        message_id_key="q1_update",
        hours_ago=36,
    )

    # Email 3: Meeting request (thread starter)
    meeting_id = send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="Meeting tomorrow?",
        body="""Hey,

Are you free tomorrow at 2pm for a quick sync? I wanted to discuss
the roadmap for next quarter.

Let me know!
Alice""",
        message_id_key="meeting",
        hours_ago=30,
    )

    # ========================================================================
    # Emails with Attachments (4, 18)
    # ========================================================================

    # Email 4: Email with attachment
    send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Report attached",
        body="""Hi,

Please find the weekly report attached.

Best,
Bob""",
        attachments=[
            ("weekly_report.txt", "Weekly Report\n==============\n\nMetrics:\n- Users: 1,234\n- Revenue: $50,000\n- Growth: 15%\n", "text/plain")
        ],
        hours_ago=28,
    )

    # ========================================================================
    # Urgent Email (5)
    # ========================================================================

    # Email 5: Urgent flag-worthy email
    send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="URGENT: Server issue",
        body="""Hi,

The production server is showing high CPU usage. Can you take a look?

- Server: prod-web-01
- CPU: 95%
- Started: 10 minutes ago

Thanks,
Alice""",
        hours_ago=24,
    )

    # ========================================================================
    # Newsletter & Automated Emails (6, 10)
    # ========================================================================

    # Email 6: Newsletter-style
    send_email(
        from_addr="newsletter@example.com",
        to_addr="demo@example.com",
        subject="Weekly Digest - Jan 2025",
        body="""Weekly Digest
=============

Top Stories:
1. New feature released
2. Team offsite planned for February
3. Q4 results exceed expectations

That's all for this week!

- The Newsletter Team""",
        hours_ago=22,
    )

    # ========================================================================
    # Thread Replies (7, 9, 14)
    # ========================================================================

    # Email 7: First reply in Q1 thread
    q1_reply1_id = send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Re: Q1 Project Update",
        body="""Following up on the project update.

Do you have any concerns about the Phase 2 timeline?

Bob""",
        in_reply_to=q1_id,
        references=[q1_id],
        message_id_key="q1_reply1",
        hours_ago=20,
    )

    # ========================================================================
    # New Senders (8, 12, 15, 16)
    # ========================================================================

    # Email 8: Invoice from Charlie
    send_email(
        from_addr="charlie@example.com",
        to_addr="demo@example.com",
        subject="Invoice #12345",
        body="""Hi,

Please find Invoice #12345 attached for services rendered in December.

Amount Due: $3,500.00
Due Date: January 31, 2025
Payment Terms: Net 30

Please remit payment via bank transfer to the account on file.

Thanks,
Charlie
Acme Consulting LLC""",
        hours_ago=18,
    )

    # Email 9: Meeting reply (proper threading)
    send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="Re: Meeting tomorrow?",
        body="""Perfect, let's do 2pm. I've sent a calendar invite.

Looking forward to it!
Alice""",
        in_reply_to=meeting_id,
        references=[meeting_id],
        hours_ago=16,
    )

    # Email 10: Another newsletter
    send_email(
        from_addr="newsletter@example.com",
        to_addr="demo@example.com",
        subject="Weekly Digest - Feb 2025",
        body="""Weekly Digest
=============

This Week's Highlights:
1. Q4 financial results published
2. New VP of Engineering announced
3. Remote work policy updated
4. Benefits enrollment deadline extended

Don't forget: Town hall meeting this Friday at 3pm!

- The Newsletter Team""",
        hours_ago=14,
    )

    # Email 11: Different topic from Bob
    send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Vacation request",
        body="""Hey,

I'm planning to take some time off next month. Would Feb 15-22 work?

Let me know if there are any conflicts with the project timeline.

Thanks,
Bob""",
        hours_ago=12,
    )

    # Email 12: Support ticket style
    send_email(
        from_addr="support@example.com",
        to_addr="demo@example.com",
        subject="Your ticket #1001 has been updated",
        body="""Your support ticket has been updated.

Ticket #: 1001
Status: In Progress
Priority: Medium

Latest update from Support Team:
"We've identified the issue and are working on a fix.
Expected resolution within 24-48 hours."

You can reply to this email to add more information to your ticket.

---
Example Support Team
support.example.com""",
        hours_ago=10,
    )

    # Email 13: FYI-style email
    send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="FYI: Policy changes",
        body="""Hey,

Just a heads up - there are some new security policies going into effect
next week:

- Password rotation: every 90 days
- VPN required for remote access
- Two-factor authentication mandatory

Full details in the wiki. Let me know if you have questions.

Alice""",
        hours_ago=8,
    )

    # Email 14: Deeper thread (third message in Q1 thread)
    send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Re: Re: Q1 Project Update",
        body="""Actually, I just talked to the team and we might be able
to finish Phase 2 early if we can get additional resources.

Should we schedule a call to discuss?

Bob""",
        in_reply_to=q1_reply1_id,
        references=[q1_id, q1_reply1_id],
        hours_ago=6,
    )

    # Email 15: HR-style
    send_email(
        from_addr="hr@example.com",
        to_addr="demo@example.com",
        subject="Important: Benefits enrollment deadline",
        body="""Dear Employee,

This is a reminder that the annual benefits enrollment period ends on
January 31, 2025.

Action Required:
- Review your current selections
- Make any changes via the HR portal
- Confirm your elections by end of day Jan 31

If you have questions, please contact hr@example.com or visit the
benefits FAQ in the employee handbook.

Best regards,
Human Resources""",
        hours_ago=5,
    )

    # Email 16: Leadership comms
    send_email(
        from_addr="ceo@example.com",
        to_addr="demo@example.com",
        subject="Company update",
        body="""Team,

I wanted to share some exciting news about our company's progress.

Key Highlights:
- Revenue grew 25% YoY
- We added 50 new customers this quarter
- Customer satisfaction scores are at an all-time high

Thank you all for your hard work and dedication. None of this would
be possible without your contributions.

Looking forward to an even better Q2!

Best,
Jane Smith
CEO""",
        hours_ago=4,
    )

    # Email 17: Short email
    send_email(
        from_addr="alice@example.com",
        to_addr="demo@example.com",
        subject="Quick question",
        body="""Hey, are you around for a 5-min chat? Need your input on something.

-A""",
        hours_ago=2,
    )

    # Email 18: With spreadsheet attachment
    # Create a minimal mock XLSX file (just headers to simulate)
    xlsx_mock = b"PK\x03\x04MOCK_XLSX_CONTENT_FOR_DEMO"  # Not a real xlsx, but works for testing
    send_email(
        from_addr="bob@example.com",
        to_addr="demo@example.com",
        subject="Budget spreadsheet",
        body="""Hi,

Attached is the Q1 budget spreadsheet for your review.

Key items:
- Engineering: $500K
- Marketing: $200K
- Operations: $150K
- Contingency: $50K

Total: $900K

Let me know if you have any questions.

Bob""",
        attachments=[
            ("q1_budget.xlsx", xlsx_mock, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        ],
        hours_ago=1,
    )

    print(f"\nDone! 18 test emails sent to demo@example.com")
    print(f"  - 3 emails in the Q1 Project thread")
    print(f"  - 2 emails in the Meeting thread")
    print(f"  - 2 emails with attachments")
    print(f"  - 6 different senders")


if __name__ == "__main__":
    main()
