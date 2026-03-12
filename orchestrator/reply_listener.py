"""
Reply listener for queue-based request/reply messaging.

Runs as a background task, reading the shared reply stream and resolving
pending asyncio.Future objects by correlation ID. The pending_replies dict
is shared with queue_client.py via import.
"""
import asyncio
import logging

import msgspec.json
from redis.exceptions import ResponseError

pending_replies: dict[str, asyncio.Future] = {}

REPLY_STREAM = "{queue}:replies"
REPLY_GROUP = "orchestrator-replies"
REPLY_CONSUMER = "orchestrator-1"
POLL_INTERVAL_MS = 1000
BATCH_SIZE = 50
STREAM_MAXLEN = 1_000


async def setup_reply_consumer_group(queue_db) -> None:
    """Create consumer group on the reply stream (idempotent)."""
    try:
        await queue_db.xgroup_create(
            REPLY_STREAM, REPLY_GROUP, id="0", mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def reply_listener(queue_db, stop_event: asyncio.Event) -> None:
    """
    Background task that reads replies and resolves pending Futures.

    Runs until stop_event is set. Each reply message must contain
    b"correlation_id" and b"result" fields.
    """
    logging.info("reply_listener started")
    try:
        while not stop_event.is_set():
            try:
                response = await queue_db.xreadgroup(
                    groupname=REPLY_GROUP,
                    consumername=REPLY_CONSUMER,
                    streams={REPLY_STREAM: ">"},
                    count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,
                )
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            correlation_id = fields.get(
                                b"correlation_id", b"",
                            ).decode()
                            result_bytes = fields.get(b"result", b"{}")
                            result_dict = msgspec.json.decode(result_bytes)

                            future = pending_replies.get(correlation_id)
                            if future is not None and not future.done():
                                future.set_result(result_dict)

                            await queue_db.xack(
                                REPLY_STREAM, REPLY_GROUP, msg_id,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("reply_listener error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("reply_listener cancelled -- shutting down")
        raise
    finally:
        logging.info("reply_listener stopped")
