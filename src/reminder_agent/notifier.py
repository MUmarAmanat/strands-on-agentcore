"""Email delivery behind a notifier interface.

:class:`Notifier` is the abstraction; the SES implementation sends mail
from a verified sender. Keeping this behind an interface means the channel
(SES today, something else later) can change without touching the agent.
"""

from __future__ import annotations

import abc

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .logging_config import get_logger
from .retry import retry

_LOGGER = get_logger(__name__)


class NotificationError(RuntimeError):
    """Raised when a notification cannot be delivered."""


class Notifier(abc.ABC):
    """Abstract notifier."""

    @abc.abstractmethod
    def send(self, subject: str, body: str) -> str:
        """Send a message and return a provider message id."""
        raise NotImplementedError


class SesNotifier(Notifier):
    """Notifier backed by AWS SES."""

    def __init__(
        self,
        sender: str,
        recipient: str,
        region: str,
        client: object | None = None,
    ) -> None:
        """Initialise the notifier.

        Args:
            sender: Verified SES sender address.
            recipient: Recipient address.
            region: AWS region of the SES endpoint.
            client: Optional pre-built boto3 client (for injection/testing).
        """
        self._sender = sender
        self._recipient = recipient
        self._client = client or boto3.client("ses", region_name=region)

    # Retry on NotificationError: send() wraps boto errors in it, so
    # retrying on BotoCoreError/ClientError would never trigger.
    @retry((NotificationError,), max_attempts=3)
    def send(self, subject: str, body: str) -> str:
        """Send a plain-text email.

        Args:
            subject: Email subject line.
            body: Plain-text body.

        Returns:
            The SES message id.

        Raises:
            NotificationError: If sending fails after retries.
        """
        try:
            response = self._client.send_email(
                Source=self._sender,
                Destination={"ToAddresses": [self._recipient]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )
        except (BotoCoreError, ClientError) as exc:
            _LOGGER.error("Email send failed", extra={"error": str(exc)})
            raise NotificationError("Failed to send reminder email") from exc

        message_id = response.get("MessageId", "")
        _LOGGER.info("Email sent", extra={"message_id": message_id})
        return message_id
