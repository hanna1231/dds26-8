"""
SAGA orchestrator gRPC server.

Implements StartCheckout RPC which drives the full SAGA lifecycle:
  - Exactly-once check via existing SAGA record inspection
  - Forward execution: reserve stock per item, charge payment, mark COMPLETED
  - On forward failure: transition to COMPENSATING and run compensation in reverse
  - Compensation retries indefinitely with exponential backoff
  - Circuit breaker errors propagate immediately out of retry_forward and trigger compensation
"""
import asyncio
import json
import logging
import random

import grpc
import grpc.aio

from orchestrator_pb2 import CheckoutResponse
from orchestrator_pb2_grpc import (
    OrchestratorServiceServicer as OrchestratorServiceServicerBase,
    add_OrchestratorServiceServicer_to_server,
)
from saga import (
    create_saga_record,
    transition_state,
    get_saga,
    set_saga_error,
)
from client import reserve_stock, release_stock, charge_payment, refund_payment


# ---------------------------------------------------------------------------
# retry_forever — exponential backoff, never gives up (SAGA-05)
# ---------------------------------------------------------------------------

async def retry_forever(fn, base: float = 0.5, cap: float = 30.0) -> dict:
    """
    Retry async callable *fn* until it returns a dict with success=True.

    Uses full-jitter exponential backoff: delay = min(cap, base * 2**attempt).

    Args:
        fn:   Async callable with no arguments returning {"success": bool, ...}.
        base: Initial backoff in seconds (default 0.5).
        cap:  Maximum backoff in seconds (default 30.0).

    Returns:
        The first successful result dict.
    """
    attempt = 0
    while True:
        try:
            result = await fn()
            if result.get("success"):
                return result
        except Exception as exc:
            logging.warning("compensation retry attempt %d failed: %s", attempt, exc)
        delay = min(cap, base * (2 ** attempt))
        await asyncio.sleep(delay)
        attempt += 1


# ---------------------------------------------------------------------------
# retry_forward — bounded retry for forward SAGA steps (max 3 attempts)
# ---------------------------------------------------------------------------

async def retry_forward(fn, max_attempts: int = 3, base: float = 0.5, cap: float = 30.0) -> dict:
    """
    Retry async callable *fn* up to max_attempts times for forward SAGA steps.

    Uses full-jitter exponential backoff between attempts.
    CircuitBreakerError propagates immediately — never retried.

    Args:
        fn:           Async callable with no arguments returning {"success": bool, ...}.
        max_attempts: Maximum number of attempts before returning failure (default 3).
        base:         Initial backoff in seconds (default 0.5).
        cap:          Maximum backoff in seconds (default 30.0).

    Returns:
        The first successful result dict, or the last failure dict if all attempts exhausted.

    Raises:
        CircuitBreakerError: If the circuit breaker is open — propagated immediately.
    """
    from circuitbreaker import CircuitBreakerError
    last_result = {"success": False, "error_message": "max retries exceeded"}
    for attempt in range(max_attempts):
        try:
            result = await fn()
            if result.get("success"):
                return result
            last_result = result
        except CircuitBreakerError:
            raise  # breaker open -- propagate immediately, never retry
        except Exception as exc:
            last_result = {"success": False, "error_message": str(exc)}
        if attempt < max_attempts - 1:
            delay = min(cap, base * (2 ** attempt))
            jitter = random.uniform(0, delay)
            await asyncio.sleep(jitter)
    return last_result


# ---------------------------------------------------------------------------
# run_compensation — reverse-order, per-step idempotent (SAGA-04)
# ---------------------------------------------------------------------------

async def run_compensation(db, saga: dict) -> None:
    """
    Compensate a failed SAGA in reverse order (payment refund first, then stock release).

    Only undoes steps whose forward flag is "1".  Per-step completion flags
    (refund_done, stock_restored) prevent double-execution on crash-recovery.

    Reads the SAGA record fresh from Redis to get up-to-date flags (Pitfall 2).
    After all compensation steps succeed, transitions SAGA to FAILED.

    Args:
        db:   redis.asyncio client.
        saga: SAGA dict (used only for order_id — flags are re-read from Redis).
    """
    order_id = saga["order_id"]
    saga_key = f"saga:{order_id}"

    # Re-read current flags to avoid stale data (Pitfall 2)
    current = await get_saga(db, order_id)
    if current is None:
        logging.error("run_compensation: saga not found for order_id=%s", order_id)
        return

    user_id = current["user_id"]
    total_cost = int(current["total_cost"])
    items = json.loads(current["items_json"])

    # Step 1: Refund payment (only if payment was charged and not yet refunded)
    if current.get("payment_charged") == "1" and current.get("refund_done") != "1":
        await retry_forever(
            lambda: refund_payment(user_id, total_cost, f"saga:{order_id}:step:refund")
        )
        await db.hset(saga_key, "refund_done", "1")

    # Step 2: Release stock (only if stock was reserved and not yet restored)
    if current.get("stock_reserved") == "1" and current.get("stock_restored") != "1":
        for item in items:
            item_id = item["item_id"]
            quantity = item["quantity"]
            await retry_forever(
                lambda iid=item_id, qty=quantity: release_stock(
                    iid, qty, f"saga:{order_id}:step:release:{iid}"
                )
            )
        await db.hset(saga_key, "stock_restored", "1")

    # Finalize: transition to FAILED
    await transition_state(db, saga_key, "COMPENSATING", "FAILED")


