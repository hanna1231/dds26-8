"""
SAGA startup recovery scanner.

On orchestrator restart, scans Redis for non-terminal SAGAs and drives each
to a terminal state (COMPLETED or FAILED) before the orchestrator serves traffic.
"""
import asyncio
import json
import logging
import os
import time

NON_TERMINAL_STATES = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING"}
STALENESS_THRESHOLD_SECONDS = int(os.environ.get('SAGA_STALENESS_SECONDS', '300'))


async def resume_saga(db, saga: dict) -> None:
    """
    Drive a partially-completed SAGA to a terminal state from its current step.

    Forward steps are idempotent (Phase 2 Lua cache). Safe to replay.
    CircuitBreakerError during recovery triggers compensation.
    """
    from grpc_server import run_compensation
    from transport import reserve_stock, charge_payment
    from circuitbreaker import CircuitBreakerError
    from saga import transition_state, get_saga

    order_id = saga["order_id"]
    saga_key = f"{{saga:{order_id}}}"
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
                    f"{{saga:{order_id}}}:step:reserve:{item_id}"
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
                f"{{saga:{order_id}}}:step:charge"
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

    async for key in db.scan_iter(match="{saga:*", count=100):
        try:
            raw = await db.hgetall(key)
        except Exception:
            # Skip non-hash keys (e.g., Redis Streams like {saga:events}:checkout)
            continue
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


# ---------------------------------------------------------------------------
# 2PC (TPC) recovery scanner (TPC-06)
# ---------------------------------------------------------------------------

TPC_NON_TERMINAL_STATES = {"INIT", "PREPARING", "COMMITTING", "ABORTING"}


async def resume_tpc(db, tpc: dict) -> None:
    """
    Drive a partially-completed TPC transaction to a terminal state.

    - INIT / PREPARING -> presumed abort (ABORTING -> ABORTED)
    - COMMITTING -> re-send commits (COMMITTED)
    - ABORTING -> re-send aborts (ABORTED)
    """
    from tpc import transition_tpc_state
    from transport import commit_stock, abort_stock, commit_payment, abort_payment

    order_id = tpc["order_id"]
    tpc_key = f"{{tpc:{order_id}}}"
    state = tpc["state"]
    items = json.loads(tpc["items_json"])
    user_id = tpc["user_id"]

    logging.info("Recovering TPC %s from state=%s", order_id, state)

    if state in ("INIT", "PREPARING"):
        # Presumed abort
        if state == "INIT":
            await transition_tpc_state(db, tpc_key, "INIT", "PREPARING")
        await transition_tpc_state(db, tpc_key, "PREPARING", "ABORTING")

        abort_futures = []
        for item in items:
            abort_futures.append(abort_stock(item["item_id"], order_id))
        abort_futures.append(abort_payment(user_id, order_id))
        await asyncio.gather(*abort_futures, return_exceptions=True)

        await transition_tpc_state(db, tpc_key, "ABORTING", "ABORTED")
        logging.info("TPC %s: presumed abort -> ABORTED", order_id)

    elif state == "COMMITTING":
        # Re-send commits
        commit_futures = []
        for item in items:
            commit_futures.append(commit_stock(item["item_id"], order_id))
        commit_futures.append(commit_payment(user_id, order_id))
        await asyncio.gather(*commit_futures, return_exceptions=True)

        await transition_tpc_state(db, tpc_key, "COMMITTING", "COMMITTED")
        logging.info("TPC %s: re-sent commits -> COMMITTED", order_id)

    elif state == "ABORTING":
        # Re-send aborts
        abort_futures = []
        for item in items:
            abort_futures.append(abort_stock(item["item_id"], order_id))
        abort_futures.append(abort_payment(user_id, order_id))
        await asyncio.gather(*abort_futures, return_exceptions=True)

        await transition_tpc_state(db, tpc_key, "ABORTING", "ABORTED")
        logging.info("TPC %s: re-sent aborts -> ABORTED", order_id)


async def recover_incomplete_tpc(db) -> None:
    """
    Scan Redis for incomplete TPC transactions and drive to terminal state.

    Only processes {tpc:*} keys (skips {saga:*}).
    Skips records younger than STALENESS_THRESHOLD_SECONDS.
    """
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="{tpc:*", count=100):
        try:
            raw = await db.hgetall(key)
        except Exception:
            continue
        if not raw:
            continue
        tpc = {k.decode(): v.decode() for k, v in raw.items()}
        state = tpc.get("state", "")

        if state not in TPC_NON_TERMINAL_STATES:
            continue  # terminal -- skip

        updated_at = int(tpc.get("updated_at", "0"))
        age_seconds = now - updated_at

        if age_seconds < STALENESS_THRESHOLD_SECONDS:
            logging.warning(
                "TPC %s is in %s state but only %ds old -- skipping (recent)",
                tpc.get("order_id"), state, age_seconds,
            )
            skipped += 1
            continue

        await resume_tpc(db, tpc)
        recovered += 1

    logging.info(
        "TPC recovery complete: %d recovered, %d skipped (recent)",
        recovered, skipped,
    )
