"""Google Sheets implementation of :class:`TaskSource`.

Reads rows from a single shared sheet using a read-only service account.
The service-account JSON is supplied via the injected
:class:`SecretsProvider`; it is never read from disk or hardcoded.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .logging_config import get_logger
from .models import Task, TaskSource
from .retry import retry
from .secrets import SecretsProvider

_LOGGER = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%B %d, %Y",  # e.g. "June 22, 2026"
    "%b %d, %Y",  # e.g. "Jun 22, 2026"
)

# Header aliases (matched as case-insensitive substrings), in priority order.
_TITLE_ALIASES = ("name", "title", "task")
_START_ALIASES = ("start date", "start_date", "start")
_PRIORITY_ALIASES = ("priority",)
_DAYS_ALIASES = ("days since passed", "days passed", "days")
_COMPLETED_ALIASES = ("completed", "complete", "done")

_TRUE_VALUES = {"yes", "y", "true", "done", "completed", "1"}


class SheetReadError(RuntimeError):
    """Raised when the sheet cannot be read or parsed."""


class GoogleSheetsTaskSource(TaskSource):
    """Fetch tasks from a Google Sheet.

    The sheet is expected to have a header row with columns for the task
    name, start date, priority, days since the start date passed, and a
    completed (yes/no) flag. Header matching is case-insensitive and
    tolerant of extra text (e.g. ``Priority (1 or high - 5 or low)``).
    """

    def __init__(
        self,
        sheet_id: str,
        sheet_range: str,
        secret_name: str,
        secrets_provider: SecretsProvider,
        service_factory: object | None = None,
    ) -> None:
        """Initialise the source.

        Args:
            sheet_id: Spreadsheet identifier.
            sheet_range: A1 notation range to read.
            secret_name: Secrets Manager name of the service-account JSON.
            secrets_provider: Injected provider used to fetch the JSON key.
            service_factory: Optional callable returning a Sheets service
                (for injection/testing).
        """
        self._sheet_id = sheet_id
        self._range = sheet_range
        self._secret_name = secret_name
        self._secrets = secrets_provider
        self._service_factory = service_factory

    def _build_service(self) -> object:
        """Construct an authenticated Sheets API service.

        Returns:
            A Google Sheets API service object.

        Raises:
            SheetReadError: If credentials cannot be parsed.
        """
        if self._service_factory is not None:
            return self._service_factory()

        raw_key = self._secrets.get_secret(self._secret_name)
        try:
            info = json.loads(raw_key)
        except json.JSONDecodeError as exc:
            raise SheetReadError("Service-account key is not valid JSON") from exc

        creds = service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    @retry((HttpError,), max_attempts=3)
    def _read_values(self, service: object) -> list[list[str]]:
        """Read raw cell values from the sheet.

        Args:
            service: An authenticated Sheets service.

        Returns:
            A list of rows, each a list of string cell values.
        """
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=self._sheet_id, range=self._range)
            .execute()
        )
        return result.get("values", [])

    def fetch_tasks(self) -> list[Task]:
        """Read and parse all tasks from the sheet.

        Returns:
            A list of parsed tasks. Malformed rows are skipped, not fatal.

        Raises:
            SheetReadError: If required columns are missing.
        """
        service = self._build_service()
        rows = self._read_values(service)
        if not rows:
            _LOGGER.info("Sheet returned no rows")
            return []

        header = [cell.strip().lower() for cell in rows[0]]
        try:
            idx_title = self._find_column(header, _TITLE_ALIASES)
            idx_start = self._find_column(header, _START_ALIASES)
            idx_priority = self._find_column(header, _PRIORITY_ALIASES)
            idx_days = self._find_column(header, _DAYS_ALIASES)
            idx_completed = self._find_column(header, _COMPLETED_ALIASES)
        except ValueError as exc:
            raise SheetReadError(
                "Sheet must contain name, start date, priority, days-passed "
                "and completed columns"
            ) from exc

        tasks: list[Task] = []
        for row in rows[1:]:
            task = self._parse_row(
                row, idx_title, idx_start, idx_priority, idx_days, idx_completed
            )
            if task is not None:
                tasks.append(task)

        _LOGGER.info("Parsed tasks from sheet", extra={"task_count": len(tasks)})
        return tasks

    @staticmethod
    def _find_column(header: list[str], aliases: tuple[str, ...]) -> int:
        """Return the index of the first header cell matching any alias.

        Matching is by case-insensitive substring so verbose headers like
        ``priority (1 or high - 5 or low)`` still resolve.

        Args:
            header: The normalised header row.
            aliases: Accepted column names, in priority order.

        Returns:
            The column index.

        Raises:
            ValueError: If none of the aliases is present.
        """
        for alias in aliases:
            for index, cell in enumerate(header):
                if alias in cell:
                    return index
        raise ValueError(f"No column matching {aliases}")

    @classmethod
    def _parse_row(
        cls,
        row: list[str],
        idx_title: int,
        idx_start: int,
        idx_priority: int,
        idx_days: int,
        idx_completed: int,
    ) -> Task | None:
        """Parse a single sheet row into a :class:`Task`.

        Args:
            row: The raw cell values.
            idx_title: Column index of the task name.
            idx_start: Column index of the start date.
            idx_priority: Column index of the priority.
            idx_days: Column index of the days-since-passed value.
            idx_completed: Column index of the completed flag.

        Returns:
            A task, or ``None`` if the row is empty or has no title.
        """

        def cell(index: int) -> str:
            return row[index].strip() if index < len(row) else ""

        title = cell(idx_title)
        if not title:
            return None

        return Task(
            title=title,
            completed=cls._parse_bool(cell(idx_completed)),
            priority=cls._parse_int(cell(idx_priority)),
            days_passed=cls._parse_int(cell(idx_days)),
            start_date=cls._parse_date(cell(idx_start)),
        )

    @staticmethod
    def _parse_bool(value: str) -> bool:
        """Interpret a yes/no style cell as a boolean."""
        return value.strip().lower() in _TRUE_VALUES

    @staticmethod
    def _parse_int(value: str) -> int | None:
        """Parse an integer cell, returning ``None`` if not a number."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_date(value: str) -> date | None:
        """Parse a date cell using several accepted formats.

        Args:
            value: The raw date string.

        Returns:
            A :class:`date`, or ``None`` if empty or unparseable.
        """
        if not value:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        _LOGGER.warning("Unparseable start date skipped", extra={"value": value})
        return None
