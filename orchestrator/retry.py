"""
Retry utilities for SAGA forward execution and compensation.

Extracted from grpc_server.py per D-04.

- retry_forever: infinite retry with exponential backoff (SAGA compensation)
- retry_forward: bounded retry up to max_attempts (SAGA forward steps)
"""
import asyncio
import random
import logging


async def retry_forever(fn, base: float = 0.5, cap: float = 30.0) -> dict:
    """
    Retry async callable *fn* until it returns a dict with success=True.

    Uses full-jitter exponential backoff: delay = min(cap, base * 2**attempt).

    Args:
        fn:   Async callable with no arguments returning {"success": bool, ...}.
        base: Initial backoff in seconds (default 0.5).
        cap:  Maximum backoff in seconds (default 30.0).

    Returns:
        The first successful result dict.
    """
    attempt = 0
    while True:
        try:
            result = await fn()
            if result.get("success"):
                return result
        except Exception as exc:
            logging.warning("compensation retry attempt %d failed: %s", attempt, exc)
        delay = min(cap, base * (2 ** attempt))
        await asyncio.sleep(delay)
        attempt += 1


async def retry_forward(fn, max_attempts: int = 3, base: float = 0.5, cap: float = 30.0) -> dict:
    """
    Retry async callable *fn* up to max_attempts times for forward SAGA steps.

    Uses full-jitter exponential backoff between attempts.
    CircuitBreakerError propagates immediately — never retried.

    Args:
        fn:           Async callable with no arguments returning {"success": bool, ...}.
        max_attempts: Maximum number of attempts before returning failure (default 3).
        base:         Initial backoff in seconds (default 0.5).
        cap:          Maximum backoff in seconds (default 30.0).

    Returns:
        The first successful result dict, or the last failure dict if all attempts exhausted.

    Raises:
        CircuitBreakerError: If the circuit breaker is open — propagated immediately.
    """
    from circuitbreaker import CircuitBreakerError
    last_result = {"success": False, "error_message": "max retries exceeded"}
    for attempt in range(max_attempts):
        try:
            result = await fn()
            if result.get("success"):
                return result
            last_result = result
        except CircuitBreakerError:
            raise  # breaker open -- propagate immediately, never retry
        except Exception as exc:
            last_result = {"success": False, "error_message": str(exc)}
        if attempt < max_attempts - 1:
            delay = min(cap, base * (2 ** attempt))
            jitter = random.uniform(0, delay)
            await asyncio.sleep(jitter)
    return last_result