# ---------------------------------------------------------------------------
# run_checkout — main SAGA execution (SAGA-03, SAGA-06)
# ---------------------------------------------------------------------------

async def run_checkout(
    db,
    order_id: str,
    user_id: str,
    items: list[dict],
    total_cost: int,
) -> dict:
    """
    Execute the checkout SAGA for the given order.

    Guarantees exactly-once semantics: if a SAGA record already exists for
    order_id, returns the stored result without re-executing.

    Forward steps:
      1. Reserve stock for each item (bounded retry via retry_forward, max 3 attempts).
      2. Charge payment (bounded retry via retry_forward, max 3 attempts).
      3. Mark COMPLETED.

    On any forward failure or CircuitBreakerError, transitions to COMPENSATING
    and calls run_compensation().

    Args:
        db:         redis.asyncio client.
        order_id:   Unique order identifier.
        user_id:    User placing the order.
        items:      List of {"item_id": str, "quantity": int} dicts.
        total_cost: Total order cost in cents.

    Returns:
        {"success": bool, "error_message": str}
    """
    from circuitbreaker import CircuitBreakerError

    saga_key = f"saga:{order_id}"

    # --- Exactly-once check (SAGA-06) ---
    existing = await get_saga(db, order_id)
    if existing is not None:
        state = existing.get("state", "")
        if state == "COMPLETED":
            return {"success": True, "error_message": ""}
        if state == "FAILED":
            return {"success": False, "error_message": existing.get("error_message", "")}
        # Any other state (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPENSATING)
        return {"success": False, "error_message": "checkout already in progress"}

    # --- Create SAGA record ---
    created = await create_saga_record(db, order_id, user_id, items, total_cost)
    if not created:
        # Race: another process created the record; re-read and return stored result
        existing = await get_saga(db, order_id)
        if existing is None:
            return {"success": False, "error_message": "internal error: saga disappeared after creation race"}
        state = existing.get("state", "")
        if state == "COMPLETED":
            return {"success": True, "error_message": ""}
        if state == "FAILED":
            return {"success": False, "error_message": existing.get("error_message", "")}
        return {"success": False, "error_message": "checkout already in progress"}

    # --- Forward execution (bounded retry; CircuitBreakerError triggers compensation) ---
    try:
        # Step 1: Reserve stock for each item
        for item in items:
            item_id = item["item_id"]
            quantity = item["quantity"]
            result = await retry_forward(
                lambda iid=item_id, qty=quantity: reserve_stock(
                    iid, qty, f"saga:{order_id}:step:reserve:{iid}"
                )
            )
            if not result.get("success"):
                error_msg = result.get("error_message", "stock reservation failed")
                await set_saga_error(db, order_id, error_msg)
                await transition_state(db, saga_key, "STARTED", "COMPENSATING")
                saga = await get_saga(db, order_id)
                await run_compensation(db, saga)
                return {"success": False, "error_message": error_msg}

        # All stock reservations succeeded
        await transition_state(db, saga_key, "STARTED", "STOCK_RESERVED", "stock_reserved", "1")

        # Step 2: Charge payment
        result = await retry_forward(
            lambda: charge_payment(user_id, total_cost, f"saga:{order_id}:step:charge")
        )
        if not result.get("success"):
            error_msg = result.get("error_message", "payment charge failed")
            await set_saga_error(db, order_id, error_msg)
            await transition_state(db, saga_key, "STOCK_RESERVED", "COMPENSATING")
            saga = await get_saga(db, order_id)
            await run_compensation(db, saga)
            return {"success": False, "error_message": error_msg}

        # Payment succeeded
        await transition_state(db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED", "payment_charged", "1")

        # Step 3: Mark COMPLETED
        await transition_state(db, saga_key, "PAYMENT_CHARGED", "COMPLETED")

        return {"success": True, "error_message": ""}

    except CircuitBreakerError as exc:
        error_msg = f"service unavailable: {exc}"
        await set_saga_error(db, order_id, error_msg)
        current_saga = await get_saga(db, order_id)
        current_state = current_saga.get("state", "STARTED") if current_saga else "STARTED"
        if current_state not in ("COMPLETED", "FAILED", "COMPENSATING"):
            await transition_state(db, saga_key, current_state, "COMPENSATING")
        saga = await get_saga(db, order_id)
        await run_compensation(db, saga)
        return {"success": False, "error_message": error_msg}


# ---------------------------------------------------------------------------
# gRPC servicer
# ---------------------------------------------------------------------------

class OrchestratorServiceServicer(OrchestratorServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def StartCheckout(self, request, context):
        items = [{"item_id": item.item_id, "quantity": item.quantity} for item in request.items]
        result = await run_checkout(
            self.db,
            order_id=request.order_id,
            user_id=request.user_id,
            items=items,
            total_cost=request.total_cost,
        )
        return CheckoutResponse(
            success=result["success"],
            error_message=result["error_message"],
        )


# ---------------------------------------------------------------------------
# gRPC server lifecycle — port 50053 (Stock=50051, Payment=50052)
# ---------------------------------------------------------------------------

_grpc_server: grpc.aio.Server = None


async def serve_grpc(db) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_OrchestratorServiceServicer_to_server(OrchestratorServiceServicer(db), _grpc_server)
    _grpc_server.add_insecure_port("[::]:50053")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()


async def stop_grpc_server() -> None:
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5.0)
