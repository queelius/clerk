"""Draft management for clerk."""

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .cache import get_cache
from .config import get_config, get_data_dir
from .models import Address, Draft


def generate_draft_id() -> str:
    """Generate a unique draft ID."""
    return f"draft_{secrets.token_hex(8)}"


class DraftManager:
    """Manages draft messages."""

    def __init__(self):
        self.cache = get_cache()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure drafts table exists (handled by cache.py)."""
        pass  # Schema is created by Cache class

    def create(
        self,
        account: str,
        to: list[Address],
        subject: str,
        body_text: str,
        cc: list[Address] | None = None,
        bcc: list[Address] | None = None,
        body_html: str | None = None,
        reply_to_conv_id: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> Draft:
        """Create a new draft."""
        draft_id = generate_draft_id()
        now = datetime.now(timezone.utc)

        draft = Draft(
            draft_id=draft_id,
            account=account,
            to=to,
            cc=cc or [],
            bcc=bcc or [],
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to_conv_id=reply_to_conv_id,
            in_reply_to=in_reply_to,
            references=references or [],
            created_at=now,
            updated_at=now,
        )

        self._save(draft)
        return draft

    def _save(self, draft: Draft) -> None:
        """Save a draft to the database."""
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drafts (
                    draft_id, account,
                    to_json, cc_json, bcc_json,
                    subject, body_text, body_html,
                    reply_to_conv_id, in_reply_to, references_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.account,
                    json.dumps([a.model_dump() for a in draft.to]),
                    json.dumps([a.model_dump() for a in draft.cc]),
                    json.dumps([a.model_dump() for a in draft.bcc]),
                    draft.subject,
                    draft.body_text,
                    draft.body_html,
                    draft.reply_to_conv_id,
                    draft.in_reply_to,
                    json.dumps(draft.references),
                    draft.created_at.isoformat(),
                    draft.updated_at.isoformat(),
                ),
            )

    def get(self, draft_id: str) -> Draft | None:
        """Get a draft by ID."""
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()

            if not row:
                return None

            return Draft(
                draft_id=row["draft_id"],
                account=row["account"],
                to=[Address(**a) for a in json.loads(row["to_json"])],
                cc=[Address(**a) for a in json.loads(row["cc_json"])],
                bcc=[Address(**a) for a in json.loads(row["bcc_json"])],
                subject=row["subject"],
                body_text=row["body_text"],
                body_html=row["body_html"],
                reply_to_conv_id=row["reply_to_conv_id"],
                in_reply_to=row["in_reply_to"],
                references=json.loads(row["references_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def list(self, account: str | None = None) -> list[Draft]:
        """List all drafts, optionally filtered by account."""
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            if account:
                rows = conn.execute(
                    "SELECT * FROM drafts WHERE account = ? ORDER BY updated_at DESC",
                    (account,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM drafts ORDER BY updated_at DESC"
                ).fetchall()

            drafts = []
            for row in rows:
                drafts.append(
                    Draft(
                        draft_id=row["draft_id"],
                        account=row["account"],
                        to=[Address(**a) for a in json.loads(row["to_json"])],
                        cc=[Address(**a) for a in json.loads(row["cc_json"])],
                        bcc=[Address(**a) for a in json.loads(row["bcc_json"])],
                        subject=row["subject"],
                        body_text=row["body_text"],
                        body_html=row["body_html"],
                        reply_to_conv_id=row["reply_to_conv_id"],
                        in_reply_to=row["in_reply_to"],
                        references=json.loads(row["references_json"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                    )
                )

            return drafts

    def update(self, draft: Draft) -> None:
        """Update an existing draft."""
        draft.updated_at = datetime.now(timezone.utc)
        self._save(draft)

    def delete(self, draft_id: str) -> bool:
        """Delete a draft. Returns True if deleted, False if not found."""
        db_path = get_data_dir() / "cache.db"
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM drafts WHERE draft_id = ?", (draft_id,)
            )
            return cursor.rowcount > 0

    def create_reply(
        self,
        account: str,
        conv_id: str,
        body_text: str,
        body_html: str | None = None,
        reply_all: bool = False,
    ) -> Draft:
        """Create a reply draft based on a conversation."""
        # Get the conversation from cache
        conv = self.cache.get_conversation(conv_id)
        if not conv:
            raise ValueError(f"Conversation not found: {conv_id}")

        if not conv.messages:
            raise ValueError(f"Conversation has no messages: {conv_id}")

        # Get the latest message to reply to
        latest = conv.messages[-1]

        # Build recipient list
        config = get_config()
        _, account_config = config.get_account(account)
        my_addr = account_config.from_.address.lower()

        # Reply to the sender
        to = [latest.from_]

        # For reply-all, add original recipients except ourselves
        cc: list[Address] = []
        if reply_all:
            for addr in latest.to + latest.cc:
                if addr.addr.lower() != my_addr and addr not in to:
                    cc.append(addr)

        # Build subject
        subject = latest.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # Build references chain
        references = latest.references.copy()
        if latest.message_id and latest.message_id not in references:
            references.append(latest.message_id)

        return self.create(
            account=account,
            to=to,
            cc=cc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to_conv_id=conv_id,
            in_reply_to=latest.message_id,
            references=references,
        )


# Global instance
_draft_manager: DraftManager | None = None


def get_draft_manager() -> DraftManager:
    """Get or create the global draft manager."""
    global _draft_manager
    if _draft_manager is None:
        _draft_manager = DraftManager()
    return _draft_manager
