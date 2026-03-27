"""
Unit tests for WorkflowEngine.

Covers:
- test_engine_routes_to_saga: execute() with strategy="saga" calls SagaStrategy.execute()
- test_engine_routes_to_2pc: execute() with strategy="2pc" calls TwoPhaseStrategy.execute()
- test_engine_publishes_started_event: publish_event called with "workflow_started" before strategy
- test_engine_publishes_succeeded_event: publish_event called with "workflow_succeeded" on success
- test_engine_publishes_failed_event: publish_event called with "workflow_failed" on failure
- test_engine_unknown_strategy: raises ValueError for unknown strategy
- test_engine_calls_store_create_saga: store.create called with "STARTED" for saga
- test_engine_calls_store_create_2pc: store.create called with "INIT" for 2pc

All async tests run without @pytest.mark.asyncio (asyncio_mode=auto in pytest.ini).
"""
import sys
import os

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from workflow_types import WorkflowStep, WorkflowDefinition  # noqa: E402
from workflow_store import WorkflowStore  # noqa: E402
from workflow_engine import WorkflowEngine  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_store(get_return=None):
    """Create a mock WorkflowStore with sensible defaults."""
    store = AsyncMock(spec=WorkflowStore)
    store.transition.return_value = True
    store.mark_step_done.return_value = None
    store.get.return_value = get_return
    store.create.return_value = True
    return store


def make_step(name, action_result=None, comp_result=None):
    """Create a WorkflowStep with AsyncMock action and compensation."""
    action_result = action_result or {"success": True, "error_message": ""}
    comp_result = comp_result or {"success": True, "error_message": ""}
    return WorkflowStep(
        name=name,
        action=AsyncMock(return_value=action_result),
        compensation=AsyncMock(return_value=comp_result),
    )


def make_engine(store=None):
    """Create a WorkflowEngine with a mock db and optional store."""
    db = AsyncMock()
    if store is None:
        store = make_mock_store()
    return WorkflowEngine(store=store, db=db), store, db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_engine_routes_to_saga():
    """engine.execute() with strategy="saga" calls SagaStrategy.execute()."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test_saga",
        steps=[make_step("step1"), make_step("step2")],
        strategy="saga",
    )
    context = {"order_id": "o1", "user_id": "u1"}
    expected_result = {"success": True, "error_message": ""}

    mock_saga = AsyncMock()
    mock_saga.execute = AsyncMock(return_value=expected_result)
    mock_tpc = AsyncMock()
    mock_tpc.execute = AsyncMock(return_value=expected_result)

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": mock_tpc}), \
         patch("workflow_engine.publish_event", new_callable=AsyncMock):
        result = await engine.execute("wf-1", definition, context)

    mock_saga.execute.assert_awaited_once_with("wf-1", definition, context, store)
    mock_tpc.execute.assert_not_awaited()
    assert result == expected_result


async def test_engine_routes_to_2pc():
    """engine.execute() with strategy="2pc" calls TwoPhaseStrategy.execute()."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test_2pc",
        steps=[make_step("step1"), make_step("step2")],
        strategy="2pc",
    )
    context = {"order_id": "o2", "user_id": "u2"}
    expected_result = {"success": True, "error_message": ""}

    mock_saga = AsyncMock()
    mock_saga.execute = AsyncMock(return_value=expected_result)
    mock_tpc = AsyncMock()
    mock_tpc.execute = AsyncMock(return_value=expected_result)

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": mock_tpc}), \
         patch("workflow_engine.publish_event", new_callable=AsyncMock):
        result = await engine.execute("wf-2", definition, context)

    mock_tpc.execute.assert_awaited_once_with("wf-2", definition, context, store)
    mock_saga.execute.assert_not_awaited()
    assert result == expected_result


