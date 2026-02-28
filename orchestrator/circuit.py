"""
Per-service circuit breaker instances for gRPC client functions.

Independent breakers for Stock and Payment services ensure that an outage
in one service does not trip the breaker for the other (locked decision).

Configuration (Claude's discretion from research):
  failure_threshold=5  -- open after 5 consecutive gRPC failures
  recovery_timeout=30  -- attempt half-open probe after 30 seconds
"""
import grpc.aio
from circuitbreaker import CircuitBreaker

stock_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    expected_exception=grpc.aio.AioRpcError,
    name="stock_service",
)

payment_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    expected_exception=grpc.aio.AioRpcError,
    name="payment_service",
)
