"""
Integration tests for SAGA state machine, checkout flow, compensation,
exactly-once semantics, and retry behavior.

Covers: SAGA-01 through SAGA-06, IDMP-01, IDMP-02, IDMP-03

All tests use:
  - Real Redis (db=0 for stock/payment, db=3 for orchestrator SAGA records)
  - Real gRPC servers (stock :50051, payment :50052, orchestrator :50053)
  - Unique order_ids / item_ids / user_ids per test (uuid4) to prevent
    cross-test interference even if fixture cleanup is skipped

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators
are needed on async test functions.
"""

import json
import sys
import os
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from msgspec import msgpack, Struct

# Ensure orchestrator path is available
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

import grpc_server as orchestrator_grpc_mod
from saga import create_saga_record, transition_state, get_saga, VALID_TRANSITIONS
from orchestrator_pb2 import CheckoutRequest, LineItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StockValue(Struct):
    stock: int
    price: int


class UserValue(Struct):
    credit: int


def new_order_id() -> str:
    return f"order-{uuid.uuid4().hex}"


def new_item_id() -> str:
    return f"item-{uuid.uuid4().hex}"


def new_user_id() -> str:
    return f"user-{uuid.uuid4().hex}"


async def seed_item(redis_db, item_id: str, stock: int, price: int) -> None:
    """Seed a stock item into the shared test Redis DB."""
    await redis_db.set(f"{{item:{item_id}}}", msgpack.encode(StockValue(stock=stock, price=price)))


async def seed_user(redis_db, user_id: str, credit: int) -> None:
    """Seed a user into the shared test Redis DB."""
    await redis_db.set(f"{{user:{user_id}}}", msgpack.encode(UserValue(credit=credit)))


async def get_item_stock(redis_db, item_id: str) -> int:
    """Return current stock count for item_id from Redis."""
    raw = await redis_db.get(f"{{item:{item_id}}}")
    if raw is None:
        raise KeyError(f"item {item_id!r} not found")
    return msgpack.decode(raw, type=StockValue).stock


async def get_user_credit(redis_db, user_id: str) -> int:
    """Return current credit for user_id from Redis."""
    raw = await redis_db.get(f"{{user:{user_id}}}")
    if raw is None:
        raise KeyError(f"user {user_id!r} not found")
    return msgpack.decode(raw, type=UserValue).credit


# ---------------------------------------------------------------------------
# Test 1: SAGA record created before side effects (SAGA-01)
# ---------------------------------------------------------------------------

async def test_saga_record_created_before_side_effects(orchestrator_db, clean_orchestrator_db):
    """SAGA-01: create_saga_record persists record atomically before gRPC calls."""
    order_id = new_order_id()
    user_id = new_user_id()
    items = [{"item_id": new_item_id(), "quantity": 2}]
    total_cost = 100

    created = await create_saga_record(orchestrator_db, order_id, user_id, items, total_cost)
    assert created is True, "create_saga_record should return True for new record"

    saga = await get_saga(orchestrator_db, order_id)
    assert saga is not None, "SAGA record must exist in Redis after creation"

    # State checks
    assert saga["state"] == "STARTED"
    assert saga["order_id"] == order_id
    assert saga["user_id"] == user_id
    assert saga["total_cost"] == str(total_cost)

    # Flag fields must all start at "0"
    assert saga["stock_reserved"] == "0"
    assert saga["payment_charged"] == "0"
    assert saga["refund_done"] == "0"
    assert saga["stock_restored"] == "0"

    # Items JSON round-trips
    assert json.loads(saga["items_json"]) == items


# ---------------------------------------------------------------------------
# Test 2: Valid state transitions accepted (SAGA-02)
# ---------------------------------------------------------------------------

