"""Tests for Google Sheets task parsing."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from reminder_agent.models import Task
from reminder_agent.sheets_source import GoogleSheetsTaskSource, SheetReadError


def _make_source(rows: list[list[str]]) -> GoogleSheetsTaskSource:
    """Return a source whose Sheets API returns the given rows."""
    fake_service = MagicMock()
    (
        fake_service.spreadsheets()
        .values()
        .get()
        .execute.return_value
    ) = {"values": rows}

    secrets = MagicMock()
    secrets.get_secret.return_value = "{}"

    return GoogleSheetsTaskSource(
        sheet_id="sheet123",
        sheet_range="Tasks!A1:Z1000",
        secret_name="dummy",
        secrets_provider=secrets,
        service_factory=lambda: fake_service,
    )


HEADER = ["Name", "Start Date", "Priority (1 high - 5 low)", "Days since passed", "Completed"]


class TestSheetParsing:
    def test_parses_active_task(self):
        rows = [HEADER, ["Write tests", "2026-06-01", "2", "22", "No"]]
        tasks = _make_source(rows).fetch_tasks()
        assert len(tasks) == 1
        t = tasks[0]
        assert t.title == "Write tests"
        assert t.completed is False
        assert t.priority == 2
        assert t.days_passed == 22
        assert t.start_date == date(2026, 6, 1)

    def test_parses_completed_task(self):
        rows = [HEADER, ["Done task", "2026-05-01", "1", "50", "Yes"]]
        tasks = _make_source(rows).fetch_tasks()
        assert tasks[0].completed is True

    def test_skips_empty_rows(self):
        rows = [HEADER, [], ["", "", "", "", ""]]
        tasks = _make_source(rows).fetch_tasks()
        assert tasks == []

    def test_empty_sheet_returns_empty_list(self):
        tasks = _make_source([]).fetch_tasks()
        assert tasks == []

    def test_missing_required_column_raises(self):
        rows = [["Name", "Priority"], ["Task A", "1"]]
        with pytest.raises(SheetReadError):
            _make_source(rows).fetch_tasks()

    def test_parses_long_date_format(self):
        rows = [HEADER, ["Plan sprint", "June 1, 2026", "3", "10", "No"]]
        tasks = _make_source(rows).fetch_tasks()
        assert tasks[0].start_date == date(2026, 6, 1)

    def test_multiple_tasks(self):
        rows = [
            HEADER,
            ["Task A", "2026-06-01", "1", "5", "No"],
            ["Task B", "2026-06-10", "3", "2", "Yes"],
        ]
        tasks = _make_source(rows).fetch_tasks()
        assert len(tasks) == 2

    def test_priority_none_when_empty(self):
        rows = [HEADER, ["Task X", "2026-06-01", "", "5", "No"]]
        tasks = _make_source(rows).fetch_tasks()
        assert tasks[0].priority is None
