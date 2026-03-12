"""
Integration tests for queue infrastructure (Redis Streams request/reply).

Tests cover: command stream XADD, reply correlation, timeout handling,
stock consumer dispatch, payment consumer dispatch, ACK verification,
and end-to-end queue round-trip.
"""
import asyncio
import os
import sys

import pytest
import pytest_asyncio
import redis.asyncio as redis
from msgspec import msgpack

# ---------------------------------------------------------------------------
# sys.path setup for cross-service imports
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stock_path = os.path.join(_repo_root, "stock")
_payment_path = os.path.join(_repo_root, "payment")
_orchestrator_path = os.path.join(_repo_root, "orchestrator")

# Ensure orchestrator is on path for queue_client/reply_listener imports
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

# ---------------------------------------------------------------------------
# Import orchestrator queue modules (clean module cache first)
# ---------------------------------------------------------------------------
for _mod in ("queue_client", "reply_listener"):
    sys.modules.pop(_mod, None)

import reply_listener  # noqa: E402
import queue_client  # noqa: E402

# ---------------------------------------------------------------------------
# Import stock consumer (clean module cache)
# ---------------------------------------------------------------------------
if _stock_path not in sys.path:
    sys.path.insert(0, _stock_path)
for _mod in ("queue_consumer", "operations"):
    sys.modules.pop(_mod, None)

import queue_consumer as stock_consumer_mod  # noqa: E402
import operations as stock_operations  # noqa: E402

# ---------------------------------------------------------------------------
# Import payment consumer (clean module cache)
# ---------------------------------------------------------------------------
if _payment_path not in sys.path:
    sys.path.insert(0, _payment_path)
for _mod in ("queue_consumer", "operations"):
    sys.modules.pop(_mod, None)

import queue_consumer as payment_consumer_mod  # noqa: E402
import operations as payment_operations  # noqa: E402

# Re-bind stock consumer's operations reference (was overwritten by payment import)
stock_consumer_mod.operations = stock_operations


# ---------------------------------------------------------------------------
# Msgspec structs for test data seeding
# ---------------------------------------------------------------------------
class StockValue:
    pass


class UserValue:
    pass


# Use msgspec Structs from the operations modules directly
from msgspec import Struct  # noqa: E402


class _StockValue(Struct):
    stock: int
    price: int


