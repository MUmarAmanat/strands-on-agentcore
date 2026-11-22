"""Domain models and the task-source interface.

The :class:`Task` model is the canonical internal representation, decoupled
from whatever backing store supplies it. :class:`TaskSource` is the
extension point: implement it for any source (Sheets, a database, an API)
without touching the rest of the agent.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Task:
    """A single task item.

    Attributes:
        title: Human-readable task name.
        completed: Whether the task is done (excluded from reminders).
        priority: Importance from 1 (highest) to 5 (lowest); ``None`` if unset.
        days_passed: Days elapsed since the task's start date passed; ``None``
            if unknown. Drives how urgent a stale task is.
        start_date: The date the task was meant to start, if known (context).
    """

    title: str
    completed: bool
    priority: int | None
    days_passed: int | None
    start_date: date | None


class TaskSource(abc.ABC):
    """Abstract source of tasks.

    Implementations must return a list of fully-formed :class:`Task`
    objects. Keeping this abstract makes the agent storage-agnostic.
    """

    @abc.abstractmethod
    def fetch_tasks(self) -> list[Task]:
        """Retrieve all tasks from the backing store.

        Returns:
            A list of tasks.
        """
        raise NotImplementedError
