"""
Redis Streams event publishing for SAGA lifecycle events.

Fire-and-forget: publish_event() never raises. On Redis failure,
increments dropped_events counter and logs a warning. The checkout
path is never blocked by event publishing.
"""
import logging
import time

import msgspec.json

STREAM_NAME = "saga:checkout:events"
DEAD_LETTERS_STREAM = "saga:dead-letters"
STREAM_MAXLEN = 10_000

_dropped_events = 0


def _build_event(event_type: str, saga_id: str, order_id: str,
                 user_id: str = "", **extra) -> dict:
    """Build a rich event payload dict suitable for XADD fields."""
    return {
        "schema_version": "v1",
        "event_type": event_type,
        "saga_id": saga_id,
        "order_id": order_id,
        "user_id": user_id,
        "timestamp": str(int(time.time())),
        **{k: (msgspec.json.encode(v).decode() if not isinstance(v, str) else v)
           for k, v in extra.items()},
    }


async def publish_event(db, event_type: str, saga_id: str,
                        order_id: str, user_id: str = "", **extra) -> None:
    """
    Fire-and-forget XADD to saga:checkout:events stream.

    Never raises. On any failure, increments dropped_events counter
    and logs warning. Stream entries use approximate trimming at MAXLEN 10000.
    """
    global _dropped_events
    try:
        fields = _build_event(event_type, saga_id, order_id, user_id, **extra)
        await db.xadd(
            STREAM_NAME,
            fields,
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        _dropped_events += 1
        logging.warning("publish_event failed (dropped_events=%d): %s",
                        _dropped_events, exc)


def get_dropped_events() -> int:
    return _dropped_events
