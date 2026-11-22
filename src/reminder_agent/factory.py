"""Composition root: builds real objects from configuration.

This is the only module that knows about concrete implementations
(Google Sheets, SES, Bedrock). Runners such as :mod:`reminder_agent.main`
and the AgentCore entrypoint call :func:`build_agent` and stay unaware of
the wiring.

Flow of :func:`build_agent`:
    1. ``SecretsManagerProvider`` is created for the configured region.
    2. ``create_source`` builds the task source (reads the sheet key via
       the secrets provider).
    3. The notifier is the injected one, or ``build_notifier`` (SES).
    4. ``build_model`` builds the Bedrock model.
    5. ``build_memory`` builds AgentCore run memory, or a no-op one.
    6. ``build_guard`` builds the sheet-text guardrail check, or a no-op.
    7. All are injected into :class:`ReminderAgent`.
"""

from __future__ import annotations

import boto3
from bedrock_agentcore.memory import MemoryClient
from botocore.config import Config as BotocoreConfig
from strands.models import BedrockModel

from .agent import ReminderAgent
from .config import Config, SourceType
from .guardrail import BedrockTaskGuard, NullTaskGuard, TaskGuard
from .models import TaskSource
from .notifier import Notifier, SesNotifier
from .run_memory import AgentCoreRunMemory, NullRunMemory, RunMemory
from .secrets import SecretsManagerProvider, SecretsProvider
from .sheets_source import GoogleSheetsTaskSource


def build_model(config: Config) -> BedrockModel:
    """Build the Bedrock model used by the agent.

    Args:
        config: Validated runtime configuration.

    Returns:
        A Strands ``BedrockModel`` with timeout and retries applied, and
        the guardrail on model input and output when configured.
    """
    guardrail: dict[str, object] = {}
    if config.guardrail_id:
        guardrail = {
            "guardrail_id": config.guardrail_id,
            "guardrail_version": config.guardrail_version,
            # Replace a blocked model reply instead of keeping it.
            "guardrail_redact_output": True,
        }
    return BedrockModel(
        model_id=config.bedrock_model_id,
        region_name=config.aws_region,
        boto_client_config=_client_config(config),
        # Batch job, no live output needed: use Converse, not ConverseStream
        # (needs only bedrock:InvokeModel, not ...WithResponseStream).
        streaming=False,
        **guardrail,
    )


def _client_config(config: Config) -> BotocoreConfig:
    """Return the shared botocore timeout and retry settings."""
    return BotocoreConfig(
        read_timeout=config.llm_timeout_seconds,
        retries={"max_attempts": config.max_retries, "mode": "standard"},
    )


def build_guard(config: Config) -> TaskGuard:
    """Build the sheet-text guard: Bedrock if configured, else no-op.

    Args:
        config: Validated runtime configuration.

    Returns:
        A :class:`BedrockTaskGuard` or a :class:`NullTaskGuard`.
    """
    if not config.guardrail_id:
        return NullTaskGuard()
    client = boto3.client(
        "bedrock-runtime",
        region_name=config.aws_region,
        config=_client_config(config),
    )
    return BedrockTaskGuard(
        client, config.guardrail_id, config.guardrail_version
    )


def create_source(config: Config, secrets: SecretsProvider) -> TaskSource:
    """Build the task source selected by ``config.source_type``.

    Args:
        config: Validated runtime configuration.
        secrets: Provider used to fetch the source's credentials.

    Returns:
        A concrete :class:`TaskSource`.

    Raises:
        ValueError: If the source type is not supported.
    """
    if config.source_type is SourceType.GOOGLE_SHEETS:
        return GoogleSheetsTaskSource(
            sheet_id=config.sheet_id,
            sheet_range=config.sheet_range,
            secret_name=config.sheet_secret_name,
            secrets_provider=secrets,
        )
    raise ValueError(f"Unsupported source type: {config.source_type}")


def build_notifier(config: Config) -> SesNotifier:
    """Build the SES notifier.

    Args:
        config: Validated runtime configuration.

    Returns:
        An :class:`SesNotifier` for the configured sender and recipient.
    """
    return SesNotifier(
        sender=config.ses_sender,
        recipient=config.ses_recipient,
        region=config.aws_region,
    )


def build_memory(config: Config) -> RunMemory:
    """Build run memory: AgentCore if ``memory_id`` is set, else no-op.

    Args:
        config: Validated runtime configuration.

    Returns:
        An :class:`AgentCoreRunMemory` or a :class:`NullRunMemory`.
    """
    if not config.memory_id:
        return NullRunMemory()
    return AgentCoreRunMemory(
        client=MemoryClient(region_name=config.aws_region),
        memory_id=config.memory_id,
    )


def build_agent(
    config: Config,
    notifier: Notifier | None = None,
) -> ReminderAgent:
    """Build a fully wired :class:`ReminderAgent`.

    Args:
        config: Validated runtime configuration.
        notifier: Optional notifier override (e.g. a printer for dry runs).
            Defaults to SES.

    Returns:
        A ready-to-run agent.
    """
    secrets = SecretsManagerProvider(region=config.aws_region)
    return ReminderAgent(
        source=create_source(config, secrets),
        notifier=notifier or build_notifier(config),
        model=build_model(config),
        memory=build_memory(config),
        guard=build_guard(config),
    )
