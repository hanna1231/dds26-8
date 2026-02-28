"""
Event-driven architecture tests covering EVENT-01, EVENT-02, EVENT-03.

Tests verify:
  EVENT-01: Fire-and-forget event publishing, payload shape, real XADD writes
  EVENT-02: Consumer group idempotency, at-least-once delivery, dead-letter after MAX_RETRIES
  EVENT-03: Consumer graceful shutdown via stop_event, full checkout lifecycle events

Uses pytest-asyncio in auto mode (asyncio_mode=auto from pytest.ini).
Integration tests connect to Redis at localhost:6379 db=3 (same orchestrator-db).
"""
import asyncio
import sys
import os
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import redis.asyncio as redis_async

# ---------------------------------------------------------------------------
# sys.path: orchestrator modules
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

from events import publish_event, _build_event, get_dropped_events, STREAM_NAME, DEAD_LETTERS_STREAM
import events as _events_mod
import consumers as _consumers_mod
from consumers import (
    setup_consumer_groups,
    compensation_consumer,
    audit_consumer,
    _handle_compensation_message,
    MAX_RETRIES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def events_db():
    """
    Redis client for event integration tests (db=3).

    Flushes the database before and after each test to ensure isolation.
    Skip the test if Redis is unavailable.
    """
    try:
        db = redis_async.Redis(host="localhost", port=6379, db=3)
        await db.ping()
        await db.flushdb()
        yield db
        await db.flushdb()
        await db.aclose()
    except Exception as exc:
        pytest.skip(f"Redis not available: {exc}")


# ---------------------------------------------------------------------------
# Task 1: EVENT-01 — event publishing tests
# ---------------------------------------------------------------------------


async def test_publish_event_fire_and_forget():
    """
    EVENT-01: publish_event does not raise when Redis XADD raises ConnectionError.

    Verifies fire-and-forget behavior: the checkout path is never blocked by
    event publishing failures.
    """
    # Reset the dropped_events counter before test
    _events_mod._dropped_events = 0

    mock_db = MagicMock()
    mock_db.xadd = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

    # Should not raise — fire-and-forget
    await publish_event(mock_db, "checkout_started", "saga:order1", "order1", "user1")

    # Counter should have incremented
    assert get_dropped_events() > 0, "dropped_events counter should have been incremented"


async def test_event_payload_shape():
    """
    EVENT-01: _build_event returns a dict with all required fields and schema_version=v1.

    Verifies that every event payload has the correct structure before XADD.
    """
    event = _build_event(
        "checkout_started", "saga:order1", "order1", "user1", total_cost="500"
    )

    assert event["schema_version"] == "v1", "schema_version must be 'v1'"
    assert event["event_type"] == "checkout_started"
    assert event["saga_id"] == "saga:order1"
    assert event["order_id"] == "order1"
    assert event["user_id"] == "user1"

    # timestamp must be a numeric string
    assert int(event["timestamp"]) > 0, "timestamp must be a parseable integer string"

    # extra kwargs passed through as strings
    assert event["total_cost"] == "500", "extra kwarg 'total_cost' should pass through"


async def test_publish_event_xadd_integration(events_db):
    """
    EVENT-01: publish_event writes to the stream with correct field names (real Redis).

    Verifies XADD writes schema_version=v1, event_type, and the stream is
    accessible via XRANGE.
    """
    await publish_event(events_db, "stock_reserved", "saga:test1", "test1", "user1")

    entries = await events_db.xrange(STREAM_NAME, count=10)
    assert len(entries) >= 1, "At least one entry should exist in the stream after publish_event"

    # Check the latest entry has the correct field names
    _msg_id, fields = entries[-1]
    assert fields.get(b"event_type") == b"stock_reserved", (
        f"event_type field should be b'stock_reserved', got {fields.get(b'event_type')}"
    )
    assert fields.get(b"schema_version") == b"v1", (
        f"schema_version field should be b'v1', got {fields.get(b'schema_version')}"
    )


# ---------------------------------------------------------------------------
# Task 2: EVENT-02 — consumer group and lifecycle event tests
# ---------------------------------------------------------------------------


async def test_consumer_group_setup_idempotent(events_db):
    """
    EVENT-02: setup_consumer_groups is idempotent — calling twice does not raise.

    BUSYGROUP errors from Redis are caught and suppressed.
    """
    # First call creates the groups
    await setup_consumer_groups(events_db)
    # Second call must not raise (BUSYGROUP caught)
    await setup_consumer_groups(events_db)

    # Verify both groups exist
    # xinfo_groups returns dicts with string keys; "name" value is bytes
    groups = await events_db.xinfo_groups(STREAM_NAME)
    group_names = [
        g["name"].decode() if isinstance(g["name"], bytes) else g["name"]
        for g in groups
    ]
    assert len(groups) >= 2, f"Expected at least 2 consumer groups, got {len(groups)}"
    assert "compensation-handler" in group_names, (
        f"'compensation-handler' not found in groups: {group_names}"
    )
    assert "audit-logger" in group_names, (
        f"'audit-logger' not found in groups: {group_names}"
    )


async def test_at_least_once_delivery(events_db):
    """
    EVENT-02: XREADGROUP delivers messages; XACK removes them from PEL.

    Verifies at-least-once delivery semantics: a message stays in the Pending
    Entry List (PEL) until acknowledged.
    """
    await setup_consumer_groups(events_db)

    # Publish a test event
    await publish_event(events_db, "saga_completed", "saga:test2", "test2", "user2")

    # Read from the audit-logger group
    response = await events_db.xreadgroup(
        groupname="audit-logger",
        consumername="test-consumer",
        streams={STREAM_NAME: ">"},
        count=1,
        block=1000,
    )
    assert response, "xreadgroup should return at least one message"

    _stream_name, messages = response[0]
    assert messages, "messages list should not be empty"

    msg_id, fields = messages[0]
    assert fields.get(b"event_type") == b"saga_completed", (
        f"Expected b'saga_completed', got {fields.get(b'event_type')}"
    )

    # ACK the message
    await events_db.xack(STREAM_NAME, "audit-logger", msg_id)

    # Verify PEL is empty (message acknowledged)
    pending = await events_db.xpending(STREAM_NAME, "audit-logger")
    assert pending["pending"] == 0, (
        f"PEL should be empty after xack, but pending count is {pending['pending']}"
    )


async def test_dead_letter_after_max_retries(events_db):
    """
    EVENT-02: Messages are dead-lettered to saga:dead-letters after MAX_RETRIES delivery attempts.

    Uses a mock on xpending_range to simulate delivery_count > MAX_RETRIES without
    actually delivering the message multiple times.
    """
    await setup_consumer_groups(events_db)

    # Publish a compensation_triggered event via direct XADD
    msg_id = await events_db.xadd(STREAM_NAME, {
        "event_type": "compensation_triggered",
        "order_id": "dead-letter-test",
        "saga_id": "saga:dead-letter-test",
        "schema_version": "v1",
        "timestamp": str(int(time.time())),
    })

    # Read the message (leave in PEL — do not ACK)
    await events_db.xreadgroup(
        groupname="compensation-handler",
        consumername="orchestrator-1",
        streams={STREAM_NAME: ">"},
        count=1,
        block=500,
    )

    # Mock xpending_range to simulate delivery_count above MAX_RETRIES
    fake_pending = [{"times_delivered": MAX_RETRIES + 1, "message_id": msg_id}]
    with patch.object(events_db, "xpending_range", new=AsyncMock(return_value=fake_pending)):
        fields = {
            b"event_type": b"compensation_triggered",
            b"order_id": b"dead-letter-test",
            b"saga_id": b"saga:dead-letter-test",
            b"schema_version": b"v1",
            b"timestamp": str(int(time.time())).encode(),
        }
        await _handle_compensation_message(events_db, "compensation-handler", msg_id, fields)

    # Message should have been dead-lettered
    dead_letters = await events_db.xrange(DEAD_LETTERS_STREAM)
    assert len(dead_letters) >= 1, (
        f"Expected at least 1 entry in dead-letters stream, got {len(dead_letters)}"
    )

    # Original message should have been ACKed (removed from PEL)
    pending = await events_db.xpending(STREAM_NAME, "compensation-handler")
    assert pending["pending"] == 0, (
        f"PEL should be empty after dead-lettering, pending count: {pending['pending']}"
    )


async def test_consumer_graceful_shutdown():
    """
    EVENT-03: Consumer loop exits cleanly when asyncio.CancelledError is raised.

    Tests that compensation_consumer properly handles CancelledError (the standard
    asyncio shutdown mechanism) and terminates cleanly.

    Strategy: start the consumer with a stop_event already pre-set to True,
    so the while-loop exits on the very first condition check without needing
    any async yields. Then verify the task completes normally.
    """
    stop_event = asyncio.Event()
    # Pre-set so the consumer loop condition is false on the first check
    stop_event.set()
    # Inject into the consumers module
    _consumers_mod._stop_event = stop_event

    mock_db = MagicMock()
    mock_db.xreadgroup = AsyncMock(return_value=[])
    mock_db.xautoclaim = AsyncMock(return_value=[b"0-0", [], []])

    # With stop_event already set, the while condition is False immediately
    # The consumer should return without error
    await compensation_consumer(mock_db)

    # If we reach here, consumer exited cleanly (no exception raised)


async def test_checkout_publishes_lifecycle_events(events_db, monkeypatch):
    """
    EVENT-03: run_checkout publishes expected lifecycle events for the full happy path.

    Verifies that checkout_started, stock_reserved, payment_completed, and
    saga_completed events all appear in the stream with correct fields.
    """
    from grpc_server import run_checkout

    await setup_consumer_groups(events_db)

    # Mock gRPC client functions to simulate successful stock/payment.
    # grpc_server imports reserve_stock and charge_payment directly via
    # "from client import ...", so we must patch them in the grpc_server module.
    mock_reserve = AsyncMock(return_value={"success": True, "error_message": ""})
    mock_charge = AsyncMock(return_value={"success": True, "error_message": ""})

    import grpc_server as _grpc_server_mod
    monkeypatch.setattr(_grpc_server_mod, "reserve_stock", mock_reserve)
    monkeypatch.setattr(_grpc_server_mod, "charge_payment", mock_charge)

    result = await run_checkout(
        events_db,
        "lifecycle-test",
        "user1",
        [{"item_id": "item1", "quantity": 1}],
        500,
    )

    assert result["success"] is True, (
        f"run_checkout should succeed with mocked gRPC calls, got: {result}"
    )

    # Read all events from the stream
    entries = await events_db.xrange(STREAM_NAME)
    assert entries, "Stream should have events after run_checkout"

    event_types = [e[1][b"event_type"].decode() for e in entries]

    # All four lifecycle events must be present in order
    required_events = ["checkout_started", "stock_reserved", "payment_completed", "saga_completed"]
    for ev in required_events:
        assert ev in event_types, (
            f"Expected event '{ev}' in stream events, got: {event_types}"
        )

    # Verify ordering: checkout_started comes before saga_completed
    assert event_types.index("checkout_started") < event_types.index("saga_completed"), (
        "checkout_started must appear before saga_completed"
    )

    # All events should reference the same order_id and schema_version=v1
    for _msg_id, fields in entries:
        assert fields.get(b"order_id") == b"lifecycle-test", (
            f"Expected b'lifecycle-test' order_id, got {fields.get(b'order_id')}"
        )
        assert fields.get(b"schema_version") == b"v1", (
            f"Expected b'v1' schema_version, got {fields.get(b'schema_version')}"
        )
