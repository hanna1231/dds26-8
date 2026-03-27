"""
Unit tests for retry module, SagaStrategy, and TwoPhaseStrategy.

Covers:
- retry_forward: success on first try, exhausted after max_attempts, CircuitBreakerError propagation
- retry_forever: success after initial failure
- SagaStrategy.execute: success path, failure triggering compensation
- SagaStrategy.compensate: reverse order, partial, recovery path reading store flags
- TwoPhaseStrategy.execute: all prepare success, prepare failure/exception, WAL ordering, concurrent prepare
- STR-04: Both strategies accept the same WorkflowDefinition object without TypeError

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
from tpc_strategy import TwoPhaseStrategy, TPC_STATES, TPC_VALID_TRANSITIONS  # noqa: E402


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


# ---------------------------------------------------------------------------
# TwoPhaseStrategy tests
# ---------------------------------------------------------------------------


async def test_tpc_execute_all_prepare_success():
    """TwoPhaseStrategy.execute() transitions INIT->PREPARING->COMMITTING->COMMITTED on success."""
    step0 = make_step("reserve_stock")
    step1 = make_step("charge_payment")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    strategy = TwoPhaseStrategy()
    context = {"order_id": "ord-10"}

    result = await strategy.execute("wf-10", definition, context, store)

    assert result["success"] is True
    assert result["error_message"] == ""

    # Verify state transitions in order
    transition_calls = store.transition.call_args_list
    assert call("wf-10", "INIT", "PREPARING") in transition_calls
    assert call("wf-10", "PREPARING", "COMMITTING") in transition_calls
    assert call("wf-10", "COMMITTING", "COMMITTED") in transition_calls

    # Verify ordering: INIT->PREPARING < PREPARING->COMMITTING < COMMITTING->COMMITTED
    indices = {
        "INIT->PREPARING": next(
            i for i, c in enumerate(transition_calls) if c == call("wf-10", "INIT", "PREPARING")
        ),
        "PREPARING->COMMITTING": next(
            i for i, c in enumerate(transition_calls) if c == call("wf-10", "PREPARING", "COMMITTING")
        ),
        "COMMITTING->COMMITTED": next(
            i for i, c in enumerate(transition_calls) if c == call("wf-10", "COMMITTING", "COMMITTED")
        ),
    }
    assert indices["INIT->PREPARING"] < indices["PREPARING->COMMITTING"] < indices["COMMITTING->COMMITTED"]


async def test_tpc_execute_concurrent_prepare():
    """TwoPhaseStrategy sends all prepare requests concurrently (all step actions called).

    Note: action is called twice total -- once for prepare (phase 1) and once for
    commit (phase 2) when all votes succeed. This test verifies all 3 steps were called
    during the prepare phase (demonstrating concurrent execution, not sequential).
    """
    step0 = make_step("s0")
    step1 = make_step("s1")
    step2 = make_step("s2")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1, step2], strategy="2pc")
    store = make_mock_store()
    strategy = TwoPhaseStrategy()

    await strategy.execute("wf-11", definition, {}, store)

    # All actions must have been called (at minimum for prepare phase)
    # With success path, each action is called twice: once for prepare, once for commit
    assert step0.action.await_count >= 1, "step0 action should have been called"
    assert step1.action.await_count >= 1, "step1 action should have been called"
    assert step2.action.await_count >= 1, "step2 action should have been called"
    # Verify all 3 steps were included (not just first N)
    assert step0.action.await_count == step1.action.await_count == step2.action.await_count


async def test_tpc_execute_wal_commit():
    """WAL: PREPARING->COMMITTING transition written BEFORE phase-2 commit action calls.

    Uses a shared call_log list to track interleaving of store.transition() calls
    and step action invocations during phase-2. The WAL PREPARING->COMMITTING entry
    must appear in the log before any phase-2 'commit_action' entries.

    Note: phase-1 actions also log 'commit_action' (they share the same function here).
    We verify the WAL write happens before the SECOND batch of action calls (phase-2).
    Since gather runs phase-1 concurrently, we count occurrences: after the WAL write
    there should be exactly 2 more 'commit_action' entries from phase-2.
    """
    call_log = []

    async def action_that_logs(ctx):
        call_log.append("commit_action")
        return {"success": True, "error_message": ""}

    async def mock_transition(workflow_id, from_state, to_state, **kwargs):
        call_log.append(f"transition:{from_state}->{to_state}")
        return True

    step0 = WorkflowStep(name="s0", action=action_that_logs, compensation=AsyncMock())
    step1 = WorkflowStep(name="s1", action=action_that_logs, compensation=AsyncMock())
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    store.transition.side_effect = mock_transition
    strategy = TwoPhaseStrategy()

    await strategy.execute("wf-12", definition, {}, store)

    # Find the WAL write position
    wal_idx = next(i for i, e in enumerate(call_log) if e == "transition:PREPARING->COMMITTING")

    # All entries after the WAL write that are 'commit_action' are phase-2 calls
    phase2_actions_after_wal = [i for i, e in enumerate(call_log) if e == "commit_action" and i > wal_idx]
    assert len(phase2_actions_after_wal) == 2, (
        f"Expected 2 phase-2 commit actions after WAL write at {wal_idx}, got {call_log}"
    )


async def test_tpc_execute_wal_abort():
    """WAL: PREPARING->ABORTING transition written BEFORE phase-2 compensation calls."""
    call_log = []

    async def prepare_fail(ctx):
        return {"success": False, "error_message": "vote no"}

    async def prepare_ok(ctx):
        return {"success": True, "error_message": ""}

    async def comp_that_logs(ctx):
        call_log.append("compensation_called")
        return {"success": True, "error_message": ""}

    async def mock_transition(workflow_id, from_state, to_state, **kwargs):
        call_log.append(f"transition:{from_state}->{to_state}")
        return True

    step0 = WorkflowStep(name="s0", action=prepare_ok, compensation=comp_that_logs)
    step1 = WorkflowStep(name="s1", action=prepare_fail, compensation=comp_that_logs)
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    store.transition.side_effect = mock_transition
    strategy = TwoPhaseStrategy()

    await strategy.execute("wf-13", definition, {}, store)

    wal_idx = next(i for i, e in enumerate(call_log) if e == "transition:PREPARING->ABORTING")
    comp_indices = [i for i, e in enumerate(call_log) if e == "compensation_called"]
    # All compensations appear after PREPARING->ABORTING WAL write
    assert all(idx > wal_idx for idx in comp_indices), (
        f"WAL write at {wal_idx} should precede all compensations at {comp_indices}"
    )


async def test_tpc_execute_prepare_failure_aborts():
    """TwoPhaseStrategy aborts when any prepare returns success=False."""
    step0 = make_step("s0")
    step1 = make_step("s1", action_result={"success": False, "error_message": "prepare rejected"})
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    strategy = TwoPhaseStrategy()
    context = {"order_id": "ord-14"}

    result = await strategy.execute("wf-14", definition, context, store)

    assert result["success"] is False
    assert result["error_message"] == "prepare rejected"

    # Abort path: PREPARING->ABORTING and ABORTING->ABORTED
    transition_calls = store.transition.call_args_list
    assert call("wf-14", "PREPARING", "ABORTING") in transition_calls
    assert call("wf-14", "ABORTING", "ABORTED") in transition_calls

    # Both compensations called (all steps get abort)
    step0.compensation.assert_awaited_once_with(context)
    step1.compensation.assert_awaited_once_with(context)


async def test_tpc_execute_prepare_exception_aborts():
    """TwoPhaseStrategy aborts when any prepare action raises an Exception."""
    step0 = make_step("s0")
    step1 = make_step("s1")
    step1.action.side_effect = RuntimeError("network")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    strategy = TwoPhaseStrategy()

    result = await strategy.execute("wf-15", definition, {}, store)

    assert result["success"] is False
    assert "network" in result["error_message"]

    transition_calls = store.transition.call_args_list
    assert call("wf-15", "PREPARING", "ABORTING") in transition_calls


async def test_tpc_marks_step_done_on_prepare_success():
    """TwoPhaseStrategy calls store.mark_step_done for each step that votes yes."""
    step0 = make_step("s0")
    step1 = make_step("s1")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="2pc")
    store = make_mock_store()
    strategy = TwoPhaseStrategy()

    await strategy.execute("wf-16", definition, {}, store)

    store.mark_step_done.assert_any_await("wf-16", 0)
    store.mark_step_done.assert_any_await("wf-16", 1)


# ---------------------------------------------------------------------------
# STR-04 complete: both strategies accept the same WorkflowDefinition
# ---------------------------------------------------------------------------


@patch("retry.asyncio.sleep", new_callable=AsyncMock)
async def test_both_strategies_accept_same_definition(mock_sleep):
    """STR-04: SagaStrategy and TwoPhaseStrategy both accept the same WorkflowDefinition without TypeError."""
    step0 = make_step("s0")
    step1 = make_step("s1")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="saga")

    # --- SagaStrategy run ---
    saga_store = make_mock_store()
    saga_result = await SagaStrategy().execute("wf-str04-saga", definition, {}, saga_store)
    assert "success" in saga_result

    # Reset mocks for TwoPhaseStrategy run (same definition object, unchanged)
    step0.action.reset_mock()
    step0.compensation.reset_mock()
    step1.action.reset_mock()
    step1.compensation.reset_mock()

    tpc_store = make_mock_store()
    tpc_result = await TwoPhaseStrategy().execute("wf-str04-tpc", definition, {}, tpc_store)
    assert "success" in tpc_result
