"""
Unit tests for 2PC participant operations (prepare/commit/abort) for Stock and Payment.

Covers: TPC-02 (Stock 2PC), TPC-03 (Payment 2PC)

All tests use:
  - Real Redis (db=0 for stock/payment data)
  - Direct function calls to operations modules (no gRPC)
  - Unique uuid-based IDs per test to prevent cross-test interference

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators needed.
"""

import os
import sys
import uuid

from msgspec import msgpack, Struct


# ---------------------------------------------------------------------------
# sys.path manipulation to import operations modules
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stock_path = os.path.join(_repo_root, "stock")
_payment_path = os.path.join(_repo_root, "payment")

# Import stock operations (clear cache first to avoid conftest pollution)
if "operations" in sys.modules:
    del sys.modules["operations"]
sys.path.insert(0, _stock_path)
import operations as stock_ops  # noqa: E402
sys.path.pop(0)

# Import payment operations (clear operations from cache to avoid cross-service collision)
if "operations" in sys.modules:
    del sys.modules["operations"]
sys.path.insert(0, _payment_path)
import operations as payment_ops  # noqa: E402
sys.path.pop(0)


# ---------------------------------------------------------------------------
# Structs for seeding Redis test data
# ---------------------------------------------------------------------------

class StockValue(Struct):
    stock: int
    price: int


class UserValue(Struct):
    credit: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


async def seed_item(db, item_id: str, stock: int, price: int) -> None:
    await db.set(f"{{item:{item_id}}}", msgpack.encode(StockValue(stock=stock, price=price)))


async def seed_user(db, user_id: str, credit: int) -> None:
    await db.set(f"{{user:{user_id}}}", msgpack.encode(UserValue(credit=credit)))


async def get_item_stock(db, item_id: str) -> int:
    raw = await db.get(f"{{item:{item_id}}}")
    if raw is None:
        raise KeyError(f"item {item_id!r} not found")
    return msgpack.decode(raw, type=StockValue).stock


async def get_user_credit(db, user_id: str) -> int:
    raw = await db.get(f"{{user:{user_id}}}")
    if raw is None:
        raise KeyError(f"user {user_id!r} not found")
    return msgpack.decode(raw, type=UserValue).credit


async def get_hold_key(db, key: str):
    """Return hold key value (bytes) or None if not present."""
    return await db.get(key)


# ---------------------------------------------------------------------------
# Stock 2PC participant tests (TPC-02)
# ---------------------------------------------------------------------------

async def test_stock_prepare_reserves(redis_db):
    """prepare_stock deducts stock and creates hold key with quantity."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    result = await stock_ops.prepare_stock(redis_db, item_id, 3, order_id)
    assert result["success"] is True
    assert result["error_message"] == ""

    # Stock should be deducted
    assert await get_item_stock(redis_db, item_id) == 7

    # Hold key should exist with quantity
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"
    hold_val = await get_hold_key(redis_db, hold_key)
    assert hold_val is not None
    assert int(hold_val) == 3


async def test_stock_prepare_idempotent(redis_db):
    """Second prepare_stock for same order returns ALREADY_PREPARED, stock unchanged."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    r1 = await stock_ops.prepare_stock(redis_db, item_id, 3, order_id)
    assert r1["success"] is True

    stock_after_first = await get_item_stock(redis_db, item_id)

    r2 = await stock_ops.prepare_stock(redis_db, item_id, 3, order_id)
    assert r2["success"] is True  # idempotent success

    # Stock must not change from duplicate
    assert await get_item_stock(redis_db, item_id) == stock_after_first


async def test_stock_prepare_insufficient(redis_db):
    """prepare_stock with insufficient stock returns error, no hold key created."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=2, price=5)

    result = await stock_ops.prepare_stock(redis_db, item_id, 5, order_id)
    assert result["success"] is False
    assert "insufficient" in result["error_message"].lower()

    # Stock unchanged
    assert await get_item_stock(redis_db, item_id) == 2

    # No hold key
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None


async def test_stock_commit_finalizes(redis_db):
    """After prepare, commit_stock deletes hold key, stock remains deducted."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    await stock_ops.prepare_stock(redis_db, item_id, 4, order_id)
    assert await get_item_stock(redis_db, item_id) == 6

    result = await stock_ops.commit_stock(redis_db, item_id, order_id)
    assert result["success"] is True

    # Hold key should be gone
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None

    # Stock remains deducted
    assert await get_item_stock(redis_db, item_id) == 6


