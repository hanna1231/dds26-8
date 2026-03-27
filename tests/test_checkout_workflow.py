"""
Unit tests for checkout_workflow.py -- make_checkout_workflow() factory.

Covers:
- make_checkout_workflow("saga"): structure, step names, transport call contracts
- make_checkout_workflow("2pc"): structure, step names
- Separation of concerns: engine and strategy modules must not reference service names

All async tests run without @pytest.mark.asyncio (asyncio_mode=auto in pytest.ini).
"""
import sys
import os

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

import pytest
from unittest.mock import AsyncMock, patch

from workflow_types import WorkflowStep, WorkflowDefinition  # noqa: E402
from checkout_workflow import make_checkout_workflow  # noqa: E402


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


async def test_make_checkout_workflow_saga_structure():
    defn = make_checkout_workflow("saga")
    assert isinstance(defn, WorkflowDefinition)
    assert defn.name == "checkout"
    assert defn.strategy == "saga"
    assert len(defn.steps) == 2
    for step in defn.steps:
        assert isinstance(step, WorkflowStep)
        assert callable(step.action)
        assert callable(step.compensation)


async def test_make_checkout_workflow_saga_step_names():
    defn = make_checkout_workflow("saga")
    assert defn.steps[0].name == "reserve_stock"
    assert defn.steps[1].name == "charge_payment"


async def test_make_checkout_workflow_2pc_structure():
    defn = make_checkout_workflow("2pc")
    assert isinstance(defn, WorkflowDefinition)
    assert defn.strategy == "2pc"
    assert len(defn.steps) == 2
    for step in defn.steps:
        assert isinstance(step, WorkflowStep)
        assert callable(step.action)
        assert callable(step.compensation)


async def test_make_checkout_workflow_2pc_step_names():
    defn = make_checkout_workflow("2pc")
    assert defn.steps[0].name == "prepare_stock"
    assert defn.steps[1].name == "prepare_payment"


# ---------------------------------------------------------------------------
# SAGA transport call contract tests
# ---------------------------------------------------------------------------


@patch("checkout_workflow.reserve_stock", new_callable=AsyncMock, return_value={"success": True, "error_message": ""})
async def test_saga_reserve_action_calls_transport(mock_reserve):
    defn = make_checkout_workflow("saga")
    context = {
        "order_id": "ord-1",
        "user_id": "u-1",
        "items": [{"item_id": "item-A", "quantity": 2}, {"item_id": "item-B", "quantity": 1}],
        "total_cost": 100,
    }
    result = await defn.steps[0].action(context)
    assert result["success"] is True
    assert mock_reserve.await_count == 2
    mock_reserve.assert_any_await("item-A", 2, "{saga:ord-1}:step:reserve:item-A")
    mock_reserve.assert_any_await("item-B", 1, "{saga:ord-1}:step:reserve:item-B")


@patch("checkout_workflow.release_stock", new_callable=AsyncMock, return_value={"success": True, "error_message": ""})
async def test_saga_release_compensation_calls_transport(mock_release):
    defn = make_checkout_workflow("saga")
    context = {
        "order_id": "ord-1",
        "user_id": "u-1",
        "items": [{"item_id": "item-A", "quantity": 2}],
        "total_cost": 100,
    }
    result = await defn.steps[0].compensation(context)
    assert result["success"] is True
    mock_release.assert_awaited_once_with("item-A", 2, "{saga:ord-1}:step:release:item-A")


@patch("checkout_workflow.charge_payment", new_callable=AsyncMock, return_value={"success": True, "error_message": ""})
async def test_saga_charge_action_calls_transport(mock_charge):
    defn = make_checkout_workflow("saga")
    context = {"order_id": "ord-1", "user_id": "u-1", "items": [], "total_cost": 50}
    result = await defn.steps[1].action(context)
    assert result["success"] is True
    mock_charge.assert_awaited_once_with("u-1", 50, "{saga:ord-1}:step:charge")


@patch("checkout_workflow.refund_payment", new_callable=AsyncMock, return_value={"success": True, "error_message": ""})
async def test_saga_refund_compensation_calls_transport(mock_refund):
    defn = make_checkout_workflow("saga")
    context = {"order_id": "ord-1", "user_id": "u-1", "items": [], "total_cost": 50}
    result = await defn.steps[1].compensation(context)
    assert result["success"] is True
    mock_refund.assert_awaited_once_with("u-1", 50, "{saga:ord-1}:step:refund")


# ---------------------------------------------------------------------------
# Separation of concerns: engine/strategy modules must not reference transport
# ---------------------------------------------------------------------------


def test_no_service_names_in_engine():
    """Engine and strategy modules must not reference Stock/Payment service names."""
    engine_path = os.path.join(_orchestrator_path, "workflow_engine.py")
    with open(engine_path) as f:
        content = f.read()
    for forbidden in [
        "reserve_stock", "release_stock", "charge_payment", "refund_payment",
        "prepare_stock", "commit_stock", "abort_stock",
        "prepare_payment", "commit_payment", "abort_payment",
    ]:
        assert forbidden not in content, f"workflow_engine.py must not contain '{forbidden}'"


def test_no_service_names_in_strategies():
    """Strategy modules must not reference specific transport function names."""
    for fname in ["saga_strategy.py", "tpc_strategy.py"]:
        fpath = os.path.join(_orchestrator_path, fname)
        with open(fpath) as f:
            content = f.read()
        for forbidden in ["reserve_stock", "release_stock", "charge_payment", "refund_payment"]:
            assert forbidden not in content, f"{fname} must not contain '{forbidden}'"
