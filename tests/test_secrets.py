"""Tests for the AWS Secrets Manager provider."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from reminder_agent.secrets import SecretsError, SecretsManagerProvider


def _make_provider(client):
    return SecretsManagerProvider(region="us-east-1", client=client)


class TestSecretsManagerProvider:
    def test_returns_secret_string(self):
        client = MagicMock()
        client.get_secret_value.return_value = {"SecretString": "my-key"}

        assert _make_provider(client).get_secret("name") == "my-key"
        client.get_secret_value.assert_called_once_with(SecretId="name")

    def test_raises_when_secret_missing(self):
        client = MagicMock()
        client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
            "GetSecretValue",
        )

        with pytest.raises(SecretsError):
            _make_provider(client).get_secret("missing")

    def test_raises_when_no_string_value(self):
        client = MagicMock()
        client.get_secret_value.return_value = {}

        with pytest.raises(SecretsError):
            _make_provider(client).get_secret("binary-only")
