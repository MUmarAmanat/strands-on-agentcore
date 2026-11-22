"""Unit tests for run memory (MemoryClient is mocked, no AWS calls)."""

from datetime import date
from unittest.mock import MagicMock

from reminder_agent.run_memory import AgentCoreRunMemory, NullRunMemory

REF_DATE = date(2026, 11, 17)


def _memory(events=None, error=None):
    """Build an AgentCoreRunMemory with a mocked client."""
    client = MagicMock()
    client.list_events.return_value = events or []
    if error:
        client.list_events.side_effect = error
        client.create_event.side_effect = error
    return AgentCoreRunMemory(client, memory_id="mem-1"), client


class TestNullRunMemory:
    """No-op memory."""

    def test_never_sent(self):
        """Always reports not sent, even after recording."""
        memory = NullRunMemory()
        memory.record_sent(REF_DATE, "m-1", 2)
        assert memory.already_sent(REF_DATE) is False


class TestAlreadySent:
    """Duplicate check."""

    def test_true_when_event_exists(self):
        """An event for the date means already sent."""
        memory, client = _memory(events=[{"eventId": "e-1"}])
        assert memory.already_sent(REF_DATE) is True

        kwargs = client.list_events.call_args.kwargs
        assert kwargs["memory_id"] == "mem-1"
        assert kwargs["actor_id"] == "reminder-agent"
        assert kwargs["session_id"] == "run-2026-11-17"

    def test_false_when_no_event(self):
        """No event for the date means not sent."""
        memory, _ = _memory(events=[])
        assert memory.already_sent(REF_DATE) is False

    def test_fail_open_on_error(self):
        """If Memory fails, report not sent so the reminder still goes out."""
        memory, _ = _memory(error=RuntimeError("down"))
        assert memory.already_sent(REF_DATE) is False


class TestRecordSent:
    """Recording a send."""

    def test_writes_event(self):
        """One event is written under the date's session."""
        memory, client = _memory()
        memory.record_sent(REF_DATE, "m-1", 3)

        kwargs = client.create_event.call_args.kwargs
        assert kwargs["session_id"] == "run-2026-11-17"
        assert kwargs["messages"] == [("sent message_id=m-1 tasks=3", "ASSISTANT")]

    def test_error_is_swallowed(self):
        """A failed write is logged, not raised."""
        memory, _ = _memory(error=RuntimeError("down"))
        memory.record_sent(REF_DATE, "m-1", 3)  # Must not raise.
