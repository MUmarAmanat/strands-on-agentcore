"""AgentCore Runtime entrypoint.

AgentCore Runtime runs this module as an HTTP server (port 8080) with two
endpoints, both provided by :class:`BedrockAgentCoreApp`:

* ``POST /invocations``: calls :func:`invoke` with the JSON payload.
* ``GET /ping``: health check (``Healthy`` or ``HealthyBusy``).

EventBridge Scheduler waits for the invoke call to return, so
:func:`invoke` only validates the input, starts the agent in a background
thread and returns straight away.

Flow:
    1. ``invoke`` loads config and parses the reference date.
    2. ``invoke`` registers an async task (``/ping`` reports HealthyBusy).
    3. ``invoke`` starts ``_run_agent`` in a thread and returns "accepted".
    4. ``_run_agent`` builds and runs the agent, then completes the task.

Run locally with ``python -m reminder_agent.app``.
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .config import Config, ConfigError
from .factory import build_agent
from .logging_config import get_logger

_LOGGER = get_logger(__name__)

app = BedrockAgentCoreApp()


def _parse_reference_date(payload: dict[str, Any], tz: str) -> date:
    """Turn the optional payload date into a :class:`datetime.date`.

    Args:
        payload: Invocation payload; may contain ``reference_date``.
        tz: IANA timezone used when no date is given.

    Returns:
        The payload date, or today in ``tz``.

    Raises:
        ValueError: If ``reference_date`` is not a ``YYYY-MM-DD`` string.
    """
    raw = payload.get("reference_date")
    if raw is None:
        # .date() drops the time, so the agent always gets a plain date.
        return datetime.now(ZoneInfo(tz)).date()
    if not isinstance(raw, str):
        raise ValueError("reference_date must be a 'YYYY-MM-DD' string")
    return date.fromisoformat(raw)


def _run_agent(task_id: int, config: Config, reference_date: date) -> None:
    """Run one reminder cycle in the background.

    Args:
        task_id: Async task id to complete when done.
        config: Validated configuration.
        reference_date: Date the agent evaluates tasks against.
    """
    try:
        result = build_agent(config).run(reference_date)
        _LOGGER.info(
            "Reminder run finished",
            extra={
                "sent": result.sent,
                "task_count": result.task_count,
                "message_id": result.message_id,
            },
        )
    except Exception:  # Log every failure; the thread has no caller.
        _LOGGER.exception("Reminder run failed")
    finally:
        # Always release the task, or /ping stays HealthyBusy.
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, str]:
    """Acknowledge the invocation and start the agent in the background.

    Args:
        payload: JSON payload, e.g. ``{}`` or
            ``{"reference_date": "2026-11-16"}``.

    Returns:
        ``{"status": "accepted", ...}`` or ``{"status": "error", ...}``.
    """
    try:
        config = Config.from_env()
        reference_date = _parse_reference_date(payload or {}, config.timezone)
    except (ConfigError, ValueError) as exc:
        _LOGGER.error("Invalid invocation", extra={"error": str(exc)})
        return {"status": "error", "error": str(exc)}

    task_id = app.add_async_task("reminder_run")
    threading.Thread(
        target=_run_agent,
        args=(task_id, config, reference_date),
        daemon=True,
    ).start()
    return {"status": "accepted", "reference_date": reference_date.isoformat()}


if __name__ == "__main__":
    app.run()
