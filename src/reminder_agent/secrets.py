"""AWS Secrets Manager access.

Wraps secret retrieval behind a small interface so callers depend on an
abstraction (dependency injection friendly) and secrets are fetched in one
place. Secret values are never logged.
"""

from __future__ import annotations

import abc

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .logging_config import get_logger
from .retry import retry

_LOGGER = get_logger(__name__)


class SecretsError(RuntimeError):
    """Raised when a secret cannot be retrieved."""


class SecretsProvider(abc.ABC):
    """Abstract provider of secret strings."""

    @abc.abstractmethod
    def get_secret(self, name: str) -> str:
        """Return the raw secret string for ``name``."""
        raise NotImplementedError


class SecretsManagerProvider(SecretsProvider):
    """Secrets provider backed by AWS Secrets Manager."""

    def __init__(self, region: str, client: object | None = None) -> None:
        """Initialise the provider.

        Args:
            region: AWS region of the Secrets Manager endpoint.
            client: Optional pre-built boto3 client (for injection/testing).
        """
        self._client = client or boto3.client("secretsmanager", region_name=region)

    @retry((BotoCoreError, ClientError), max_attempts=3)
    def get_secret(self, name: str) -> str:
        """Fetch a secret value by name.

        Args:
            name: The Secrets Manager secret name.

        Returns:
            The secret string.

        Raises:
            SecretsError: If the secret is missing or has no string value.
        """
        try:
            response = self._client.get_secret_value(SecretId=name)
        except (BotoCoreError, ClientError) as exc:
            _LOGGER.error("Secret retrieval failed", extra={"secret_name": name})
            raise SecretsError(f"Could not retrieve secret '{name}'") from exc

        secret = response.get("SecretString")
        if not secret:
            raise SecretsError(f"Secret '{name}' has no string value")
        return secret
