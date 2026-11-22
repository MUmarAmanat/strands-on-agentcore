"""Retry utilities with exponential backoff.

Transient failures (network blips, throttling) should be retried; logic
errors should not. The :func:`retry` decorator retries only the exception
types it is told to, with jittered exponential backoff to avoid thundering
herds.
"""

from __future__ import annotations

import functools
import random
import time
from typing import Callable, TypeVar

from .logging_config import get_logger

_LOGGER = get_logger(__name__)

T = TypeVar("T")


def retry(
    exceptions: tuple[type[Exception], ...],
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry a callable on the given exceptions with exponential backoff.

    Args:
        exceptions: Exception types that should trigger a retry.
        max_attempts: Total attempts including the first call.
        base_delay: Initial backoff delay in seconds.
        max_delay: Upper bound for any single backoff delay.

    Returns:
        A decorator that wraps the target callable with retry behaviour.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> T:
            attempt = 1
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= max_attempts:
                        _LOGGER.error(
                            "Retries exhausted",
                            extra={"function": func.__name__, "attempts": attempt},
                        )
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, base_delay)
                    _LOGGER.warning(
                        "Transient failure, retrying",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 3),
                            "error_type": type(exc).__name__,
                        },
                    )
                    time.sleep(delay)
                    attempt += 1

        return wrapper

    return decorator