class _UserValue(Struct):
    credit: int


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def queue_db():
    """Redis client for queue streams (db=4, separate from domain data)."""
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD", None)
    db = redis.Redis(host=host, port=port, password=password, db=4)
    await db.flushdb()
    yield db
    await db.flushdb()
    await db.aclose()


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def service_db():
    """Redis client for domain data (db=5, separate from queue and other tests)."""
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD", None)
    db = redis.Redis(host=host, port=port, password=password, db=5)
    await db.flushdb()
    yield db
    await db.flushdb()
    await db.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_command_stream_xadd(queue_db):
    """XADD to stock command stream creates a message readable by XREADGROUP."""
    stream = stock_consumer_mod.COMMAND_STREAM
    group = "test-group"
    consumer = "test-consumer-1"

    # Create consumer group
    await queue_db.xgroup_create(stream, group, id="0", mkstream=True)

    # Add a command
    await queue_db.xadd(stream, {
        "correlation_id": "test-corr-1",
        "command": "check_stock",
        "payload": '{"item_id": "test-1"}',
    })

    # Read via XREADGROUP
    response = await queue_db.xreadgroup(
        groupname=group, consumername=consumer,
        streams={stream: ">"}, count=1, block=1000,
    )
    assert response is not None
    assert len(response) == 1
    _stream_name, messages = response[0]
    assert len(messages) == 1
    msg_id, fields = messages[0]
    assert fields[b"command"] == b"check_stock"
    assert fields[b"correlation_id"] == b"test-corr-1"

    # ACK
    await queue_db.xack(stream, group, msg_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_reply_correlation(queue_db):
    """send_command returns correct result when reply_listener processes a matching reply."""
    import msgspec.json as mj

    await reply_listener.setup_reply_consumer_group(queue_db)
    queue_client.init_queue_client(queue_db)

    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(
        reply_listener.reply_listener(queue_db, stop_event)
    )

    # Set up a fake consumer that immediately replies
    stream = stock_consumer_mod.COMMAND_STREAM
    group = "fake-stock"
    try:
        await queue_db.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    async def fake_consumer():
        while not stop_event.is_set():
            response = await queue_db.xreadgroup(
                groupname=group, consumername="fake-1",
                streams={stream: ">"}, count=1, block=500,
            )
            if response:
                for _s, msgs in response:
                    for mid, fields in msgs:
                        cid = fields[b"correlation_id"].decode()
                        await queue_db.xadd(
                            reply_listener.REPLY_STREAM,
                            {
                                "correlation_id": cid,
                                "result": mj.encode({"success": True, "error_message": ""}).decode(),
                            },
                        )
                        await queue_db.xack(stream, group, mid)
                        return  # done after first message

    consumer_task = asyncio.create_task(fake_consumer())

    try:
        result = await queue_client.send_command(
            stream, "check_stock", {"item_id": "x"}, timeout=3.0,
        )
        assert result["success"] is True
    finally:
        stop_event.set()
        consumer_task.cancel()
        listener_task.cancel()
        for t in (consumer_task, listener_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        queue_client.close_queue_client()


@pytest.mark.asyncio(loop_scope="session")
async def test_reply_timeout(queue_db):
    """send_command returns timeout error dict when no reply arrives within timeout."""
    await reply_listener.setup_reply_consumer_group(queue_db)
    queue_client.init_queue_client(queue_db)

    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(
        reply_listener.reply_listener(queue_db, stop_event)
    )

    try:
        # No consumer running, so no reply will come
        result = await queue_client.send_command(
            stock_consumer_mod.COMMAND_STREAM,
            "check_stock",
            {"item_id": "nonexistent"},
            timeout=0.5,
        )
        assert result == {"success": False, "error_message": "queue timeout"}
    finally:
        stop_event.set()
        listener_task.cancel()
        try:
            await listener_task
        except (asyncio.CancelledError, Exception):
            pass
        queue_client.close_queue_client()


@pytest.mark.asyncio(loop_scope="session")
async def test_stock_consumer_reserve(queue_db, service_db):
    """Stock consumer processes reserve_stock command and publishes reply."""
    import msgspec.json as mj

    # Seed stock data
    await service_db.set(
        "{item:test-q-reserve}",
        msgpack.encode(_StockValue(stock=50, price=5)),
    )

    await stock_consumer_mod.setup_command_consumer_group(queue_db)

    stop_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        stock_consumer_mod.queue_consumer(service_db, queue_db, stop_event)
    )

    # Send command directly via XADD
    corr_id = "reserve-corr-1"
    await queue_db.xadd(
        stock_consumer_mod.COMMAND_STREAM,
        {
            "correlation_id": corr_id,
            "command": "reserve_stock",
            "payload": mj.encode({
                "item_id": "test-q-reserve",
                "quantity": 3,
                "idempotency_key": "idem-reserve-1",
            }).decode(),
        },
    )

    # Read reply from reply stream
    reply = None
    for _ in range(20):  # poll up to 2 seconds
        raw = await queue_db.xrange(stock_consumer_mod.REPLY_STREAM)
        for _mid, fields in raw:
            if fields.get(b"correlation_id", b"").decode() == corr_id:
                reply = mj.decode(fields[b"result"])
                break
        if reply:
            break
        await asyncio.sleep(0.1)

    stop_event.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except (asyncio.CancelledError, Exception):
        pass

    assert reply is not None, "No reply received from stock consumer"
    assert reply["success"] is True

    # Verify stock decreased
    raw_stock = await service_db.get("{item:test-q-reserve}")
    stock_val = msgpack.decode(raw_stock, type=_StockValue)
    assert stock_val.stock == 47


@pytest.mark.asyncio(loop_scope="session")
async def test_stock_consumer_check(queue_db, service_db):
    """Stock consumer processes check_stock command and returns stock/price."""
    import msgspec.json as mj

    # Seed stock data
    await service_db.set(
        "{item:test-q-check}",
        msgpack.encode(_StockValue(stock=25, price=8)),
    )

    await stock_consumer_mod.setup_command_consumer_group(queue_db)

    stop_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        stock_consumer_mod.queue_consumer(service_db, queue_db, stop_event)
    )

    corr_id = "check-corr-1"
    await queue_db.xadd(
        stock_consumer_mod.COMMAND_STREAM,
        {
            "correlation_id": corr_id,
            "command": "check_stock",
            "payload": mj.encode({"item_id": "test-q-check"}).decode(),
        },
    )

    reply = None
    for _ in range(20):
        raw = await queue_db.xrange(stock_consumer_mod.REPLY_STREAM)
        for _mid, fields in raw:
            if fields.get(b"correlation_id", b"").decode() == corr_id:
                reply = mj.decode(fields[b"result"])
                break
        if reply:
            break
        await asyncio.sleep(0.1)

    stop_event.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except (asyncio.CancelledError, Exception):
        pass

    assert reply is not None, "No reply received from stock consumer"
    assert reply["success"] is True
    assert reply["stock"] == 25
    assert reply["price"] == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_payment_consumer_charge(queue_db, service_db):
    """Payment consumer processes charge_payment command and publishes reply."""
    import msgspec.json as mj

    # Seed payment data
    await service_db.set(
        "{user:test-q-pay}",
        msgpack.encode(_UserValue(credit=500)),
    )

    # Need to rebind payment consumer's operations to payment_operations
    payment_consumer_mod.operations = payment_operations

    await payment_consumer_mod.setup_command_consumer_group(queue_db)

    stop_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        payment_consumer_mod.queue_consumer(service_db, queue_db, stop_event)
    )

    corr_id = "charge-corr-1"
    await queue_db.xadd(
        payment_consumer_mod.COMMAND_STREAM,
        {
            "correlation_id": corr_id,
            "command": "charge_payment",
            "payload": mj.encode({
                "user_id": "test-q-pay",
                "amount": 100,
                "idempotency_key": "idem-charge-1",
            }).decode(),
        },
    )

    reply = None
    for _ in range(20):
        raw = await queue_db.xrange(payment_consumer_mod.REPLY_STREAM)
        for _mid, fields in raw:
            if fields.get(b"correlation_id", b"").decode() == corr_id:
                reply = mj.decode(fields[b"result"])
                break
        if reply:
            break
        await asyncio.sleep(0.1)

    stop_event.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except (asyncio.CancelledError, Exception):
        pass

    assert reply is not None, "No reply received from payment consumer"
    assert reply["success"] is True

    # Verify credit decreased
    raw_user = await service_db.get("{user:test-q-pay}")
    user_val = msgpack.decode(raw_user, type=_UserValue)
    assert user_val.credit == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_consumer_ack(queue_db, service_db):
    """Messages are ACKed after consumer processes them (XPENDING count = 0)."""
    import msgspec.json as mj

    # Seed stock data
    await service_db.set(
        "{item:test-q-ack}",
        msgpack.encode(_StockValue(stock=10, price=1)),
    )

    await stock_consumer_mod.setup_command_consumer_group(queue_db)

    stop_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        stock_consumer_mod.queue_consumer(service_db, queue_db, stop_event)
    )

    corr_id = "ack-corr-1"
    await queue_db.xadd(
        stock_consumer_mod.COMMAND_STREAM,
        {
            "correlation_id": corr_id,
            "command": "check_stock",
            "payload": mj.encode({"item_id": "test-q-ack"}).decode(),
        },
    )

    # Wait for reply
    for _ in range(20):
        raw = await queue_db.xrange(stock_consumer_mod.REPLY_STREAM)
        found = any(
            fields.get(b"correlation_id", b"").decode() == corr_id
            for _mid, fields in raw
        )
        if found:
            break
        await asyncio.sleep(0.1)

    # Small delay to ensure ACK completes
    await asyncio.sleep(0.2)

    stop_event.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except (asyncio.CancelledError, Exception):
        pass

    # Check XPENDING shows 0 pending messages
    pending_info = await queue_db.xpending(
        stock_consumer_mod.COMMAND_STREAM,
        stock_consumer_mod.CONSUMER_GROUP,
    )
    assert pending_info["pending"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_end_to_end_queue_roundtrip(queue_db, service_db):
    """Full round-trip: queue_client -> stock consumer -> reply_listener -> Future resolved."""
    # Seed stock data
    await service_db.set(
        "{item:test-q-e2e}",
        msgpack.encode(_StockValue(stock=100, price=10)),
    )

    # Set up consumer groups
    await reply_listener.setup_reply_consumer_group(queue_db)
    await stock_consumer_mod.setup_command_consumer_group(queue_db)

    # Init queue client
    queue_client.init_queue_client(queue_db)

    stop_event = asyncio.Event()

    # Start reply listener
    listener_task = asyncio.create_task(
        reply_listener.reply_listener(queue_db, stop_event)
    )

    # Start stock consumer
    consumer_task = asyncio.create_task(
        stock_consumer_mod.queue_consumer(service_db, queue_db, stop_event)
    )

    try:
        # Use queue_client wrapper to send command
        result = await queue_client.reserve_stock("test-q-e2e", 2, "idem-e2e-1")
        assert result["success"] is True

        # Verify stock decreased
        raw_stock = await service_db.get("{item:test-q-e2e}")
        stock_val = msgpack.decode(raw_stock, type=_StockValue)
        assert stock_val.stock == 98
    finally:
        stop_event.set()
        consumer_task.cancel()
        listener_task.cancel()
        for t in (consumer_task, listener_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        queue_client.close_queue_client()
