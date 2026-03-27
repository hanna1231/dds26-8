"""
Fault tolerance tests covering FAULT-01 through FAULT-04.

Tests run in-process against real Redis and real gRPC test servers.
Docker-based kill tests are marked with @pytest.mark.requires_docker.
"""
import asyncio
from time import monotonic
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import grpc
import grpc.aio

# Orchestrator modules are on sys.path from conftest
from circuit import stock_breaker, payment_breaker
from circuitbreaker import CircuitBreakerError, STATE_CLOSED, STATE_OPEN
import client as _client_mod
from workflow_store import WorkflowStore
from workflow_engine import WorkflowEngine
from checkout_workflow import make_checkout_workflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rpc_error(code=grpc.StatusCode.UNAVAILABLE, details="service unavailable"):
    """Create a grpc.aio.AioRpcError for use in mocks."""
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details=details,
    )


def _open_breaker(breaker):
    """Manually open a circuit breaker (skips the real gRPC calls)."""
    breaker._state = STATE_OPEN
    breaker._failure_count = breaker._failure_threshold
    breaker._opened = monotonic()


def _reset_breaker(breaker):
    """Reset breaker to closed state."""
    breaker.reset()


# ---------------------------------------------------------------------------
# FAULT-04: Circuit breaker tripping and recovery
# ---------------------------------------------------------------------------


async def test_circuit_breaker_trips_after_threshold(grpc_clients):
    """
    FAULT-04: Circuit breaker opens after failure_threshold consecutive gRPC failures.

    After threshold failures, the next call raises CircuitBreakerError (not AioRpcError).
    """
    _reset_breaker(stock_breaker)
    try:
        rpc_err = _make_rpc_error()
        mock_reserve = AsyncMock(side_effect=rpc_err)

        with patch.object(_client_mod, "_stock_stub") as mock_stub:
            mock_stub.ReserveStock = mock_reserve

            threshold = stock_breaker.FAILURE_THRESHOLD
            for _ in range(threshold):
                try:
                    await _client_mod.reserve_stock("test-item-1", 1, "key-trip-test")
                except grpc.aio.AioRpcError:
                    pass  # expected until threshold

            # Now the breaker should be OPEN — next call raises CircuitBreakerError
            with pytest.raises(CircuitBreakerError):
                await _client_mod.reserve_stock("test-item-1", 1, "key-trip-test")

        assert stock_breaker.opened, "stock_breaker should be in OPEN state"
    finally:
        _reset_breaker(stock_breaker)


async def test_circuit_breaker_half_open_recovery(grpc_clients):
    """
    FAULT-04: Circuit breaker transitions to CLOSED after successful half-open probe.
    """
    _reset_breaker(stock_breaker)
    try:
        # Trip the breaker by manually opening it
        _open_breaker(stock_breaker)
        assert stock_breaker.opened

        # Fast-forward recovery timeout: set _opened to past so breaker enters half-open
        stock_breaker._opened = monotonic() - stock_breaker.RECOVERY_TIMEOUT - 1

        # Now patch stub to return a successful response
        success_resp = MagicMock()
        success_resp.success = True
        success_resp.error_message = ""
        mock_reserve = AsyncMock(return_value=success_resp)

        with patch.object(_client_mod, "_stock_stub") as mock_stub:
            mock_stub.ReserveStock = mock_reserve
            result = await _client_mod.reserve_stock("test-item-1", 1, "key-half-open")

        assert result["success"] is True, "Half-open probe should succeed"
        assert stock_breaker.closed, "Breaker should return to CLOSED after successful probe"
    finally:
        _reset_breaker(stock_breaker)


async def test_independent_breakers(grpc_clients):
    """
    FAULT-04: Tripping stock_breaker does not affect payment_breaker.
    """
    _reset_breaker(stock_breaker)
    _reset_breaker(payment_breaker)
    try:
        # Manually open stock breaker
        _open_breaker(stock_breaker)
        assert stock_breaker.opened, "stock_breaker should be OPEN"
        assert payment_breaker.closed, "payment_breaker should still be CLOSED"

        # Payment calls should still succeed (payment breaker is closed)
        success_resp = MagicMock()
        success_resp.success = True
        success_resp.error_message = ""
        success_resp.credit = 1000
        mock_check = AsyncMock(return_value=success_resp)

        with patch.object(_client_mod, "_payment_stub") as mock_stub:
            mock_stub.CheckPayment = mock_check
            result = await _client_mod.check_payment("test-user-1")

        assert result["success"] is True, "Payment call should succeed despite stock breaker OPEN"
        assert payment_breaker.closed, "payment_breaker should still be CLOSED"
    finally:
        _reset_breaker(stock_breaker)
        _reset_breaker(payment_breaker)


async def test_run_checkout_compensates_on_circuit_breaker(
    clean_orchestrator_db, grpc_clients, seed_test_data, orchestrator_db
):
    """
    FAULT-04 + FAULT-03: When stock_breaker is open, engine.execute() compensates and returns failure.
    """
    _reset_breaker(stock_breaker)
    try:
        store = WorkflowStore(orchestrator_db)
        engine = WorkflowEngine(store=store, db=orchestrator_db)

        # Trip the stock breaker
        _open_breaker(stock_breaker)

        result = await engine.execute(
            "order-cb-1",
            make_checkout_workflow("saga"),
            {
                "order_id": "order-cb-1",
                "user_id": "test-user-1",
                "items": [{"item_id": "test-item-1", "quantity": 1}],
                "total_cost": 10,
            },
        )

        assert result["success"] is False, "Checkout should fail when circuit breaker is OPEN"

        # Workflow should be in terminal FAILED state (compensation ran)
        record = await store.get("order-cb-1")
        assert record is not None, "Workflow record should exist"
        assert record["state"] == "FAILED", f"Workflow should be FAILED, got: {record['state']}"
    finally:
        _reset_breaker(stock_breaker)
