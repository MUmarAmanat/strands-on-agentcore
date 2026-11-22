"""Local runner: one real reminder cycle from your machine.

Usage::

    uv run python -m reminder_agent.main --dry-run   # email printed, not sent
    uv run python -m reminder_agent.main             # email sent via SES
    uv run python -m reminder_agent.main --date 2026-01-15

Flow of :func:`main`:
    1. ``_parse_args`` reads ``--dry-run`` and ``--date``.
    2. ``.env`` is loaded (if python-dotenv is installed).
    3. ``Config.from_env`` validates settings.
    4. ``build_agent`` wires real Sheets + Bedrock (+ SES unless dry run).
    5. ``agent.run`` executes one cycle and the result is printed.
"""

from __future__ import annotations

import argparse
from datetime import date

from .config import Config
from .factory import build_agent
from .logging_config import get_logger
from .notifier import Notifier

_LOGGER = get_logger(__name__)

_DRY_RUN_MESSAGE_ID = "dry-run"


class PrintNotifier(Notifier):
    """Notifier that prints the email instead of sending it."""

    def send(self, subject: str, body: str) -> str:
        """Print the email to stdout.

        Args:
            subject: Email subject line.
            body: Plain-text body.

        Returns:
            A fixed placeholder message id.
        """
        print(f"\n--- DRY RUN EMAIL ---\nSubject: {subject}\n\n{body}\n")
        return _DRY_RUN_MESSAGE_ID


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list; ``None`` means ``sys.argv[1:]``.

    Returns:
        Parsed arguments with ``dry_run`` and ``date``.
    """
    parser = argparse.ArgumentParser(description="Run the reminder agent.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the email instead of sending it.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=date.today(),
        help="Reference date, YYYY-MM-DD (default: today).",
    )
    return parser.parse_args(argv)


def _load_dotenv() -> None:
    """Load ``.env`` into the environment if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        _LOGGER.warning(
            "python-dotenv not installed; .env NOT loaded, using real env vars"
        )
        return
    load_dotenv()


def main(argv: list[str] | None = None) -> None:
    """Run one reminder cycle locally.

    Args:
        argv: Argument list; ``None`` means ``sys.argv[1:]``.
    """
    args = _parse_args(argv)
    _load_dotenv()

    config = Config.from_env()
    notifier = PrintNotifier() if args.dry_run else None
    agent = build_agent(config, notifier=notifier)

    result = agent.run(args.date)
    print(result)


if __name__ == "__main__":
    main()
