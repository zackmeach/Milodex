"""Custom exceptions for the broker layer.

These exceptions are broker-agnostic -- the rest of the system catches
these without knowing which broker implementation raised them.
"""


class BrokerError(Exception):
    """Base exception for all broker errors."""


class BrokerConnectionError(BrokerError):
    """Cannot reach the broker API."""


class BrokerAuthError(BrokerError):
    """Authentication failed (bad or expired credentials)."""


class InsufficientFundsError(BrokerError):
    """Not enough buying power to execute the order."""


class OrderRejectedError(BrokerError):
    """Broker rejected the order.

    Attributes:
        reason: Human-readable rejection reason from the broker.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Order rejected: {reason}")