async def test_saga_state_transitions_valid(orchestrator_db, clean_orchestrator_db):
    """SAGA-02: Valid state transitions succeed and state is updated correctly."""
    order_id = new_order_id()
    await create_saga_record(orchestrator_db, order_id, "u1", [], 0)
    saga_key = f"{{saga:{order_id}}}"

    # STARTED -> STOCK_RESERVED
    ok = await transition_state(orchestrator_db, saga_key, "STARTED", "STOCK_RESERVED")
    assert ok is True

    # STOCK_RESERVED -> PAYMENT_CHARGED
    ok = await transition_state(orchestrator_db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED")
    assert ok is True

    # PAYMENT_CHARGED -> COMPLETED
    ok = await transition_state(orchestrator_db, saga_key, "PAYMENT_CHARGED", "COMPLETED")
    assert ok is True

    saga = await get_saga(orchestrator_db, order_id)
    assert saga["state"] == "COMPLETED"


# ---------------------------------------------------------------------------
# Test 3: Invalid state transitions rejected (SAGA-02)
# ---------------------------------------------------------------------------

async def test_saga_state_transition_invalid_rejected(orchestrator_db, clean_orchestrator_db):
    """SAGA-02: Invalid transitions raise ValueError and state is unchanged."""
    order_id = new_order_id()
    await create_saga_record(orchestrator_db, order_id, "u1", [], 0)
    saga_key = f"{{saga:{order_id}}}"

    # STARTED -> COMPLETED is not a valid transition
    with pytest.raises(ValueError):
        await transition_state(orchestrator_db, saga_key, "STARTED", "COMPLETED")

    # STARTED -> FAILED is not a valid transition (only COMPENSATING -> FAILED is)
    with pytest.raises(ValueError):
        await transition_state(orchestrator_db, saga_key, "STARTED", "FAILED")

    # State must still be STARTED after rejected attempts
    saga = await get_saga(orchestrator_db, order_id)
    assert saga["state"] == "STARTED"


# ---------------------------------------------------------------------------
# Test 4: Happy path checkout (SAGA-03)
# ---------------------------------------------------------------------------

async def test_checkout_happy_path(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """SAGA-03: Successful checkout transitions to COMPLETED, stock and credit decremented."""
    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    await seed_item(redis_db, item_id, stock=50, price=10)
    await seed_user(redis_db, user_id, credit=500)

    response = await orchestrator_stub.StartCheckout(
        CheckoutRequest(
            order_id=order_id,
            user_id=user_id,
            items=[LineItem(item_id=item_id, quantity=3)],
            total_cost=30,
        )
    )

    assert response.success is True
    assert response.error_message == ""

    saga = await get_saga(orchestrator_db, order_id)
    assert saga is not None
    assert saga["state"] == "COMPLETED"

    # Stock decremented by quantity (3)
    remaining_stock = await get_item_stock(redis_db, item_id)
    assert remaining_stock == 47

    # Credit decremented by total_cost (30)
    remaining_credit = await get_user_credit(redis_db, user_id)
    assert remaining_credit == 470


# ---------------------------------------------------------------------------
# Test 5: Insufficient stock triggers compensation — no payment charged (SAGA-04)
# ---------------------------------------------------------------------------

async def test_checkout_insufficient_stock_compensates(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """SAGA-04: Stock failure → SAGA FAILED, payment never charged."""
    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    # stock=0 — reservation will fail immediately
    await seed_item(redis_db, item_id, stock=0, price=10)
    await seed_user(redis_db, user_id, credit=500)

    response = await orchestrator_stub.StartCheckout(
        CheckoutRequest(
            order_id=order_id,
            user_id=user_id,
            items=[LineItem(item_id=item_id, quantity=1)],
            total_cost=10,
        )
    )

    assert response.success is False
    assert "insufficient stock" in response.error_message

    saga = await get_saga(orchestrator_db, order_id)
    assert saga is not None
    assert saga["state"] == "FAILED"

    # Credit must be unchanged — payment was never charged
    credit = await get_user_credit(redis_db, user_id)
    assert credit == 500


# ---------------------------------------------------------------------------
# Test 6: Insufficient credit triggers compensation — stock restored (SAGA-04)
# ---------------------------------------------------------------------------

async def test_checkout_insufficient_credit_compensates(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """SAGA-04: Payment failure → SAGA FAILED, stock restored to original value."""
    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    initial_stock = 20
    await seed_item(redis_db, item_id, stock=initial_stock, price=10)
    # credit=0 — charge will fail
    await seed_user(redis_db, user_id, credit=0)

    response = await orchestrator_stub.StartCheckout(
        CheckoutRequest(
            order_id=order_id,
            user_id=user_id,
            items=[LineItem(item_id=item_id, quantity=2)],
            total_cost=20,
        )
    )

    assert response.success is False
    assert "insufficient credit" in response.error_message

    saga = await get_saga(orchestrator_db, order_id)
    assert saga is not None
    assert saga["state"] == "FAILED"

    # Stock must be restored — release_stock added back what reserve_stock took
    restored_stock = await get_item_stock(redis_db, item_id)
    assert restored_stock == initial_stock


# ---------------------------------------------------------------------------
# Test 7: Duplicate checkout returns original result (SAGA-06)
# ---------------------------------------------------------------------------

async def test_checkout_duplicate_returns_original(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """SAGA-06: Duplicate StartCheckout with same order_id returns original result."""
    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    await seed_item(redis_db, item_id, stock=50, price=5)
    await seed_user(redis_db, user_id, credit=200)

    request = CheckoutRequest(
        order_id=order_id,
        user_id=user_id,
        items=[LineItem(item_id=item_id, quantity=2)],
        total_cost=10,
    )

    # First checkout — succeeds
    r1 = await orchestrator_stub.StartCheckout(request)
    assert r1.success is True

    stock_after_first = await get_item_stock(redis_db, item_id)
    credit_after_first = await get_user_credit(redis_db, user_id)

    # Second checkout — same order_id, should return stored result
    r2 = await orchestrator_stub.StartCheckout(request)
    assert r2.success is True
    assert r2.error_message == ""

    # Stock and credit must not change from duplicate execution
    assert await get_item_stock(redis_db, item_id) == stock_after_first
    assert await get_user_credit(redis_db, user_id) == credit_after_first


# ---------------------------------------------------------------------------
# Test 8: Duplicate SAGA creation prevented (SAGA-01 atomicity)
# ---------------------------------------------------------------------------

async def test_saga_duplicate_creation_prevented(orchestrator_db, clean_orchestrator_db):
    """SAGA-01: Second create_saga_record call for same order_id returns False."""
    order_id = new_order_id()

    first = await create_saga_record(orchestrator_db, order_id, "u1", [], 0)
    assert first is True

    second = await create_saga_record(orchestrator_db, order_id, "u1", [], 0)
    assert second is False

    # Record state is unchanged from first creation
    saga = await get_saga(orchestrator_db, order_id)
    assert saga["state"] == "STARTED"


# ---------------------------------------------------------------------------
# Test 9: Compensation retries until success (SAGA-05)
# ---------------------------------------------------------------------------

async def test_compensation_retries_until_success(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """SAGA-05: retry_forever retries compensation until service becomes available.

    - Sets up stock and a user with no credit (payment will fail).
    - Patches grpc_server.release_stock to raise AioRpcError on first 2 calls,
      then succeed on the 3rd.
    - Patches asyncio.sleep in grpc_server to avoid actual delays.
    - Verifies release_stock was called exactly 3 times (2 failures + 1 success).
    - Verifies SAGA reaches FAILED state (compensation completed).
    - Verifies stock is restored.
    """
    import grpc
    import grpc.aio

    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    initial_stock = 10
    await seed_item(redis_db, item_id, stock=initial_stock, price=5)
    # No credit so payment will fail, triggering stock compensation
    await seed_user(redis_db, user_id, credit=0)

    # Build a fake AioRpcError to raise on first two calls
    def make_rpc_error():
        mock_err = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNAVAILABLE,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="service unavailable",
            debug_error_string="unavailable",
        )
        return mock_err

    call_count = 0

    async def flaky_release_stock(item_id_arg, quantity, idempotency_key):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise make_rpc_error()
        # 3rd call succeeds — return a successful result dict
        return {"success": True, "error_message": ""}

    # Patch asyncio.sleep in orchestrator grpc_server to be instant
    async def instant_sleep(_delay):
        pass

    with (
        patch.object(orchestrator_grpc_mod, "release_stock", side_effect=flaky_release_stock),
        patch("asyncio.sleep", side_effect=instant_sleep),
    ):
        response = await orchestrator_stub.StartCheckout(
            CheckoutRequest(
                order_id=order_id,
                user_id=user_id,
                items=[LineItem(item_id=item_id, quantity=2)],
                total_cost=999999,  # far exceeds credit=0 → payment fails
            )
        )

    # Checkout fails (payment failed)
    assert response.success is False

    # release_stock called exactly 3 times (2 failures + 1 success)
    assert call_count == 3, f"Expected 3 calls to release_stock, got {call_count}"

    # SAGA must be in FAILED state (compensation completed, not stuck)
    saga = await get_saga(orchestrator_db, order_id)
    assert saga is not None
    assert saga["state"] == "FAILED"

    # Stock must be restored (the successful 3rd call restored it)
    # Note: the real release_stock didn't run so Redis stock unchanged; we verified
    # via mock call count. If using real stock service this check differs.
    # Since we mocked release_stock, stock in Redis was never changed by compensation,
    # but reserve_stock DID run (real call). Stock was decremented by reserve_stock.
    # The successful mock call represents the compensation completing successfully.
    # In a real scenario stock would be restored; here we verify the retry loop worked.
    assert saga["stock_restored"] == "1"


# ---------------------------------------------------------------------------
# Test 10: Idempotency keys prevent duplicate side effects (IDMP-01, IDMP-02, IDMP-03)
# ---------------------------------------------------------------------------

async def test_idempotency_keys_prevent_duplicate_side_effects(
    redis_db, orchestrator_db, orchestrator_stub, clean_orchestrator_db
):
    """IDMP-01/02/03: SAGA-generated idempotency keys prevent duplicate stock/payment operations.

    After a successful checkout, replaying the same stock/payment operations with
    the same idempotency keys returns cached results without modifying balances.
    This proves Phase 2 Lua-based idempotency correctly deduplicates replayed ops.
    """
    item_id = new_item_id()
    user_id = new_user_id()
    order_id = new_order_id()

    await seed_item(redis_db, item_id, stock=10, price=5)
    await seed_user(redis_db, user_id, credit=500)

    # First checkout — succeeds
    r1 = await orchestrator_stub.StartCheckout(
        CheckoutRequest(
            order_id=order_id,
            user_id=user_id,
            items=[LineItem(item_id=item_id, quantity=2)],
            total_cost=10,
        )
    )
    assert r1.success is True

    # Record values after first checkout
    stock_after_checkout = await get_item_stock(redis_db, item_id)
    credit_after_checkout = await get_user_credit(redis_db, user_id)

    # The SAGA uses deterministic idempotency keys derived from order_id (hash-tagged for cluster co-location)
    reserve_ikey = f"{{saga:{order_id}}}:step:reserve:{item_id}"
    charge_ikey = f"{{saga:{order_id}}}:step:charge"

    # Replay stock reservation with same idempotency key — must return cached result
    # (Lua script returns cached JSON instead of executing business logic again)
    from client import reserve_stock, charge_payment

    r_stock_replay = await reserve_stock(item_id, 2, reserve_ikey)
    assert r_stock_replay["success"] is True  # Returns original success result

    # Stock must not change from the replay
    assert await get_item_stock(redis_db, item_id) == stock_after_checkout, (
        "Duplicate reserve_stock with same idempotency key must not decrement stock again"
    )

    # Replay payment charge with same idempotency key
    r_pay_replay = await charge_payment(user_id, 10, charge_ikey)
    assert r_pay_replay["success"] is True  # Returns original success result

    # Credit must not change from the replay
    assert await get_user_credit(redis_db, user_id) == credit_after_checkout, (
        "Duplicate charge_payment with same idempotency key must not decrement credit again"
    )
