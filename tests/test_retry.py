"""Tests for the retry decorator."""

import pytest

from reminder_agent.retry import retry


class TestRetry:
    def test_returns_result_on_first_success(self):
        calls = {"n": 0}

        @retry((ValueError,), max_attempts=3, base_delay=0)
        def succeed():
            calls["n"] += 1
            return "ok"

        assert succeed() == "ok"
        assert calls["n"] == 1

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        @retry((ValueError,), max_attempts=3, base_delay=0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return "recovered"

        assert flaky() == "recovered"
        assert calls["n"] == 3

    def test_raises_after_exhausting_attempts(self):
        calls = {"n": 0}

        @retry((ValueError,), max_attempts=3, base_delay=0)
        def always_fail():
            calls["n"] += 1
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            always_fail()
        assert calls["n"] == 3

    def test_does_not_retry_unlisted_exception(self):
        calls = {"n": 0}

        @retry((ValueError,), max_attempts=3, base_delay=0)
        def wrong_error():
            calls["n"] += 1
            raise KeyError("not retried")

        with pytest.raises(KeyError):
            wrong_error()
        assert calls["n"] == 1