async def test_engine_publishes_started_event():
    """publish_event called with "workflow_started" BEFORE strategy.execute()."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[make_step("step1")],
        strategy="saga",
    )
    context = {"order_id": "o1", "user_id": "u1"}

    call_order = []

    mock_saga = AsyncMock()
    async def record_strategy_call(*args, **kwargs):
        call_order.append("strategy")
        return {"success": True, "error_message": ""}
    mock_saga.execute = AsyncMock(side_effect=record_strategy_call)

    async def record_event_call(db_arg, event_type, *args, **kwargs):
        call_order.append(event_type)
    mock_publish = AsyncMock(side_effect=record_event_call)

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": AsyncMock()}), \
         patch("workflow_engine.publish_event", mock_publish):
        await engine.execute("wf-1", definition, context)

    # "workflow_started" must appear before "strategy"
    assert "workflow_started" in call_order
    started_idx = call_order.index("workflow_started")
    strategy_idx = call_order.index("strategy")
    assert started_idx < strategy_idx, (
        f"workflow_started (pos {started_idx}) must come before strategy call (pos {strategy_idx})"
    )

    # Verify the call args for workflow_started
    started_calls = [c for c in mock_publish.call_args_list
                     if c.args[1] == "workflow_started"]
    assert len(started_calls) == 1
    c = started_calls[0]
    assert c.args[2] == "wf-1"
    assert c.args[3] == "o1"
    assert c.args[4] == "u1"


async def test_engine_publishes_succeeded_event():
    """publish_event called with "workflow_succeeded" when strategy returns success=True."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[make_step("step1")],
        strategy="saga",
    )
    context = {"order_id": "o1", "user_id": "u1"}

    mock_saga = AsyncMock()
    mock_saga.execute = AsyncMock(return_value={"success": True, "error_message": ""})
    mock_publish = AsyncMock()

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": AsyncMock()}), \
         patch("workflow_engine.publish_event", mock_publish):
        await engine.execute("wf-1", definition, context)

    event_types = [c.args[1] for c in mock_publish.call_args_list]
    assert "workflow_succeeded" in event_types
    assert "workflow_failed" not in event_types


async def test_engine_publishes_failed_event():
    """publish_event called with "workflow_failed" when strategy returns success=False."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[make_step("step1")],
        strategy="saga",
    )
    context = {"order_id": "o1", "user_id": "u1"}

    mock_saga = AsyncMock()
    mock_saga.execute = AsyncMock(return_value={"success": False, "error_message": "failed"})
    mock_publish = AsyncMock()

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": AsyncMock()}), \
         patch("workflow_engine.publish_event", mock_publish):
        await engine.execute("wf-1", definition, context)

    event_types = [c.args[1] for c in mock_publish.call_args_list]
    assert "workflow_failed" in event_types
    assert "workflow_succeeded" not in event_types


async def test_engine_unknown_strategy():
    """engine.execute() with unknown strategy raises ValueError."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[],
        strategy="invalid",  # type: ignore[arg-type]  -- Literal not enforced at runtime
    )
    context = {"order_id": "o1", "user_id": "u1"}

    with pytest.raises(ValueError, match="Unknown strategy"):
        await engine.execute("wf-1", definition, context)


async def test_engine_calls_store_create_saga():
    """engine.execute() calls store.create(workflow_id, "STARTED", metadata=context) for saga."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[make_step("step1")],
        strategy="saga",
    )
    context = {"order_id": "o1", "user_id": "u1"}

    mock_saga = AsyncMock()
    mock_saga.execute = AsyncMock(return_value={"success": True, "error_message": ""})

    with patch("workflow_engine._STRATEGIES", {"saga": mock_saga, "2pc": AsyncMock()}), \
         patch("workflow_engine.publish_event", new_callable=AsyncMock):
        await engine.execute("wf-1", definition, context)

    store.create.assert_awaited_once_with("wf-1", "STARTED", metadata=context)


async def test_engine_calls_store_create_2pc():
    """engine.execute() calls store.create(workflow_id, "INIT", metadata=context) for 2pc."""
    engine, store, db = make_engine()
    definition = WorkflowDefinition(
        name="test",
        steps=[make_step("step1")],
        strategy="2pc",
    )
    context = {"order_id": "o2", "user_id": "u2"}

    mock_tpc = AsyncMock()
    mock_tpc.execute = AsyncMock(return_value={"success": True, "error_message": ""})

    with patch("workflow_engine._STRATEGIES", {"saga": AsyncMock(), "2pc": mock_tpc}), \
         patch("workflow_engine.publish_event", new_callable=AsyncMock):
        await engine.execute("wf-2", definition, context)

    store.create.assert_awaited_once_with("wf-2", "INIT", metadata=context)
