"""
SAGA startup recovery scanner.

On orchestrator restart, scans Redis for non-terminal SAGAs and drives each
to a terminal state (COMPLETED or FAILED) before the orchestrator serves traffic.
"""
import json
import logging
import time

NON_TERMINAL_STATES = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING"}
STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes (Claude's discretion)


async def resume_saga(db, saga: dict) -> None:
    """
    Drive a partially-completed SAGA to a terminal state from its current step.

    Forward steps are idempotent (Phase 2 Lua cache). Safe to replay.
    CircuitBreakerError during recovery triggers compensation.
    """
    from grpc_server import run_compensation
    from client import reserve_stock, charge_payment, CircuitBreakerError
    from saga import transition_state, get_saga

    order_id = saga["order_id"]
    saga_key = f"saga:{order_id}"
    state = saga["state"]

    logging.info("Recovering SAGA %s from state=%s", order_id, state)

    if state == "COMPENSATING":
        await run_compensation(db, saga)
        logging.info("SAGA %s: compensation completed -> FAILED", order_id)
        return

    # Forward recovery: STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED
    # All gRPC calls carry the same idempotency keys as original -- safe to replay
    try:
        if state == "STARTED":
            items = json.loads(saga["items_json"])
            for item in items:
                item_id = item["item_id"]
                quantity = item["quantity"]
                result = await reserve_stock(
                    item_id, quantity,
                    f"saga:{order_id}:step:reserve:{item_id}"
                )
                if not result.get("success"):
                    logging.warning(
                        "SAGA %s: forward recovery failed at reserve_stock: %s",
                        order_id, result.get("error_message"),
                    )
                    await transition_state(db, saga_key, state, "COMPENSATING")
                    await run_compensation(db, await get_saga(db, order_id))
                    return
            await transition_state(db, saga_key, "STARTED", "STOCK_RESERVED", "stock_reserved", "1")
            state = "STOCK_RESERVED"

        if state == "STOCK_RESERVED":
            result = await charge_payment(
                saga["user_id"], int(saga["total_cost"]),
                f"saga:{order_id}:step:charge"
            )
            if not result.get("success"):
                logging.warning(
                    "SAGA %s: forward recovery failed at charge_payment: %s",
                    order_id, result.get("error_message"),
                )
                await transition_state(db, saga_key, state, "COMPENSATING")
                await run_compensation(db, await get_saga(db, order_id))
                return
            await transition_state(db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED", "payment_charged", "1")
            state = "PAYMENT_CHARGED"

        if state == "PAYMENT_CHARGED":
            await transition_state(db, saga_key, "PAYMENT_CHARGED", "COMPLETED")

        logging.info("SAGA %s: recovery -> COMPLETED", order_id)

    except CircuitBreakerError as exc:
        logging.error(
            "SAGA %s: circuit open during recovery, compensating: %s",
            order_id, exc,
        )
        current = await get_saga(db, order_id)
        if current and current["state"] not in ("COMPLETED", "FAILED"):
            current_state = current["state"]
            if current_state != "COMPENSATING":
                await transition_state(db, saga_key, current_state, "COMPENSATING")
            await run_compensation(db, await get_saga(db, order_id))


async def recover_incomplete_sagas(db) -> None:
    """
    Scan Redis for incomplete SAGAs and drive them to terminal state.

    Called from app.before_serving -- blocks until all stale SAGAs resolved.
    Skips SAGAs younger than STALENESS_THRESHOLD_SECONDS (still fresh, not stuck).
    """
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="saga:*", count=100):
        raw = await db.hgetall(key)
        if not raw:
            continue
        saga = {k.decode(): v.decode() for k, v in raw.items()}
        state = saga.get("state", "")

        if state not in NON_TERMINAL_STATES:
            continue  # terminal -- skip

        updated_at = int(saga.get("updated_at", "0"))
        age_seconds = now - updated_at

        if age_seconds < STALENESS_THRESHOLD_SECONDS:
            logging.warning(
                "SAGA %s is in %s state but only %ds old -- skipping (recent)",
                saga.get("order_id"), state, age_seconds,
            )
            skipped += 1
            continue

        await resume_saga(db, saga)
        recovered += 1

    logging.info(
        "SAGA recovery complete: %d recovered, %d skipped (recent)",
        recovered, skipped,
    )
