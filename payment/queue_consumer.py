"""
Queue consumer for the Payment service.

Reads commands from the payment command stream, dispatches to operations
module functions, and publishes results to the shared reply stream.
"""
import asyncio
import logging

import msgspec.json
from redis.exceptions import ResponseError

import operations

COMMAND_STREAM = "{queue}:payment:commands"
REPLY_STREAM = "{queue}:replies"
CONSUMER_GROUP = "payment-consumers"
CONSUMER_NAME = "payment-1"
POLL_INTERVAL_MS = 1000
BATCH_SIZE = 10
STREAM_MAXLEN = 1_000

COMMAND_DISPATCH = {
    "charge_payment": lambda db, p: operations.charge_payment(
        db, p["user_id"], int(p["amount"]), p["idempotency_key"],
    ),
    "refund_payment": lambda db, p: operations.refund_payment(
        db, p["user_id"], int(p["amount"]), p["idempotency_key"],
    ),
    "check_payment": lambda db, p: operations.check_payment(
        db, p["user_id"],
    ),
    "prepare_payment": lambda db, p: operations.prepare_payment(
        db, p["user_id"], int(p["amount"]), p["order_id"],
    ),
    "commit_payment": lambda db, p: operations.commit_payment(
        db, p["user_id"], p["order_id"],
    ),
    "abort_payment": lambda db, p: operations.abort_payment(
        db, p["user_id"], p["order_id"],
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
    Background task that reads payment commands and dispatches to operations.

    Args:
        db: Payment service Redis connection (for operations calls).
        queue_db: Shared queue Redis connection (for stream reads/writes).
        stop_event: Set to signal graceful shutdown.
    """
    logging.info("payment queue_consumer started")
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
                logging.warning("payment queue_consumer error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("payment queue_consumer cancelled -- shutting down")
        raise
    finally:
        logging.info("payment queue_consumer stopped")
