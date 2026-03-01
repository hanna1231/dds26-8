"""
Fault tolerance tests covering FAULT-01 through FAULT-04.

Tests run in-process against real Redis and real gRPC test servers.
Docker-based kill tests are marked with @pytest.mark.requires_docker.
"""
import asyncio
import json
import time
from time import monotonic
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import grpc
import grpc.aio

# Orchestrator modules are on sys.path from conftest
from circuit import stock_breaker, payment_breaker
from circuitbreaker import CircuitBreakerError, STATE_CLOSED, STATE_OPEN
import client as _client_mod
from saga import create_saga_record, get_saga, transition_state
from grpc_server import retry_forward, run_checkout, run_compensation
from recovery import recover_incomplete_sagas, resume_saga, STALENESS_THRESHOLD_SECONDS


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


async def seed_saga(db, order_id, state, updated_at, **extra_fields):
    """Seed a SAGA record directly in Redis for recovery tests."""
    saga_key = f"{{saga:{order_id}}}"
    mapping = {
        "order_id": order_id,
        "state": state,
        "user_id": extra_fields.get("user_id", "test-user-1"),
        "total_cost": str(extra_fields.get("total_cost", 10)),
        "items_json": extra_fields.get(
            "items_json", json.dumps([{"item_id": "test-item-1", "quantity": 1}])
        ),
        "stock_reserved": extra_fields.get("stock_reserved", "0"),
        "payment_charged": extra_fields.get("payment_charged", "0"),
        "refund_done": extra_fields.get("refund_done", "0"),
        "stock_restored": extra_fields.get("stock_restored", "0"),
        "error_message": extra_fields.get("error_message", ""),
        "started_at": str(updated_at),
        "updated_at": str(updated_at),
    }
    await db.hset(saga_key, mapping=mapping)


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
    FAULT-04 + FAULT-03: When stock_breaker is open, run_checkout compensates and returns failure.
    """
    _reset_breaker(stock_breaker)
    try:
        # Trip the stock breaker
        _open_breaker(stock_breaker)

        result = await run_checkout(
            orchestrator_db,
            "order-cb-1",
            "test-user-1",
            [{"item_id": "test-item-1", "quantity": 1}],
            10,
        )

        assert result["success"] is False, "Checkout should fail when circuit breaker is OPEN"
        assert "service unavailable" in result["error_message"].lower(), (
            f"Error message should mention 'service unavailable', got: {result['error_message']}"
        )

        # SAGA should be in terminal FAILED state (compensation ran)
        saga = await get_saga(orchestrator_db, "order-cb-1")
        assert saga is not None, "SAGA record should exist"
        assert saga["state"] == "FAILED", f"SAGA should be FAILED, got: {saga['state']}"
    finally:
        _reset_breaker(stock_breaker)


# ---------------------------------------------------------------------------
# FAULT-01: Bounded forward retry
# ---------------------------------------------------------------------------


async def test_retry_forward_exhaustion():
    """
    FAULT-01: retry_forward gives up after max_attempts and returns the failure result.
    """
    async def always_fails():
        return {"success": False, "error_message": "fail"}

    result = await retry_forward(always_fails, max_attempts=3, base=0.01, cap=0.1)
    assert result["success"] is False
    assert result["error_message"] == "fail"


async def test_retry_forward_propagates_circuit_breaker_error():
    """
    FAULT-04: retry_forward re-raises CircuitBreakerError immediately (no retries).
    """
    call_count = 0

    async def raises_circuit_breaker():
        nonlocal call_count
        call_count += 1
        raise CircuitBreakerError(stock_breaker)

    with pytest.raises(CircuitBreakerError):
        await retry_forward(raises_circuit_breaker, max_attempts=3, base=0.01)

    assert call_count == 1, "CircuitBreakerError should not be retried — should abort after 1 call"


async def test_retry_forward_succeeds_on_retry():
    """
    FAULT-01: retry_forward succeeds when a later attempt returns success.
    """
    call_count = 0

    async def fails_twice_then_succeeds():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return {"success": False, "error_message": "transient"}
        return {"success": True, "error_message": ""}

    result = await retry_forward(fails_twice_then_succeeds, max_attempts=3, base=0.01, cap=0.1)
    assert result["success"] is True
    assert call_count == 3


# ---------------------------------------------------------------------------
# FAULT-02 + FAULT-03: SAGA startup recovery
# ---------------------------------------------------------------------------


async def test_recovery_resolves_stale_started_saga(
    clean_orchestrator_db, grpc_clients, seed_test_data, orchestrator_db
):
    """
    FAULT-02 + FAULT-03: Recovery scanner drives stale STARTED SAGA to COMPLETED.
    """
    stale_ts = int(time.time()) - 600  # 10 minutes ago, past staleness threshold
    await seed_saga(orchestrator_db, "order-rec-started-1", "STARTED", stale_ts)

    await recover_incomplete_sagas(orchestrator_db)

    saga = await get_saga(orchestrator_db, "order-rec-started-1")
    assert saga is not None
    assert saga["state"] in ("COMPLETED", "FAILED"), (
        f"SAGA should be in terminal state after recovery, got: {saga['state']}"
    )
    # Forward recovery should succeed for valid test data
    assert saga["state"] == "COMPLETED", (
        f"SAGA with valid test-item-1 / test-user-1 should forward-recover to COMPLETED, got: {saga['state']}"
    )


async def test_recovery_resolves_stale_compensating_saga(
    clean_orchestrator_db, grpc_clients, seed_test_data, orchestrator_db
):
    """
    FAULT-02 + FAULT-03: Recovery scanner drives stale COMPENSATING SAGA to FAILED.
    """
    stale_ts = int(time.time()) - 600
    await seed_saga(
        orchestrator_db,
        "order-rec-comp-1",
        "COMPENSATING",
        stale_ts,
        payment_charged="1",
        refund_done="0",
        stock_reserved="1",
        stock_restored="0",
    )

    await recover_incomplete_sagas(orchestrator_db)

    saga = await get_saga(orchestrator_db, "order-rec-comp-1")
    assert saga is not None
    assert saga["state"] == "FAILED", (
        f"COMPENSATING SAGA should resolve to FAILED after recovery, got: {saga['state']}"
    )


async def test_recovery_skips_fresh_sagas(clean_orchestrator_db, orchestrator_db):
    """
    FAULT-02: Recovery scanner skips SAGAs younger than STALENESS_THRESHOLD_SECONDS.
    """
    fresh_ts = int(time.time())  # right now, not stale
    await seed_saga(orchestrator_db, "order-rec-fresh-1", "STARTED", fresh_ts)

    await recover_incomplete_sagas(orchestrator_db)

    saga = await get_saga(orchestrator_db, "order-rec-fresh-1")
    assert saga is not None
    assert saga["state"] == "STARTED", (
        f"Fresh SAGA should not be touched by recovery, got: {saga['state']}"
    )


async def test_recovery_skips_terminal_sagas(clean_orchestrator_db, orchestrator_db):
    """
    FAULT-02: Recovery scanner does not modify terminal SAGAs (COMPLETED, FAILED).
    """
    stale_ts = int(time.time()) - 600

    await seed_saga(orchestrator_db, "order-rec-terminal-comp", "COMPLETED", stale_ts)
    await seed_saga(orchestrator_db, "order-rec-terminal-fail", "FAILED", stale_ts)

    await recover_incomplete_sagas(orchestrator_db)

    comp_saga = await get_saga(orchestrator_db, "order-rec-terminal-comp")
    fail_saga = await get_saga(orchestrator_db, "order-rec-terminal-fail")

    assert comp_saga["state"] == "COMPLETED", "COMPLETED SAGA should remain COMPLETED"
    assert fail_saga["state"] == "FAILED", "FAILED SAGA should remain FAILED"


async def test_no_sagas_stranded_after_recovery(
    clean_orchestrator_db, grpc_clients, seed_test_data, orchestrator_db
):
    """
    FAULT-02 + FAULT-03: After recovery, no SAGA remains in a non-terminal state.

    Seeds 3 stale non-terminal SAGAs and verifies all reach terminal state.
    """
    stale_ts = int(time.time()) - 600

    # STARTED saga — forward recovery should drive to COMPLETED
    await seed_saga(orchestrator_db, "order-strand-1", "STARTED", stale_ts)

    # STOCK_RESERVED saga — forward recovery: charge payment -> COMPLETED
    await seed_saga(
        orchestrator_db,
        "order-strand-2",
        "STOCK_RESERVED",
        stale_ts,
        stock_reserved="1",
    )

    # PAYMENT_CHARGED saga — forward recovery: mark COMPLETED
    await seed_saga(
        orchestrator_db,
        "order-strand-3",
        "PAYMENT_CHARGED",
        stale_ts,
        stock_reserved="1",
        payment_charged="1",
    )

    await recover_incomplete_sagas(orchestrator_db)

    # Scan all {saga:*} keys and verify every SAGA is in a terminal state
    terminal_states = {"COMPLETED", "FAILED"}
    non_terminal_keys = []
    async for key in orchestrator_db.scan_iter(match="{saga:*", count=100):
        raw = await orchestrator_db.hgetall(key)
        saga = {k.decode(): v.decode() for k, v in raw.items()}
        if saga.get("state") not in terminal_states:
            non_terminal_keys.append((key, saga.get("state")))

    assert not non_terminal_keys, (
        f"Found SAGAs in non-terminal state after recovery: {non_terminal_keys}"
    )
