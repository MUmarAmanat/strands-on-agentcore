"""Unit tests for the composition root (no AWS calls)."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from reminder_agent.agent import ReminderAgent
from reminder_agent.config import Config, Environment, SourceType
from reminder_agent.factory import (
    build_agent,
    build_guard,
    build_memory,
    build_model,
    create_source,
)
from reminder_agent.guardrail import BedrockTaskGuard, NullTaskGuard
from reminder_agent.run_memory import AgentCoreRunMemory, NullRunMemory
from reminder_agent.sheets_source import GoogleSheetsTaskSource

CONFIG = Config(
    environment=Environment.DEV,
    source_type=SourceType.GOOGLE_SHEETS,
    bedrock_model_id="amazon.nova-pro-v1:0",
    ses_sender="sender@example.com",
    ses_recipient="to@example.com",
    aws_region="test-region",
    sheet_id="sheet123",
    sheet_range="Tasks!A1:Z1000",
    sheet_secret_name="sheet-secret",
)


class TestBuildModel:
    """Bedrock model construction."""

    def test_uses_model_id_and_region(self):
        """Model ID and region come from config."""
        with patch("reminder_agent.factory.BedrockModel") as model_cls:
            build_model(CONFIG)

        kwargs = model_cls.call_args.kwargs
        assert kwargs["model_id"] == "amazon.nova-pro-v1:0"
        assert kwargs["region_name"] == "test-region"
        assert kwargs["boto_client_config"].read_timeout == 30

    def test_streaming_disabled(self):
        """Streaming is off, so only bedrock:InvokeModel is needed."""
        with patch("reminder_agent.factory.BedrockModel") as model_cls:
            build_model(CONFIG)

        assert model_cls.call_args.kwargs["streaming"] is False


class TestCreateSource:
    """Task source selection."""

    def test_google_sheets(self):
        """google_sheets source type gives a GoogleSheetsTaskSource."""
        source = create_source(CONFIG, secrets=MagicMock())
        assert isinstance(source, GoogleSheetsTaskSource)

    def test_unsupported_source_raises(self):
        """An unimplemented source type raises ValueError."""
        notion = replace(
            CONFIG,
            source_type=SourceType.NOTION,
            notion_database_id="db",
            notion_secret_name="secret",
        )
        with pytest.raises(ValueError):
            create_source(notion, secrets=MagicMock())


GUARDED = replace(CONFIG, guardrail_id="gr-1", guardrail_version="1")


class TestGuardrailWiring:
    """Guardrail on the model (layer 1) and on sheet text (layer 2)."""

    def test_model_without_guardrail(self):
        """No guardrail ID means no guardrail settings on the model."""
        with patch("reminder_agent.factory.BedrockModel") as model_cls:
            build_model(CONFIG)

        assert "guardrail_id" not in model_cls.call_args.kwargs

    def test_model_with_guardrail(self):
        """A guardrail ID puts ID, version and output redaction on the model."""
        with patch("reminder_agent.factory.BedrockModel") as model_cls:
            build_model(GUARDED)

        kwargs = model_cls.call_args.kwargs
        assert kwargs["guardrail_id"] == "gr-1"
        assert kwargs["guardrail_version"] == "1"
        assert kwargs["guardrail_redact_output"] is True

    def test_guard_null_without_id(self):
        """No guardrail ID means the no-op sheet guard."""
        assert isinstance(build_guard(CONFIG), NullTaskGuard)

    @patch("reminder_agent.factory.boto3")
    def test_guard_bedrock_with_id(self, boto3_mod):
        """A guardrail ID gives a Bedrock guard with a regional client."""
        guard = build_guard(GUARDED)

        assert isinstance(guard, BedrockTaskGuard)
        args, kwargs = boto3_mod.client.call_args
        assert args == ("bedrock-runtime",)
        assert kwargs["region_name"] == "test-region"


class TestBuildMemory:
    """Run memory selection."""

    def test_empty_id_gives_null_memory(self):
        """No memory ID means the no-op memory."""
        assert isinstance(build_memory(CONFIG), NullRunMemory)

    @patch("reminder_agent.factory.MemoryClient")
    def test_id_gives_agentcore_memory(self, client_cls):
        """A memory ID gives AgentCore memory with a regional client."""
        memory = build_memory(replace(CONFIG, memory_id="mem-1"))

        assert isinstance(memory, AgentCoreRunMemory)
        client_cls.assert_called_once_with(region_name="test-region")


class TestBuildAgent:
    """Full wiring."""

    @patch("reminder_agent.factory.build_model")
    @patch("reminder_agent.factory.SecretsManagerProvider")
    def test_uses_injected_notifier(self, _secrets, _model):
        """An injected notifier is used instead of SES."""
        notifier = MagicMock()
        with patch("reminder_agent.factory.build_notifier") as ses:
            agent = build_agent(CONFIG, notifier=notifier)

        assert isinstance(agent, ReminderAgent)
        assert agent._notifier is notifier
        ses.assert_not_called()

    @patch("reminder_agent.factory.build_model")
    @patch("reminder_agent.factory.SecretsManagerProvider")
    def test_defaults_to_ses(self, _secrets, _model):
        """Without an override, the SES notifier is built."""
        with patch("reminder_agent.factory.build_notifier") as ses:
            agent = build_agent(CONFIG)

        assert agent._notifier is ses.return_value
