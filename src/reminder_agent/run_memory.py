"""Run memory: remembers which dates already got a reminder email.

Stops duplicate emails when the agent runs twice for the same date
(e.g. a Scheduler retry or a manual re-run).

* :class:`AgentCoreRunMemory` stores one event per sent date in
  AgentCore Memory (short-term events, no long-term strategies).
* :class:`NullRunMemory` does nothing; used when no memory is configured
  (local runs, tests).

Failure policy is fail-open: if Memory is unreachable, the check says
"not sent" so the reminder still goes out. A duplicate is better than a
missed reminder.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from .logging_config import get_logger

_LOGGER = get_logger(__name__)

# Fixed actor: no email or personal data is stored as an identifier.
_ACTOR_ID = "reminder-agent"


def _session_id(reference_date: date) -> str:
    """Return the Memory session id for a date, e.g. ``run-2026-11-17``."""
    return f"run-{reference_date.isoformat()}"


class RunMemory(ABC):
    """Records sent reminders per date."""

    @abstractmethod
    def already_sent(self, reference_date: date) -> bool:
        """Return True if a reminder was already sent for this date."""

    @abstractmethod
    def record_sent(
        self, reference_date: date, message_id: str, task_count: int
    ) -> None:
        """Record that a reminder was sent for this date."""


class NullRunMemory(RunMemory):
    """No-op memory: never remembers anything."""

    def already_sent(self, reference_date: date) -> bool:
        """Always False, so every run proceeds."""
        return False

    def record_sent(
        self, reference_date: date, message_id: str, task_count: int
    ) -> None:
        """Do nothing."""


class AgentCoreRunMemory(RunMemory):
    """Run memory backed by AgentCore Memory events."""

    def __init__(self, client: Any, memory_id: str) -> None:
        """Store the injected client and memory id.

        Args:
            client: A ``bedrock_agentcore.memory.MemoryClient``.
            memory_id: ID of the AgentCore Memory resource.
        """
        self._client = client
        self._memory_id = memory_id

    def already_sent(self, reference_date: date) -> bool:
        """Check Memory for an event under this date's session.

        Args:
            reference_date: Date of the run.

        Returns:
            True if an event exists; False if none, or if Memory fails
            (fail-open).
        """
        try:
            events = self._client.list_events(
                memory_id=self._memory_id,
                actor_id=_ACTOR_ID,
                session_id=_session_id(reference_date),
                max_results=1,
                include_payload=False,
            )
        except Exception:  # Fail-open: never block a reminder.
            _LOGGER.warning("Memory check failed; proceeding", exc_info=True)
            return False
        return bool(events)

    def record_sent(
        self, reference_date: date, message_id: str, task_count: int
    ) -> None:
        """Write one event recording the sent email.

        Errors are logged, not raised: the email is already sent, so a
        failure here must not fail the run.

        Args:
            reference_date: Date of the run.
            message_id: SES message id.
            task_count: Number of active tasks in the email.
        """
        text = f"sent message_id={message_id} tasks={task_count}"
        try:
            self._client.create_event(
                memory_id=self._memory_id,
                actor_id=_ACTOR_ID,
                session_id=_session_id(reference_date),
                messages=[(text, "ASSISTANT")],
            )
        except Exception:  # Email went out; only the record is lost.
            _LOGGER.warning("Memory record failed", exc_info=True)
