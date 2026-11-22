"""Tests for the SES email notifier."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from reminder_agent.notifier import NotificationError, SesNotifier


def _make_notifier(client):
    return SesNotifier(
        sender="sender@example.com",
        recipient="to@example.com",
        region="us-east-1",
        client=client,
    )


class TestSesNotifier:
    def test_sends_email_and_returns_message_id(self):
        client = MagicMock()
        client.send_email.return_value = {"MessageId": "abc-123"}

        message_id = _make_notifier(client).send("Subject", "Body")

        assert message_id == "abc-123"
        client.send_email.assert_called_once()
        kwargs = client.send_email.call_args.kwargs
        assert kwargs["Source"] == "sender@example.com"
        assert kwargs["Destination"] == {"ToAddresses": ["to@example.com"]}
        assert kwargs["Message"]["Subject"]["Data"] == "Subject"
        assert kwargs["Message"]["Body"]["Text"]["Data"] == "Body"

    def test_raises_notification_error_on_client_error(self):
        client = MagicMock()
        client.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email not verified"}},
            "SendEmail",
        )

        with pytest.raises(NotificationError):
            _make_notifier(client).send("Subject", "Body")

    def test_retries_then_succeeds(self):
        client = MagicMock()
        client.send_email.side_effect = [
            ClientError(
                {"Error": {"Code": "Throttling", "Message": "slow down"}},
                "SendEmail",
            ),
            {"MessageId": "ok-after-retry"},
        ]

        message_id = _make_notifier(client).send("Subject", "Body")

        assert message_id == "ok-after-retry"
        assert client.send_email.call_count == 2
