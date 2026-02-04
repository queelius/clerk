"""Integration tests for sending emails with Greenmail."""




class TestSend:
    """Tests for sending emails with real SMTP server."""

    def test_create_and_list_draft(self, api_with_greenmail):
        """Test creating and listing drafts."""
        # Create a draft
        draft = api_with_greenmail.create_draft(
            to=["recipient@example.com"],
            subject="Test Draft",
            body="This is a test draft.",
            account="test",
        )

        assert draft is not None
        assert draft.subject == "Test Draft"

        # List drafts
        drafts = api_with_greenmail.list_drafts(account="test")
        assert any(d.draft_id == draft.draft_id for d in drafts)

    def test_create_draft_with_cc_bcc(self, api_with_greenmail):
        """Test creating draft with CC and BCC."""
        draft = api_with_greenmail.create_draft(
            to=["recipient@example.com"],
            subject="Test with CC/BCC",
            body="Testing CC and BCC fields.",
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            account="test",
        )

        assert draft is not None
        assert draft.cc == ["cc@example.com"]
        assert draft.bcc == ["bcc@example.com"]

    def test_delete_draft(self, api_with_greenmail):
        """Test deleting a draft."""
        # Create a draft
        draft = api_with_greenmail.create_draft(
            to=["recipient@example.com"],
            subject="Draft to Delete",
            body="This draft will be deleted.",
            account="test",
        )

        # Delete it
        result = api_with_greenmail.delete_draft(draft.draft_id)
        assert result is True

        # Verify it's gone
        drafts = api_with_greenmail.list_drafts(account="test")
        assert not any(d.draft_id == draft.draft_id for d in drafts)

    def test_send_draft(self, api_with_greenmail, greenmail_server):
        """Test sending a draft via SMTP."""
        # Create a draft to send to ourselves
        draft = api_with_greenmail.create_draft(
            to=[greenmail_server["email"]],
            subject="Test Send",
            body="This is a sent message.",
            account="test",
        )

        # Send the draft (skip confirmation for testing)
        result = api_with_greenmail.send_draft(
            draft_id=draft.draft_id,
            skip_confirmation=True,
        )

        assert result.success is True
        assert result.message_id is not None

    def test_send_draft_requires_confirmation(self, api_with_greenmail, greenmail_server):
        """Test that sending without skip_confirmation returns a token."""
        draft = api_with_greenmail.create_draft(
            to=[greenmail_server["email"]],
            subject="Needs Confirmation",
            body="This needs confirmation.",
            account="test",
        )

        # First call returns a confirmation token
        result = api_with_greenmail.send_draft(
            draft_id=draft.draft_id,
            skip_confirmation=False,
        )

        # Should get a confirmation token, not success yet
        assert result.requires_confirmation is True
        assert result.confirm_token is not None

        # Now confirm with the token
        result2 = api_with_greenmail.send_draft(
            draft_id=draft.draft_id,
            skip_confirmation=False,
            confirm_token=result.confirm_token,
        )

        assert result2.success is True

    def test_update_draft(self, api_with_greenmail):
        """Test updating an existing draft."""
        # Create initial draft
        draft = api_with_greenmail.create_draft(
            to=["original@example.com"],
            subject="Original Subject",
            body="Original body.",
            account="test",
        )

        # Update it
        updated = api_with_greenmail.update_draft(
            draft_id=draft.draft_id,
            subject="Updated Subject",
            body="Updated body.",
        )

        assert updated is not None
        assert updated.subject == "Updated Subject"
        assert updated.body == "Updated body."
