"""
Queue consumer for the Stock service.

Reads commands from the stock command stream, dispatches to operations
module functions, and publishes results to the shared reply stream.
"""
import asyncio
import logging

import msgspec.json
from redis.exceptions import ResponseError

import operations

COMMAND_STREAM = "{queue}:stock:commands"
REPLY_STREAM = "{queue}:replies"
CONSUMER_GROUP = "stock-consumers"
CONSUMER_NAME = "stock-1"
POLL_INTERVAL_MS = 1000
BATCH_SIZE = 10
STREAM_MAXLEN = 1_000

COMMAND_DISPATCH = {
    "reserve_stock": lambda db, p: operations.reserve_stock(
        db, p["item_id"], int(p["quantity"]), p["idempotency_key"],
    ),
    "release_stock": lambda db, p: operations.release_stock(
        db, p["item_id"], int(p["quantity"]), p["idempotency_key"],
    ),
    "check_stock": lambda db, p: operations.check_stock(
        db, p["item_id"],
    ),
}


async def setup_command_consumer_group(queue_db) -> None:
    """Create consumer group on the command stream (idempotent)."""
    try:
        await queue_db.xgroup_create(
            COMMAND_STREAM, CONSUMER_GROUP, id="0", mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def queue_consumer(db, queue_db, stop_event: asyncio.Event) -> None:
    """
    Background task that reads stock commands and dispatches to operations.

    Args:
        db: Stock service Redis connection (for operations calls).
        queue_db: Shared queue Redis connection (for stream reads/writes).
        stop_event: Set to signal graceful shutdown.
    """
    logging.info("stock queue_consumer started")
    try:
        while not stop_event.is_set():
            try:
                response = await queue_db.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=CONSUMER_NAME,
                    streams={COMMAND_STREAM: ">"},
                    count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,
                )
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            correlation_id = fields[b"correlation_id"].decode()
                            command = fields[b"command"].decode()
                            payload_str = fields[b"payload"].decode()
                            payload = msgspec.json.decode(payload_str.encode())

                            handler = COMMAND_DISPATCH.get(command)
                            if handler is None:
                                result = {
                                    "success": False,
                                    "error_message": f"unknown command: {command}",
                                }
                            else:
                                result = await handler(db, payload)

                            await queue_db.xadd(
                                REPLY_STREAM,
                                {
                                    "correlation_id": correlation_id,
                                    "result": msgspec.json.encode(result).decode(),
                                },
                                maxlen=STREAM_MAXLEN,
                                approximate=True,
                            )
                            await queue_db.xack(
                                COMMAND_STREAM, CONSUMER_GROUP, msg_id,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("stock queue_consumer error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("stock queue_consumer cancelled -- shutting down")
        raise
    finally:
        logging.info("stock queue_consumer stopped")
