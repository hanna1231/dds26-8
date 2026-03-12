"""
Queue-based client for sending commands to domain services via Redis Streams.

Drop-in replacement for client.py (gRPC). Same function signatures, same
return types. Uses XADD to per-service command streams and awaits replies
via shared pending_replies dict from reply_listener.py.
"""
import asyncio
import uuid

import msgspec.json

from reply_listener import pending_replies

_queue_db = None

STOCK_COMMAND_STREAM = "{queue}:stock:commands"
PAYMENT_COMMAND_STREAM = "{queue}:payment:commands"
COMMAND_TIMEOUT = 5.0  # matches RPC_TIMEOUT in client.py
STREAM_MAXLEN = 1_000


def init_queue_client(queue_db) -> None:
    """Store queue Redis connection. Call once at application startup."""
    global _queue_db
    _queue_db = queue_db


def close_queue_client() -> None:
    """Release queue Redis connection. Call once at application shutdown."""
    global _queue_db
    _queue_db = None


async def send_command(stream: str, command: str, payload: dict,
                       timeout: float = COMMAND_TIMEOUT) -> dict:
    """
    XADD a command to the given stream and await the reply Future.

    Returns the result dict on success, or {"success": False, "error_message":
    "queue timeout"} if no reply arrives within the timeout.
    """
    correlation_id = str(uuid.uuid4())
    future = asyncio.get_event_loop().create_future()
    pending_replies[correlation_id] = future

    await _queue_db.xadd(
        stream,
        {
            "correlation_id": correlation_id,
            "command": command,
            "payload": msgspec.json.encode(payload).decode(),
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"success": False, "error_message": "queue timeout"}
    finally:
        pending_replies.pop(correlation_id, None)


# ---------------------------------------------------------------------------
# Stock wrappers (signatures match client.py exactly)
# ---------------------------------------------------------------------------

async def reserve_stock(item_id: str, quantity: int,
                        idempotency_key: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "reserve_stock",
                              {"item_id": item_id, "quantity": quantity,
                               "idempotency_key": idempotency_key})


async def release_stock(item_id: str, quantity: int,
                        idempotency_key: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "release_stock",
                              {"item_id": item_id, "quantity": quantity,
                               "idempotency_key": idempotency_key})


async def check_stock(item_id: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "check_stock",
                              {"item_id": item_id})


# ---------------------------------------------------------------------------
# Payment wrappers (signatures match client.py exactly)
# ---------------------------------------------------------------------------

async def charge_payment(user_id: str, amount: int,
                         idempotency_key: str) -> dict:
    return await send_command(PAYMENT_COMMAND_STREAM, "charge_payment",
                              {"user_id": user_id, "amount": amount,
                               "idempotency_key": idempotency_key})


async def refund_payment(user_id: str, amount: int,
                         idempotency_key: str) -> dict:
    return await send_command(PAYMENT_COMMAND_STREAM, "refund_payment",
                              {"user_id": user_id, "amount": amount,
                               "idempotency_key": idempotency_key})


async def check_payment(user_id: str) -> dict:
    return await send_command(PAYMENT_COMMAND_STREAM, "check_payment",
                              {"user_id": user_id})
