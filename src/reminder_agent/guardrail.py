"""Task guard: checks sheet text with a Bedrock Guardrail before the LLM sees it.

The sheet is untrusted input: a task title could carry a prompt injection.
The model-level guardrail (set on ``BedrockModel``) may not screen tool
results, so the text is checked explicitly with the ``ApplyGuardrail`` API.

* :class:`BedrockTaskGuard` calls ``ApplyGuardrail`` (no model involved).
* :class:`NullTaskGuard` passes everything; used when no guardrail is
  configured (local runs, tests).

Failure policy is fail-closed: if the check errors, the text is treated
as unsafe, so possibly injected text never reaches the model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .logging_config import get_logger

_LOGGER = get_logger(__name__)


class TaskGuard(ABC):
    """Decides whether task text is safe to give to the model."""

    @abstractmethod
    def is_safe(self, text: str) -> bool:
        """Return True if the text may be passed to the model."""


class NullTaskGuard(TaskGuard):
    """No-op guard: every text is safe."""

    def is_safe(self, text: str) -> bool:
        """Always True."""
        return True


class BedrockTaskGuard(TaskGuard):
    """Task guard backed by the Bedrock ``ApplyGuardrail`` API."""

    def __init__(
        self, client: Any, guardrail_id: str, guardrail_version: str
    ) -> None:
        """Store the injected client and guardrail identity.

        Args:
            client: A boto3 ``bedrock-runtime`` client.
            guardrail_id: ID of the Bedrock Guardrail.
            guardrail_version: Published version of the guardrail.
        """
        self._client = client
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version

    def is_safe(self, text: str) -> bool:
        """Check the text as model input.

        Args:
            text: Rendered task text from the sheet.

        Returns:
            False if the guardrail intervened or the call failed
            (fail-closed); True otherwise.
        """
        try:
            response = self._client.apply_guardrail(
                guardrailIdentifier=self._guardrail_id,
                guardrailVersion=self._guardrail_version,
                source="INPUT",
                content=[{"text": {"text": text}}],
            )
        except Exception:  # Fail-closed: unchecked text never reaches the LLM.
            _LOGGER.warning("Guardrail check failed; blocking", exc_info=True)
            return False

        if response.get("action") == "GUARDRAIL_INTERVENED":
            _LOGGER.warning("Guardrail blocked task data")
            return False
        return True
