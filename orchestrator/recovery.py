"""
Workflow startup recovery scanner.

On orchestrator restart, scans Redis for non-terminal workflow records and drives
each to a terminal state (COMPLETED/COMMITTED or FAILED/ABORTED) before the
orchestrator serves traffic. Uses WorkflowEngine.resume() for all recovery paths.
"""
import json
import logging
import os
import time

from checkout_workflow import make_checkout_workflow

WORKFLOW_NON_TERMINAL = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING",
                          "INIT", "PREPARING", "COMMITTING", "ABORTING"}
STALENESS_THRESHOLD_SECONDS = int(os.environ.get('SAGA_STALENESS_SECONDS', '300'))


async def recover_incomplete_workflows(db, engine) -> None:
    """Scan Redis for incomplete workflow records and drive to terminal state.

    Scans {workflow:*} keys written by WorkflowEngine. Reads strategy field
    from stored record to reconstruct the correct WorkflowDefinition.
    Calls engine.resume() to drive each workflow to completion.

    Skips workflows younger than STALENESS_THRESHOLD_SECONDS.

    Args:
        db:     redis.asyncio client.
        engine: WorkflowEngine instance with resume() method.
    """
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="{workflow:*", count=100):
        try:
            raw = await db.hgetall(key)
        except Exception:
            continue
        if not raw:
            continue
        record = {k.decode(): v.decode() for k, v in raw.items()}
        state = record.get("state", "")

        if state not in WORKFLOW_NON_TERMINAL:
            continue

        updated_at = int(record.get("updated_at", "0"))
        age_seconds = now - updated_at

        if age_seconds < STALENESS_THRESHOLD_SECONDS:
            logging.warning(
                "Workflow %s is in %s state but only %ds old -- skipping (recent)",
                record.get("workflow_id", "?"), state, age_seconds,
            )
            skipped += 1
            continue

        # Reconstruct context from stored fields
        workflow_id = record.get("workflow_id", "")
        strategy = record.get("strategy", "saga")
        try:
            context = {
                "order_id": record.get("order_id", workflow_id),
                "user_id": record.get("user_id", ""),
                "items": json.loads(record.get("items", "[]")),
                "total_cost": int(record.get("total_cost", "0")),
            }
            definition = make_checkout_workflow(strategy)
            logging.info("Recovering workflow %s (strategy=%s, state=%s)",
                         workflow_id, strategy, state)
            await engine.resume(workflow_id, definition, context)
            recovered += 1
        except Exception as exc:
            logging.error("Failed to recover workflow %s: %s", workflow_id, exc)

    logging.info(
        "Workflow recovery complete: %d recovered, %d skipped (recent)",
        recovered, skipped,
    )
