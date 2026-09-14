"""Bounded retry policy for transient provider failures.

Responsible for calculating exponential backoff delays with jitter and classifying
transient OpenAI network/status errors (timeouts, rate limits, 5xx server errors).
Must NOT execute the retry loop itself, maintain attempt counters, or swallow non-transient errors.
Next: ade_app.openai_client for the execution of API calls wrapped with this retry logic.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from ade_app.config import RetrySettings


def is_transient_openai_error(error: Exception) -> bool:
    if isinstance(
        error, (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
    ):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status in {408, 409, 429} or status >= 500)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    settings: RetrySettings
    sleep: Callable[[float], None] = time.sleep
    random_value: Callable[[], float] = random.random

    def delay(self, attempt: int, error: Exception) -> float:
        retry_after = _retry_after_seconds(error)
        calculated = min(
            self.settings.backoff_max_seconds,
            self.settings.backoff_initial_seconds * (2 ** max(0, attempt - 1))
            + self.random_value() * self.settings.jitter_seconds,
        )
        return min(self.settings.backoff_max_seconds, retry_after or calculated)


def _retry_after_seconds(error: Exception) -> float | None:
    response: Any = getattr(error, "response", None)
    headers: Any = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = float(headers.get("retry-after", ""))
    except (TypeError, ValueError):
        return None
    return max(0.0, value)
