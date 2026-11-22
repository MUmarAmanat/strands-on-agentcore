"""Unit tests for the local runner (no AWS calls)."""

from datetime import date
from unittest.mock import patch

from reminder_agent.main import PrintNotifier, main


class TestPrintNotifier:
    """Dry-run notifier."""

    def test_prints_and_returns_id(self, capsys):
        """The email is printed and a placeholder id returned."""
        message_id = PrintNotifier().send("Subj", "Body text")

        out = capsys.readouterr().out
        assert "Subj" in out and "Body text" in out
        assert message_id == "dry-run"


@patch("reminder_agent.main._load_dotenv")
@patch("reminder_agent.main.Config.from_env")
@patch("reminder_agent.main.build_agent")
class TestMain:
    """Argument handling and wiring in main()."""

    def test_dry_run_uses_print_notifier(self, build, _cfg, _env):
        """--dry-run passes a PrintNotifier to build_agent."""
        main(["--dry-run"])
        assert isinstance(build.call_args.kwargs["notifier"], PrintNotifier)

    def test_default_uses_ses(self, build, _cfg, _env):
        """Without --dry-run, notifier is None so the factory uses SES."""
        main([])
        assert build.call_args.kwargs["notifier"] is None

    def test_date_is_passed_to_run(self, build, _cfg, _env):
        """--date is parsed and given to agent.run()."""
        main(["--date", "2026-01-15"])
        build.return_value.run.assert_called_once_with(date(2026, 1, 15))
