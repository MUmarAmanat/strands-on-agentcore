"""Unit tests for the AgentCore entrypoint (no AWS calls, no threads)."""

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from reminder_agent import app as app_module
from reminder_agent.app import _parse_reference_date, _run_agent, invoke
from reminder_agent.config import Config, Environment, SourceType

CONFIG = Config(
    environment=Environment.DEV,
    source_type=SourceType.GOOGLE_SHEETS,
    bedrock_model_id="amazon.nova-pro-v1:0",
    ses_sender="sender@example.com",
    ses_recipient="to@example.com",
    aws_region="test-region",
    timezone="Asia/Dubai",
    sheet_id="sheet123",
    sheet_range="Tasks!A1:Z1000",
    sheet_secret_name="sheet-secret",
)


class TestParseReferenceDate:
    """Payload date -> datetime.date."""

    def test_missing_date_is_today_in_timezone(self):
        """No date in the payload gives today in the configured timezone."""
        expected = datetime.now(ZoneInfo("Asia/Dubai")).date()
        assert _parse_reference_date({}, "Asia/Dubai") == expected

    def test_valid_string_becomes_date(self):
        """A 'YYYY-MM-DD' string is parsed into a date object."""
        result = _parse_reference_date({"reference_date": "2026-11-16"}, "UTC")
        assert result == date(2026, 11, 16)

    def test_bad_format_raises(self):
        """A non-ISO date string is rejected."""
        with pytest.raises(ValueError):
            _parse_reference_date({"reference_date": "16/11/2026"}, "UTC")

    def test_non_string_raises(self):
        """A date that is not a string is rejected."""
        with pytest.raises(ValueError):
            _parse_reference_date({"reference_date": 20261116}, "UTC")


class TestInvoke:
    """The entrypoint acknowledges fast and hands off to a thread."""

    @patch("reminder_agent.app.threading.Thread")
    @patch("reminder_agent.app.Config.from_env", return_value=CONFIG)
    def test_accepts_and_starts_thread(self, _env, thread_cls):
        """A valid call returns 'accepted' and starts the background run."""
        with patch.object(app_module.app, "add_async_task", return_value=7):
            response = invoke({"reference_date": "2026-11-16"})

        assert response == {"status": "accepted", "reference_date": "2026-11-16"}
        assert thread_cls.call_args.kwargs["args"] == (
            7, CONFIG, date(2026, 11, 16)
        )
        thread_cls.return_value.start.assert_called_once()

    @patch("reminder_agent.app.threading.Thread")
    @patch("reminder_agent.app.Config.from_env", return_value=CONFIG)
    def test_bad_date_returns_error_without_thread(self, _env, thread_cls):
        """An invalid date returns an error and never starts the agent."""
        response = invoke({"reference_date": "tomorrow"})

        assert response["status"] == "error"
        thread_cls.assert_not_called()


class TestRunAgent:
    """Background run."""

    @patch("reminder_agent.app.build_agent")
    def test_completes_task_even_when_agent_fails(self, build_agent):
        """The async task is released even if the agent raises."""
        build_agent.return_value.run.side_effect = RuntimeError("boom")
        with patch.object(app_module.app, "complete_async_task") as complete:
            _run_agent(7, CONFIG, date(2026, 11, 16))

        complete.assert_called_once_with(7)

    @patch("reminder_agent.app.build_agent")
    def test_runs_agent_with_reference_date(self, build_agent):
        """The agent is run with the given date."""
        build_agent.return_value.run.return_value = MagicMock(
            sent=True, task_count=2, message_id="m-1"
        )
        with patch.object(app_module.app, "complete_async_task"):
            _run_agent(7, CONFIG, date(2026, 11, 16))

        build_agent.return_value.run.assert_called_once_with(date(2026, 11, 16))
