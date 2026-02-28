"""
Redis Streams consumer loops for SAGA event processing.

Two consumer groups:
  - compensation-handler: retries compensation for stuck SAGAs, dead-letters after 5 attempts
  - audit-logger: logs all SAGA events for observability (best-effort, ACK always)

Consumers run as Quart background tasks (app.add_background_task).
"""
import asyncio
import logging

from redis.exceptions import ResponseError

from events import STREAM_NAME, DEAD_LETTERS_STREAM

CONSUMER_GROUPS = ["compensation-handler", "audit-logger"]
POLL_INTERVAL_MS = 2000
BATCH_SIZE = 10
CLAIM_IDLE_MS = 30_000
MAX_RETRIES = 5

_stop_event: asyncio.Event | None = None


def init_stop_event() -> asyncio.Event:
    global _stop_event
    _stop_event = asyncio.Event()
    return _stop_event


async def setup_consumer_groups(db) -> None:
    for group in CONSUMER_GROUPS:
        try:
            await db.xgroup_create(
                STREAM_NAME, group, id="0", mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise


async def compensation_consumer(db) -> None:
    group = "compensation-handler"
    consumer_name = "orchestrator-1"
    logging.info("compensation_consumer started")
    try:
        while not _stop_event.is_set():
            # Reclaim idle messages from crashed consumers
            try:
                autoclaim_result = await db.xautoclaim(
                    STREAM_NAME, group, consumer_name,
                    min_idle_time=CLAIM_IDLE_MS, start_id="0-0",
                    count=BATCH_SIZE,
                )
                claimed_messages = autoclaim_result[1] if autoclaim_result else []
                for msg_id, fields in claimed_messages:
                    await _handle_compensation_message(db, group, msg_id, fields)
            except Exception as exc:
                logging.warning("xautoclaim error: %s", exc)

            # Read new messages
            try:
                response = await db.xreadgroup(
                    groupname=group, consumername=consumer_name,
                    streams={STREAM_NAME: ">"}, count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,
                )
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            await _handle_compensation_message(
                                db, group, msg_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("xreadgroup error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("compensation_consumer cancelled — shutting down")
        raise
    finally:
        logging.info("compensation_consumer stopped")


async def _handle_compensation_message(db, group, msg_id, fields) -> None:
    event_type = fields.get(b"event_type", b"").decode()
    if event_type != "compensation_triggered":
        await db.xack(STREAM_NAME, group, msg_id)
        return

    # Check delivery count
    try:
        pending = await db.xpending_range(
            STREAM_NAME, group, min=msg_id, max=msg_id, count=1,
        )
        delivery_count = pending[0]["times_delivered"] if pending else 0
    except Exception:
        delivery_count = 0

    if delivery_count > MAX_RETRIES:
        await db.xadd(DEAD_LETTERS_STREAM, {**fields, b"original_id": msg_id})
        await db.xack(STREAM_NAME, group, msg_id)
        logging.error("Message %s dead-lettered after %d attempts",
                       msg_id, delivery_count)
        return

    # Attempt compensation
    try:
        order_id = fields.get(b"order_id", b"").decode()
        if order_id:
            from grpc_server import run_compensation
            from saga import get_saga
            saga = await get_saga(db, order_id)
            if saga and saga.get("state") == "COMPENSATING":
                await run_compensation(db, saga)
        await db.xack(STREAM_NAME, group, msg_id)
    except Exception as exc:
        logging.warning("Compensation for %s failed: %s (will retry)",
                        msg_id, exc)


async def audit_consumer(db) -> None:
    group = "audit-logger"
    consumer_name = "orchestrator-1"
    logging.info("audit_consumer started")
    try:
        while not _stop_event.is_set():
            try:
                response = await db.xreadgroup(
                    groupname=group, consumername=consumer_name,
                    streams={STREAM_NAME: ">"}, count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,
                )
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            event_type = fields.get(
                                b"event_type", b"unknown").decode()
                            order_id = fields.get(b"order_id", b"").decode()
                            logging.info("SAGA_EVENT %s order=%s id=%s",
                                         event_type, order_id, msg_id)
                            await db.xack(STREAM_NAME, group, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("audit_consumer error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("audit_consumer cancelled — shutting down")
        raise
    finally:
        logging.info("audit_consumer stopped")
