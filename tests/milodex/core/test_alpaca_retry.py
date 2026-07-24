# tests/milodex/core/test_alpaca_retry.py
"""Unit tests for the shared Alpaca 429 retry helper.

Tests target milodex.core._alpaca_retry.call_with_retry_on_429 directly.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests
from alpaca.common.exceptions import APIError

from milodex.core._alpaca_retry import call_with_retry_on_429, call_with_retry_on_transient


def _make_429_api_error() -> APIError:
    """Construct an APIError that reports status_code == 429."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = 429
    return APIError('{"code": 429, "message": "too many requests"}', http_error)


def _make_non_429_api_error(code: int = 422) -> APIError:
    """Construct an APIError with a non-429 status code."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = code
    return APIError(f'{{"code": {code}, "message": "error"}}', http_error)


class TestCallWithRetryOn429:
    def test_succeeds_immediately(self):
        """No retries when the call succeeds on the first attempt."""
        call = MagicMock(return_value="ok")
        result = call_with_retry_on_429(call)
        assert result == "ok"
        assert call.call_count == 1

    def test_retries_on_429_then_succeeds(self):
        """429 errors trigger retry; eventual success returns the value."""
        err = _make_429_api_error()
        call = MagicMock(side_effect=[err, err, "success"])

        with patch("time.sleep"):
            result = call_with_retry_on_429(call)

        assert result == "success"
        assert call.call_count == 3

    def test_exhausts_max_attempts_and_reraises(self):
        """After max_attempts, the last 429 is re-raised."""
        err = _make_429_api_error()
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                call_with_retry_on_429(call, max_attempts=4)

        assert exc_info.value is err
        assert call.call_count == 4

    def test_does_not_retry_non_429_api_error(self):
        """Non-429 APIError bubbles up on the first failure."""
        err = _make_non_429_api_error(422)
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                call_with_retry_on_429(call)

        assert exc_info.value is err
        assert call.call_count == 1

    def test_retries_on_429_http_error(self):
        """requests.HTTPError with status 429 is also retried."""
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=429))
        call = MagicMock(side_effect=[err, "ok"])

        with patch("time.sleep"):
            result = call_with_retry_on_429(call)

        assert result == "ok"
        assert call.call_count == 2

    def test_does_not_retry_non_429_http_error(self):
        """requests.HTTPError with non-429 status bubbles up immediately."""
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=503))
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError):
                call_with_retry_on_429(call)

        assert call.call_count == 1

    def test_exponential_backoff_with_jitter(self):
        """Backoff sleeps follow base_delay * 2^attempt + jitter, capped at max_delay."""
        err = _make_429_api_error()
        call = MagicMock(side_effect=[err, err, err, "done"])

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch("random.uniform", return_value=0.5):
                result = call_with_retry_on_429(call, base_delay=1.0, max_delay=60.0)

        assert result == "done"
        # attempt 0: min(1.0*1 + 0.5, 60) = 1.5
        # attempt 1: min(1.0*2 + 0.5, 60) = 2.5
        # attempt 2: min(1.0*4 + 0.5, 60) = 4.5
        assert sleep_calls == pytest.approx([1.5, 2.5, 4.5], rel=1e-6)

    def test_backoff_capped_at_max_delay(self):
        """Backoff delay never exceeds max_delay."""
        err = _make_429_api_error()
        # Force 5 attempts all failing
        call = MagicMock(side_effect=err)

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch("random.uniform", return_value=0.0):
                with pytest.raises(APIError):
                    call_with_retry_on_429(call, max_attempts=5, base_delay=10.0, max_delay=15.0)

        # attempt 0: min(10*1 + 0, 15) = 10
        # attempt 1: min(10*2 + 0, 15) = 15
        # attempt 2: min(10*4 + 0, 15) = 15
        # attempt 3: min(10*8 + 0, 15) = 15
        assert all(s <= 15.0 for s in sleep_calls)
        assert sleep_calls[1] == pytest.approx(15.0)
        assert sleep_calls[2] == pytest.approx(15.0)

    def test_apierror_with_response_none_not_retried(self):
        """APIError whose status_code raises AttributeError (response=None) is re-raised."""
        http_error = MagicMock(spec=requests.exceptions.HTTPError)
        http_error.response = None
        err = APIError('{"code": 0, "message": "unknown"}', http_error)
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                call_with_retry_on_429(call)

        assert exc_info.value is err
        assert call.call_count == 1


class TestCallWithRetryOnTransient:
    """Read-only-call helper: retries 429 AND transient read/connect timeouts.

    submit_order deliberately stays on call_with_retry_on_429 (an ambiguous
    write timeout must not be blindly retried), so these tests pin the
    read-path resilience added after the 2026-06-17 co-run soak crashed three
    runners on an unhandled Alpaca ReadTimeout.
    """

    def test_retries_on_read_timeout_then_succeeds(self):
        err = requests.exceptions.ReadTimeout("read timed out")
        call = MagicMock(side_effect=[err, err, "ok"])

        with patch("time.sleep"):
            result = call_with_retry_on_transient(call)

        assert result == "ok"
        assert call.call_count == 3

    def test_exhausts_on_persistent_timeout_and_reraises(self):
        err = requests.exceptions.ReadTimeout("read timed out")
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(requests.exceptions.ReadTimeout) as exc_info:
                call_with_retry_on_transient(call, max_attempts=4)

        assert exc_info.value is err
        assert call.call_count == 4

    def test_retries_on_connect_timeout_and_connection_error(self):
        err1 = requests.exceptions.ConnectTimeout("connect timed out")
        err2 = requests.exceptions.ConnectionError("conn reset")
        call = MagicMock(side_effect=[err1, err2, "ok"])

        with patch("time.sleep"):
            result = call_with_retry_on_transient(call)

        assert result == "ok"
        assert call.call_count == 3

    def test_still_retries_429(self):
        err = _make_429_api_error()
        call = MagicMock(side_effect=[err, "ok"])

        with patch("time.sleep"):
            result = call_with_retry_on_transient(call)

        assert result == "ok"
        assert call.call_count == 2

    def test_does_not_retry_unexpected_error(self):
        """A non-transient, non-429 error bubbles up on the first failure."""
        err = _make_non_429_api_error(422)
        call = MagicMock(side_effect=err)

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                call_with_retry_on_transient(call)

        assert exc_info.value is err
        assert call.call_count == 1

    def test_retries_on_tls_eof_ssl_error_then_succeeds(self):
        """TLS-teardown classes are transient: pin SSLError coverage.

        requests wraps urllib3's mid-handshake/mid-read TLS teardown
        (``ssl.SSLEOFError`` — UNEXPECTED_EOF_WHILE_READING) in
        ``requests.exceptions.SSLError``, a ``ConnectionError`` subclass, so
        the transient set covers it by subclassing. Pinned explicitly so a
        future narrowing of ``_TRANSIENT_READ_ERRORS`` (e.g. swapping
        ``ConnectionError`` for an enumerated list) cannot silently drop the
        class that killed four daily runners mid close-eval on 2026-07-23.
        """
        err = requests.exceptions.SSLError(
            "HTTPSConnectionPool(host='data.alpaca.markets', port=443): Max retries "
            "exceeded with url: /v2/stocks/bars (Caused by SSLError(SSLEOFError(8, "
            "'[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol')))"
        )
        call = MagicMock(side_effect=[err, "ok"])

        with patch("time.sleep"):
            result = call_with_retry_on_transient(call)

        assert result == "ok"
        assert call.call_count == 2
