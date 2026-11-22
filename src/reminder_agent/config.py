"""Environment-based configuration loading and validation.

All runtime values come from the environment (no hardcoded values).
The :class:`Config` dataclass validates inputs on creation so the
function fails fast with a clear error rather than midway through a run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .logging_config import get_logger

_LOGGER = get_logger(__name__)


class Environment(str, Enum):
    """Supported deployment environments."""

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class SourceType(str, Enum):
    """Supported task source backends."""

    GOOGLE_SHEETS = "google_sheets"
    NOTION = "notion"


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration.

    Attributes:
        environment: Deployment environment (dev/staging/prod).
        source_type: Which task source backend to use.
        bedrock_model_id: Amazon Bedrock model ID (e.g.
            ``amazon.nova-pro-v1:0``). Auth is via IAM, so no API key.
        ses_sender: Verified SES sender address.
        ses_recipient: Reminder recipient address.
        aws_region: AWS region for Secrets Manager and SES.
        llm_timeout_seconds: Hard timeout for a single LLM call.
        max_retries: Maximum retry attempts for transient failures.
        timezone: IANA timezone used to decide "today" (e.g.
            ``Asia/Dubai``). Defaults to ``UTC``.
        memory_id: AgentCore Memory resource ID (set by Terraform). Empty
            disables run memory (no duplicate-send check).
        guardrail_id: Bedrock Guardrail ID (set by Terraform). Empty
            disables guardrails. Must be set together with the version.
        guardrail_version: Published guardrail version (e.g. ``1``).
        sheet_id: Google Sheet identifier (required when source_type=google_sheets).
        sheet_range: A1 notation range (required when source_type=google_sheets).
        sheet_secret_name: Secrets Manager name for service-account JSON
            (required when source_type=google_sheets).
        notion_database_id: Notion database identifier
            (required when source_type=notion).
        notion_secret_name: Secrets Manager name for Notion API key
            (required when source_type=notion).
    """

    environment: Environment
    source_type: SourceType
    bedrock_model_id: str
    ses_sender: str
    ses_recipient: str
    aws_region: str
    llm_timeout_seconds: int = 30
    max_retries: int = 3
    timezone: str = "UTC"
    memory_id: str = ""
    guardrail_id: str = ""
    guardrail_version: str = ""
    sheet_id: str = ""
    sheet_range: str = ""
    sheet_secret_name: str = ""
    notion_database_id: str = ""
    notion_secret_name: str = ""
    _REQUIRED: tuple[str, ...] = field(
        default=(
            "bedrock_model_id",
            "ses_sender",
            "ses_recipient",
            "aws_region",
        ),
        repr=False,
    )

    def __post_init__(self) -> None:
        """Validate required fields are present and well-formed.

        Raises:
            ConfigError: If a required value is empty or a numeric bound
                is non-positive.
        """
        for name in self._REQUIRED:
            if not getattr(self, name):
                raise ConfigError(f"Missing required config value: {name}")

        if self.source_type is SourceType.GOOGLE_SHEETS:
            for name in ("sheet_id", "sheet_range", "sheet_secret_name"):
                if not getattr(self, name):
                    raise ConfigError(
                        f"Missing required config value for google_sheets source: {name}"
                    )
        elif self.source_type is SourceType.NOTION:
            for name in ("notion_database_id", "notion_secret_name"):
                if not getattr(self, name):
                    raise ConfigError(
                        f"Missing required config value for notion source: {name}"
                    )
        if "@" not in self.ses_sender or "@" not in self.ses_recipient:
            raise ConfigError("SES sender and recipient must be valid emails")

        if self.llm_timeout_seconds <= 0:
            raise ConfigError("llm_timeout_seconds must be positive")

        if self.max_retries < 0:
            raise ConfigError("max_retries must be zero or greater")

        # An ID without a version (or vice versa) would silently disable it.
        if bool(self.guardrail_id) != bool(self.guardrail_version):
            raise ConfigError(
                "guardrail_id and guardrail_version must be set together"
            )

        # Fail fast on a typo like "Asia/Dubay" instead of at run time.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"Unknown timezone: {self.timezone}") from exc

    @classmethod
    def from_env(cls) -> "Config":
        """Build a :class:`Config` from environment variables.

        Returns:
            A validated configuration instance.

        Raises:
            ConfigError: If required variables are missing or invalid.
        """
        try:
            return cls(
                environment=Environment(os.environ.get("ENVIRONMENT", "dev")),
                source_type=SourceType(os.environ.get("SOURCE_TYPE", "google_sheets")),
                bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", ""),
                ses_sender=os.environ.get("SES_SENDER", ""),
                ses_recipient=os.environ.get("SES_RECIPIENT", ""),
                aws_region=os.environ.get("AWS_REGION", ""),
                llm_timeout_seconds=int(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
                max_retries=int(os.environ.get("MAX_RETRIES", "3")),
                timezone=os.environ.get("TIMEZONE", "UTC"),
                memory_id=os.environ.get("MEMORY_ID", ""),
                guardrail_id=os.environ.get("GUARDRAIL_ID", ""),
                guardrail_version=os.environ.get("GUARDRAIL_VERSION", ""),
                sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
                sheet_range=os.environ.get("GOOGLE_SHEET_RANGE", "Active!A1:Z1000"),
                sheet_secret_name=os.environ.get(
                    "SHEET_SECRET_NAME", ""
                ),
                notion_database_id=os.environ.get("NOTION_DATABASE_ID", ""),
                notion_secret_name=os.environ.get(
                    "NOTION_SECRET_NAME", ""
                ),
            )
        except ValueError as exc:
            raise ConfigError(f"Invalid configuration: {exc}") from exc
