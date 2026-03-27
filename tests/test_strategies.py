"""
Unit tests for retry module and SagaStrategy.

Covers:
- retry_forward: success on first try, exhausted after max_attempts, CircuitBreakerError propagation
- retry_forever: success after initial failure
- SagaStrategy.execute: success path, failure triggering compensation
- SagaStrategy.compensate: reverse order, partial, recovery path reading store flags
- STR-04 partial: SagaStrategy accepts WorkflowDefinition with strategy="saga"

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
from saga_strategy import SagaStrategy, SAGA_STATES, VALID_TRANSITIONS  # noqa: E402
from retry import retry_forward, retry_forever  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_store(get_return=None):
    """Create a mock WorkflowStore with sensible defaults."""
    store = AsyncMock(spec=WorkflowStore)
    store.transition.return_value = True
    store.mark_step_done.return_value = None
    store.get.return_value = get_return
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


# ---------------------------------------------------------------------------
# retry_forward tests
# ---------------------------------------------------------------------------


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_forward_success(mock_sleep):
    """retry_forward returns success dict on first try."""
    fn = AsyncMock(return_value={"success": True, "error_message": ""})
    result = await retry_forward(fn)
    assert result["success"] is True
    fn.assert_awaited_once()
    mock_sleep.assert_not_called()


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_forward_exhausted(mock_sleep):
    """retry_forward returns last failure after max_attempts=3 exhausted."""
    fn = AsyncMock(return_value={"success": False, "error_message": "fail"})
    result = await retry_forward(fn, max_attempts=3)
    assert result["success"] is False
    assert fn.await_count == 3


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_forward_circuit_breaker(mock_sleep):
    """retry_forward raises CircuitBreakerError immediately when fn raises it."""
    from circuitbreaker import CircuitBreakerError, CircuitBreaker

    cb = CircuitBreaker(name="test-cb")
    fn = AsyncMock(side_effect=CircuitBreakerError(cb))
    with pytest.raises(CircuitBreakerError):
        await retry_forward(fn, max_attempts=3)
    fn.assert_awaited_once()


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_forever_success(mock_sleep):
    """retry_forever returns success dict when fn succeeds on second call."""
    fn = AsyncMock(
        side_effect=[
            {"success": False, "error_message": "first fail"},
            {"success": True, "error_message": ""},
        ]
    )
    result = await retry_forever(fn)
    assert result["success"] is True
    assert fn.await_count == 2


# ---------------------------------------------------------------------------
# SagaStrategy.execute tests
# ---------------------------------------------------------------------------


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_saga_execute_success(mock_sleep):
    """SagaStrategy.execute() calls all step actions in order, marks step_N_done, returns success."""
    step0 = make_step("reserve_stock")
    step1 = make_step("charge_payment")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="saga")
    store = make_mock_store()
    strategy = SagaStrategy()
    context = {"order_id": "ord-1"}

    result = await strategy.execute("wf-1", definition, context, store)

    assert result["success"] is True
    step0.action.assert_awaited_once_with(context)
    step1.action.assert_awaited_once_with(context)
    store.mark_step_done.assert_any_await("wf-1", 0)
    store.mark_step_done.assert_any_await("wf-1", 1)


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_saga_execute_step_failure_triggers_compensation(mock_sleep):
    """SagaStrategy.execute() with step 1 failing calls compensate, returns failure."""
    step0 = make_step("reserve_stock")
    step1 = make_step("charge_payment", action_result={"success": False, "error_message": "payment failed"})
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="saga")
    store = make_mock_store()
    strategy = SagaStrategy()
    context = {"order_id": "ord-2"}

    result = await strategy.execute("wf-2", definition, context, store)

    assert result["success"] is False
    # step0 succeeded so its compensation should be called
    step0.compensation.assert_awaited_once_with(context)
    # step1 failed so its compensation should NOT be called
    step1.compensation.assert_not_awaited()


# ---------------------------------------------------------------------------
# SagaStrategy.compensate tests
# ---------------------------------------------------------------------------


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_saga_compensate_reverse_order(mock_sleep):
    """compensate() calls compensations in reverse order of completed_indices."""
    call_order = []

    async def comp0(ctx):
        call_order.append(0)
        return {"success": True, "error_message": ""}

    async def comp1(ctx):
        call_order.append(1)
        return {"success": True, "error_message": ""}

    step0 = WorkflowStep(name="s0", action=AsyncMock(), compensation=comp0)
    step1 = WorkflowStep(name="s1", action=AsyncMock(), compensation=comp1)
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1])
    store = make_mock_store()
    strategy = SagaStrategy()

    await strategy.compensate("wf-3", definition, {}, store, completed_indices=[0, 1])

    # Reverse order: step 1 compensated before step 0
    assert call_order == [1, 0]


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_saga_compensate_partial(mock_sleep):
    """compensate() with completed_indices=[0] only compensates step 0, not step 1."""
    step0 = make_step("s0")
    step1 = make_step("s1")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1])
    store = make_mock_store()
    strategy = SagaStrategy()

    await strategy.compensate("wf-4", definition, {}, store, completed_indices=[0])

    step0.compensation.assert_awaited_once()
    step1.compensation.assert_not_awaited()


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_saga_compensate_recovery_reads_store(mock_sleep):
    """compensate() without completed_indices reads step_N_done flags from store.get()."""
    step0 = make_step("s0")
    step1 = make_step("s1")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1])
    # Store returns step_0_done=1, step_1_done=0 (step 1 never completed)
    store = make_mock_store(get_return={"step_0_done": "1", "step_1_done": "0", "state": "COMPENSATING"})
    strategy = SagaStrategy()

    await strategy.compensate("wf-5", definition, {}, store)

    store.get.assert_awaited_once_with("wf-5")
    step0.compensation.assert_awaited_once()
    step1.compensation.assert_not_awaited()


# ---------------------------------------------------------------------------
# STR-04 partial: SagaStrategy accepts WorkflowDefinition with strategy="saga"
# ---------------------------------------------------------------------------


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_both_strategies_accept_saga_definition(mock_sleep):
    """SagaStrategy.execute() accepts WorkflowDefinition with strategy='saga' without TypeError."""
    step0 = make_step("s0")
    definition = WorkflowDefinition(name="checkout", steps=[step0], strategy="saga")
    store = make_mock_store()
    strategy = SagaStrategy()

    # Should not raise TypeError or ValueError
    result = await strategy.execute("wf-6", definition, {}, store)
    assert result["success"] is True
