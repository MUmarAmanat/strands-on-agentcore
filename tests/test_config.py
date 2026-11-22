"""Tests for environment-based configuration validation."""

import pytest

from reminder_agent.config import Config, ConfigError, Environment, SourceType


def _valid_kwargs(**overrides):
    """Return a valid set of Config kwargs, with optional overrides."""
    base = dict(
        environment=Environment.DEV,
        source_type=SourceType.GOOGLE_SHEETS,
        bedrock_model_id="amazon.nova-pro-v1:0",
        ses_sender="sender@example.com",
        ses_recipient="to@example.com",
        aws_region="test-region",
        sheet_id="sheet123",
        sheet_range="Tasks!A1:Z1000",
        sheet_secret_name="sheet-service-account",
    )
    base.update(overrides)
    return base


class TestConfigValidation:
    """Validation rules enforced by Config.__post_init__."""

    def test_valid_config_succeeds(self):
        """A complete, well-formed config is accepted."""
        config = Config(**_valid_kwargs())
        assert config.bedrock_model_id == "amazon.nova-pro-v1:0"

    def test_missing_model_id_raises(self):
        """An empty Bedrock model ID is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(bedrock_model_id=""))

    def test_invalid_sender_email_raises(self):
        """A sender without '@' is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(ses_sender="not-an-email"))

    def test_invalid_recipient_email_raises(self):
        """A recipient without '@' is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(ses_recipient="nope"))

    def test_google_sheets_requires_sheet_fields(self):
        """The Google Sheets source needs a sheet ID."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(sheet_id=""))

    def test_notion_requires_notion_fields(self):
        """The Notion source needs a database ID and secret name."""
        with pytest.raises(ConfigError):
            Config(
                **_valid_kwargs(
                    source_type=SourceType.NOTION,
                    sheet_id="",
                    sheet_range="",
                    sheet_secret_name="",
                )
            )

    def test_non_positive_timeout_raises(self):
        """A zero timeout is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(llm_timeout_seconds=0))

    def test_negative_retries_raises(self):
        """A negative retry count is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(max_retries=-1))

    def test_timezone_defaults_to_utc(self):
        """Without a timezone, UTC is used."""
        assert Config(**_valid_kwargs()).timezone == "UTC"

    def test_unknown_timezone_raises(self):
        """A misspelled timezone is rejected at load time."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(timezone="Asia/Dubay"))

    def test_guardrail_id_without_version_raises(self):
        """A guardrail ID alone is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(guardrail_id="gr-1"))

    def test_guardrail_version_without_id_raises(self):
        """A guardrail version alone is rejected."""
        with pytest.raises(ConfigError):
            Config(**_valid_kwargs(guardrail_version="1"))

    def test_from_env_builds_config(self, monkeypatch):
        """from_env reads all values, including BEDROCK_MODEL_ID."""
        env = {
            "ENVIRONMENT": "dev",
            "SOURCE_TYPE": "google_sheets",
            "BEDROCK_MODEL_ID": "amazon.nova-pro-v1:0",
            "SES_SENDER": "sender@example.com",
            "SES_RECIPIENT": "to@example.com",
            "AWS_REGION": "test-region",
            "GOOGLE_SHEET_ID": "sheet123",
            "GOOGLE_SHEET_RANGE": "Tasks!A1:Z1000",
            "SHEET_SECRET_NAME": "reminder-agent/sheet-service-account",
            "TIMEZONE": "Asia/Dubai",
            "MEMORY_ID": "mem-1",
            "GUARDRAIL_ID": "gr-1",
            "GUARDRAIL_VERSION": "1",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        config = Config.from_env()
        assert config.bedrock_model_id == "amazon.nova-pro-v1:0"
        assert config.source_type is SourceType.GOOGLE_SHEETS
        assert config.timezone == "Asia/Dubai"
        assert config.memory_id == "mem-1"
        assert config.guardrail_id == "gr-1"
        assert config.guardrail_version == "1"
