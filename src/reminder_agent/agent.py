"""Strands reminder agent.

:class:`ReminderAgent` gives a Strands agent two tools, ``fetch_tasks`` (read)
and ``send_reminder_email`` (write), and lets the model decide which tasks
deserve a reminder. All dependencies are injected, so the agent never builds
its own clients; see :mod:`reminder_agent.factory` for the wiring.

Flow of :meth:`ReminderAgent.run`:
    1. Run memory is checked; if this date was already sent, return
       ``skipped`` (no LLM call).
    2. ``_build_tools`` creates the two tools, bound to an outcome dict.
       ``fetch_tasks`` checks the sheet text with the task guard; if it
       is blocked, ``send_reminder_email`` refuses to send.
    3. A Strands ``Agent`` is created with the model, tools and prompt.
    4. The agent runs the LLM <-> tool loop, capped by loop limits.
       After a successful send, the tool records it in run memory.
    5. The outcome dict is returned as a :class:`RunResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from strands import Agent, tool
from strands.models import Model

from .guardrail import NullTaskGuard, TaskGuard
from .logging_config import get_logger
from .models import Task, TaskSource
from .notifier import Notifier
from .run_memory import NullRunMemory, RunMemory

_LOGGER = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a proactive personal task assistant. Each day you:\n"
    "1. Call `fetch_tasks` to retrieve the user's active tasks "
    "(completed tasks are already excluded).\n"
    "2. Group ALL active tasks by priority, in descending order of "
    "importance: Priority 1 (highest) first, down to Priority 5 (lowest). "
    "Tasks with no priority go in a final 'No priority' group. Within each "
    "group, list tasks with the most days passed first.\n"
    "3. If there is at least one active task, call `send_reminder_email` "
    "exactly once. Include EVERY active task, not only urgent ones. For "
    "each task write its title and how many days have passed since it "
    "started (e.g. 'Write report - 5 days passed'); if unknown, write "
    "'days passed unknown'. Use a heading for each priority group. Keep "
    "the tone friendly and concise, and end with one encouraging line "
    "that pushes the user to start and beat procrastination.\n"
    "4. If there are no active tasks, do NOT send an email; just reply "
    "that there is nothing to remind about.\n"
    "Today's reference date is {reference_date}."
)

_USER_PROMPT = "Run today's reminder check."

# A normal run is 3 turns: fetch -> send -> final reply.
_DEFAULT_MAX_TURNS = 5
_DEFAULT_MAX_TOTAL_TOKENS = 20_000


@dataclass(frozen=True)
class RunResult:
    """Outcome of a single agent run.

    Attributes:
        sent: Whether an email was sent.
        message_id: SES message id when sent, else empty.
        task_count: Number of non-completed tasks the agent considered.
        skipped: True if the run was skipped because this date was
            already sent.
        blocked: True if the guardrail blocked the sheet text.
    """

    sent: bool
    message_id: str
    task_count: int
    skipped: bool = False
    blocked: bool = False


def _render_tasks(tasks: list[Task]) -> str:
    """Render active tasks into a compact text block for the LLM.

    Args:
        tasks: The active (non-completed) tasks to render.

    Returns:
        A plain-text representation, one task per line.
    """
    if not tasks:
        return "No active tasks found."

    lines: list[str] = []
    for task in tasks:
        parts = [f"- {task.title}"]
        if task.priority is not None:
            parts.append(f"[priority {task.priority} (1=high, 5=low)]")
        if task.days_passed is not None:
            parts.append(f"[{task.days_passed} day(s) since start]")
        if task.start_date is not None:
            parts.append(f"(start {task.start_date.isoformat()})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


class ReminderAgent:
    """A Strands agent that reminds the user about their tasks."""

    def __init__(
        self,
        source: TaskSource,
        notifier: Notifier,
        model: Model,
        max_turns: int = _DEFAULT_MAX_TURNS,
        max_total_tokens: int = _DEFAULT_MAX_TOTAL_TOKENS,
        memory: RunMemory | None = None,
        guard: TaskGuard | None = None,
    ) -> None:
        """Store the injected dependencies and loop limits.

        Args:
            source: Where tasks are read from (e.g. Google Sheets).
            notifier: How the reminder is delivered (e.g. SES).
            model: Strands model the agent reasons with (e.g. Bedrock).
            max_turns: Cap on model calls per run (stops runaway loops).
            max_total_tokens: Cap on input + output tokens per run.
            memory: Records sent dates to skip duplicates. Defaults to
                :class:`NullRunMemory` (no check).
            guard: Checks sheet text before the LLM sees it. Defaults to
                :class:`NullTaskGuard` (no check).
        """
        self._source = source
        self._notifier = notifier
        self._model = model
        self._limits = {"turns": max_turns, "total_tokens": max_total_tokens}
        self._memory = memory or NullRunMemory()
        self._guard = guard or NullTaskGuard()

    def run(self, reference_date: date) -> RunResult:
        """Execute one reminder cycle.

        Args:
            reference_date: Date to evaluate tasks against.

        Returns:
            A :class:`RunResult` describing what happened.
        """
        # Checked in code, not by the LLM: a hard rule, and saves tokens.
        if self._memory.already_sent(reference_date):
            _LOGGER.info(
                "Reminder already sent for this date; skipping",
                extra={"reference_date": reference_date.isoformat()},
            )
            return RunResult(
                sent=False, message_id="", task_count=0, skipped=True
            )

        # Tools write here so the outcome can be reported after the run.
        outcome: dict[str, Any] = {
            "sent": False,
            "message_id": "",
            "task_count": 0,
            "blocked": False,
        }

        agent = Agent(
            model=self._model,
            tools=self._build_tools(outcome, reference_date),
            system_prompt=_SYSTEM_PROMPT.format(
                reference_date=reference_date.isoformat()
            ),
            callback_handler=None,  # No streaming output to stdout.
        )
        result = agent(_USER_PROMPT, limits=self._limits)

        _LOGGER.info(
            "Agent run complete",
            extra={
                "sent": outcome["sent"],
                "task_count": outcome["task_count"],
                "blocked": outcome["blocked"],
                "stop_reason": str(result.stop_reason),
            },
        )
        return RunResult(
            sent=outcome["sent"],
            message_id=outcome["message_id"],
            task_count=outcome["task_count"],
            blocked=outcome["blocked"],
        )

    def _build_tools(self, outcome: dict[str, Any], reference_date: date) -> list:
        """Create the tools for one run, bound to its outcome dict.

        Args:
            outcome: Mutable dict the tools update with run results.
            reference_date: Date of the run, used to record the send.

        Returns:
            The ``fetch_tasks`` and ``send_reminder_email`` tools.
        """

        @tool
        def fetch_tasks() -> str:
            """Fetch the user's active tasks with priority and age context."""
            active = [t for t in self._source.fetch_tasks() if not t.completed]
            outcome["task_count"] = len(active)
            text = _render_tasks(active)
            # Sheet text is untrusted: check it before the LLM reads it.
            if active and not self._guard.is_safe(text):
                outcome["blocked"] = True
                return (
                    "Task data was blocked by the safety guardrail. "
                    "Do not send an email."
                )
            return text

        @tool
        def send_reminder_email(subject: str, body: str) -> str:
            """Send the reminder email to the user. Call at most once.

            Args:
                subject: The email subject line.
                body: The plain-text email body, ordered by urgency.
            """
            # Enforced in code, so a blocked run never emails, whatever the LLM does.
            if outcome["blocked"]:
                return "Task data was blocked; email not sent."
            if outcome["sent"]:
                return "Email already sent; not sending again."
            message_id = self._notifier.send(subject, body)
            outcome["sent"] = True
            outcome["message_id"] = message_id
            # Record right after SES succeeds, so a retry skips this date.
            self._memory.record_sent(
                reference_date, message_id, outcome["task_count"]
            )
            return f"Email sent (id: {message_id})."

        return [fetch_tasks, send_reminder_email]
