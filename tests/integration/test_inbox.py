"""Integration tests for inbox operations with Greenmail."""



class TestInbox:
    """Tests for inbox listing with real IMAP server."""

    def test_list_inbox(self, api_with_greenmail, populated_mailbox):
        """Test listing inbox returns emails."""
        result = api_with_greenmail.list_inbox(limit=10)

        assert result.count > 0
        assert len(result.conversations) > 0

    def test_list_inbox_fresh(self, api_with_greenmail, populated_mailbox):
        """Test fresh inbox fetch bypasses cache."""
        # First fetch
        api_with_greenmail.list_inbox(limit=10)

        # Fresh fetch
        result2 = api_with_greenmail.list_inbox(limit=10, fresh=True)

        assert result2.count > 0
        assert result2.from_cache is False

    def test_get_conversation(self, api_with_greenmail, populated_mailbox):
        """Test getting a specific conversation."""
        # First list to populate cache
        result = api_with_greenmail.list_inbox(limit=10)

        if result.conversations:
            conv_id = result.conversations[0].conv_id
            conv = api_with_greenmail.get_conversation(conv_id)

            assert conv is not None
            assert conv.conv_id == conv_id
            assert len(conv.messages) > 0

    def test_get_message(self, api_with_greenmail, populated_mailbox):
        """Test getting a specific message."""
        # First list to populate cache
        result = api_with_greenmail.list_inbox(limit=10)

        if result.conversations:
            conv = api_with_greenmail.get_conversation(result.conversations[0].conv_id)
            if conv and conv.messages:
                msg_id = conv.messages[0].message_id
                msg = api_with_greenmail.get_message(msg_id)

                assert msg is not None
                assert msg.message_id == msg_id