async def test_stock_commit_idempotent(redis_db):
    """Second commit (hold already gone) returns success."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    await stock_ops.prepare_stock(redis_db, item_id, 2, order_id)
    await stock_ops.commit_stock(redis_db, item_id, order_id)

    # Second commit -- idempotent
    result = await stock_ops.commit_stock(redis_db, item_id, order_id)
    assert result["success"] is True


async def test_stock_abort_releases(redis_db):
    """After prepare, abort_stock restores stock and deletes hold key."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    await stock_ops.prepare_stock(redis_db, item_id, 3, order_id)
    assert await get_item_stock(redis_db, item_id) == 7

    result = await stock_ops.abort_stock(redis_db, item_id, order_id)
    assert result["success"] is True

    # Stock restored
    assert await get_item_stock(redis_db, item_id) == 10

    # Hold key gone
    hold_key = f"{{item:{item_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None


async def test_stock_abort_idempotent(redis_db):
    """Abort when no hold key returns success (ALREADY_ABORTED)."""
    item_id = new_id("item-")
    order_id = new_id("order-")
    await seed_item(redis_db, item_id, stock=10, price=5)

    # No prepare -- abort directly
    result = await stock_ops.abort_stock(redis_db, item_id, order_id)
    assert result["success"] is True

    # Stock unchanged
    assert await get_item_stock(redis_db, item_id) == 10


# ---------------------------------------------------------------------------
# Payment 2PC participant tests (TPC-03)
# ---------------------------------------------------------------------------

async def test_payment_prepare_reserves(redis_db):
    """prepare_payment deducts credit and creates hold key with amount."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=100)

    result = await payment_ops.prepare_payment(redis_db, user_id, 30, order_id)
    assert result["success"] is True
    assert result["error_message"] == ""

    # Credit should be deducted
    assert await get_user_credit(redis_db, user_id) == 70

    # Hold key should exist with amount
    hold_key = f"{{user:{user_id}}}:hold:{order_id}"
    hold_val = await get_hold_key(redis_db, hold_key)
    assert hold_val is not None
    assert int(hold_val) == 30


async def test_payment_prepare_idempotent(redis_db):
    """Second prepare returns ALREADY_PREPARED, credit unchanged."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=100)

    r1 = await payment_ops.prepare_payment(redis_db, user_id, 30, order_id)
    assert r1["success"] is True

    credit_after_first = await get_user_credit(redis_db, user_id)

    r2 = await payment_ops.prepare_payment(redis_db, user_id, 30, order_id)
    assert r2["success"] is True

    assert await get_user_credit(redis_db, user_id) == credit_after_first


async def test_payment_prepare_insufficient(redis_db):
    """prepare with insufficient credit returns error."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=10)

    result = await payment_ops.prepare_payment(redis_db, user_id, 50, order_id)
    assert result["success"] is False
    assert "insufficient" in result["error_message"].lower()

    # Credit unchanged
    assert await get_user_credit(redis_db, user_id) == 10

    # No hold key
    hold_key = f"{{user:{user_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None


async def test_payment_commit_finalizes(redis_db):
    """commit deletes hold key, credit remains deducted."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=100)

    await payment_ops.prepare_payment(redis_db, user_id, 40, order_id)
    assert await get_user_credit(redis_db, user_id) == 60

    result = await payment_ops.commit_payment(redis_db, user_id, order_id)
    assert result["success"] is True

    hold_key = f"{{user:{user_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None

    assert await get_user_credit(redis_db, user_id) == 60


async def test_payment_abort_releases(redis_db):
    """abort restores credit, deletes hold key."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=100)

    await payment_ops.prepare_payment(redis_db, user_id, 25, order_id)
    assert await get_user_credit(redis_db, user_id) == 75

    result = await payment_ops.abort_payment(redis_db, user_id, order_id)
    assert result["success"] is True

    assert await get_user_credit(redis_db, user_id) == 100

    hold_key = f"{{user:{user_id}}}:hold:{order_id}"
    assert await get_hold_key(redis_db, hold_key) is None


async def test_payment_abort_idempotent(redis_db):
    """abort when no hold returns success (ALREADY_ABORTED)."""
    user_id = new_id("user-")
    order_id = new_id("order-")
    await seed_user(redis_db, user_id, credit=100)

    result = await payment_ops.abort_payment(redis_db, user_id, order_id)
    assert result["success"] is True

    assert await get_user_credit(redis_db, user_id) == 100
