"""
Checkout workflow definition factory.

Expresses the checkout transaction as a WorkflowDefinition using transport.py
functions as step callables. The engine and strategies know nothing about
Stock or Payment -- this module is the ONLY place that binds transport functions
to workflow steps.

Per CHK-01: make_checkout_workflow() returns a WorkflowDefinition.
Per Pitfall 4: exactly 2 steps for saga (matches STATE_SEQUENCE length).
Per Research Option A: module-level async functions (no closure late-binding risk).
"""
from workflow_types import WorkflowStep, WorkflowDefinition
from transport import (
    reserve_stock, release_stock,
    charge_payment, refund_payment,
    prepare_stock, commit_stock, abort_stock,
    prepare_payment, commit_payment, abort_payment,
)


# ---------------------------------------------------------------------------
# SAGA step callables (module-level async functions -- no closure capture risk)
# ---------------------------------------------------------------------------

async def _reserve_all(context: dict) -> dict:
    """Reserve stock for all items; return first failure immediately."""
    order_id = context["order_id"]
    for item in context["items"]:
        iid, qty = item["item_id"], item["quantity"]
        result = await reserve_stock(iid, qty, f"{{saga:{order_id}}}:step:reserve:{iid}")
        if not result.get("success"):
            return result
    return {"success": True, "error_message": ""}


async def _release_all(context: dict) -> dict:
    """Compensation: release stock for all items."""
    order_id = context["order_id"]
    for item in context["items"]:
        iid, qty = item["item_id"], item["quantity"]
        await release_stock(iid, qty, f"{{saga:{order_id}}}:step:release:{iid}")
    return {"success": True, "error_message": ""}


async def _charge(context: dict) -> dict:
    """Charge payment for the order total."""
    return await charge_payment(
        context["user_id"], context["total_cost"],
        f"{{saga:{context['order_id']}}}:step:charge"
    )


async def _refund(context: dict) -> dict:
    """Compensation: refund payment for the order total."""
    return await refund_payment(
        context["user_id"], context["total_cost"],
        f"{{saga:{context['order_id']}}}:step:refund"
    )


# ---------------------------------------------------------------------------
# 2PC step callables
# ---------------------------------------------------------------------------

async def _prepare_all_stock(context: dict) -> dict:
    """2PC prepare: prepare stock for all items."""
    order_id = context["order_id"]
    for item in context["items"]:
        result = await prepare_stock(item["item_id"], item["quantity"], order_id)
        if not result.get("success"):
            return result
    return {"success": True, "error_message": ""}


async def _prepare_payment(context: dict) -> dict:
    """2PC prepare: prepare payment."""
    return await prepare_payment(
        context["user_id"], context["total_cost"], context["order_id"]
    )


async def _abort_all_stock(context: dict) -> dict:
    """2PC abort: abort stock for all items."""
    order_id = context["order_id"]
    for item in context["items"]:
        await abort_stock(item["item_id"], order_id)
    return {"success": True, "error_message": ""}


async def _abort_payment(context: dict) -> dict:
    """2PC abort: abort payment."""
    await abort_payment(context["user_id"], context["order_id"])
    return {"success": True, "error_message": ""}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_checkout_workflow(strategy: str = "saga") -> WorkflowDefinition:
    """Create a checkout WorkflowDefinition for the given strategy.

    SAGA steps (2 steps matching STATE_SEQUENCE length):
      1. reserve_stock: loops reserve_stock() per item, compensation = release_stock()
      2. charge_payment: charge_payment(), compensation = refund_payment()

    2PC steps (2 steps):
      1. prepare_stock: loops prepare_stock() per item, compensation = abort_stock()
      2. prepare_payment: prepare_payment(), compensation = abort_payment()

    Args:
        strategy: "saga" or "2pc"

    Returns:
        WorkflowDefinition ready for WorkflowEngine.execute()
    """
    if strategy == "saga":
        steps = [
            WorkflowStep(
                name="reserve_stock",
                action=_reserve_all,
                compensation=_release_all,
            ),
            WorkflowStep(
                name="charge_payment",
                action=_charge,
                compensation=_refund,
            ),
        ]
    elif strategy == "2pc":
        steps = [
            WorkflowStep(
                name="prepare_stock",
                action=_prepare_all_stock,
                compensation=_abort_all_stock,
            ),
            WorkflowStep(
                name="prepare_payment",
                action=_prepare_payment,
                compensation=_abort_payment,
            ),
        ]
    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    return WorkflowDefinition(name="checkout", steps=steps, strategy=strategy)
