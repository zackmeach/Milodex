# src/milodex/core/_alpaca_retry.py
"""Shared Alpaca 429 retry helper.

Private to the milodex package — imported by both the data layer
(alpaca_provider.py) and the broker layer (alpaca_client.py).
Alpaca-specific only; not a general-purpose retry mechanism.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import requests.exceptions
from alpaca.common.exceptions import APIError

_logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def call_with_retry_on_429(
    call: Callable[[], _T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> _T:
    """Invoke ``call()`` and retry on Alpaca 429 with exponential backoff + jitter.

    Returns the call's result on success. Re-raises the original exception
    when ``max_attempts`` is exhausted. Non-429 exceptions are NOT retried —
    they bubble up on the first failure (retrying 401/500 hides real problems).

    Backoff schedule: ``base_delay * 2**attempt`` capped at ``max_delay``,
    plus ``random.uniform(0, base_delay)`` jitter to avoid thundering-herd
    when multiple runners hit 429 simultaneously and synchronize their retries.

    Safety note for write operations (e.g., submit_order):
    Alpaca returns 429 before any state change — the request was rejected at
    the rate-limit gate, not after order creation began. Retrying is therefore
    safe and idempotent: no duplicate order can result from a 429 retry. This
    assumption holds for the standard Alpaca Trading API; verify if using
    alternative endpoints.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return call()
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            if status is not None and status == 429:
                delay = min(base_delay * 2**attempt + random.uniform(0, base_delay), max_delay)
                _logger.warning(
                    "alpaca_429_retry attempt=%d delay=%.2fs",
                    attempt,
                    delay,
                )
                time.sleep(delay)
                last_exc = exc
            else:
                raise
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                delay = min(base_delay * 2**attempt + random.uniform(0, base_delay), max_delay)
                _logger.warning(
                    "alpaca_429_retry attempt=%d delay=%.2fs",
                    attempt,
                    delay,
                )
                time.sleep(delay)
                last_exc = exc
            else:
                raise
    raise last_exc  # type: ignore[misc]
