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


# Transient network failures that are safe to retry on an IDEMPOTENT read.
# ConnectTimeout subclasses both ConnectionError and Timeout; listing all three
# keeps the intent explicit.
_TRANSIENT_READ_ERRORS = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)


def call_with_retry_on_transient(
    call: Callable[[], _T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> _T:
    """Like :func:`call_with_retry_on_429` but ALSO retries transient network
    timeouts — for IDEMPOTENT read-only Alpaca calls only.

    A routine eval-cycle broker read (``get_clock`` / ``get_account`` /
    ``get_all_positions`` / ``get_orders`` / ...) that hits a transient
    ``ReadTimeout`` / ``ConnectTimeout`` / ``ConnectionError`` would otherwise
    propagate out of the runner's poll loop and kill the whole process — three
    runners died this way during the 2026-06-17 same-symbol co-run soak. Reads
    are idempotent, so retrying a timed-out read is safe.

    **NOT for writes.** ``submit_order`` stays on
    :func:`call_with_retry_on_429`: a write timeout is ambiguous (the order may
    already have been created) and a blind retry could double-submit. 429s are
    delegated to ``call_with_retry_on_429`` (Alpaca rejects at the rate-limit
    gate before any state change, so that retry is safe and idempotent).

    Re-raises the last transient error when ``max_attempts`` is exhausted; any
    non-transient, non-429 error bubbles up on its first occurrence.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return call_with_retry_on_429(
                call,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )
        except _TRANSIENT_READ_ERRORS as exc:
            delay = min(base_delay * 2**attempt + random.uniform(0, base_delay), max_delay)
            _logger.warning(
                "alpaca_transient_retry kind=%s attempt=%d delay=%.2fs",
                type(exc).__name__,
                attempt,
                delay,
            )
            time.sleep(delay)
            last_exc = exc
    raise last_exc  # type: ignore[misc]
