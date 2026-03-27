"""
Tests for WorkflowStep, WorkflowDefinition dataclasses and WorkflowStore.

Covers: ENG-01 (WorkflowStep), ENG-02 (WorkflowDefinition),
        ENG-04 (Redis Lua CAS persistence), ENG-05 (per-step completion flags)

All integration tests use real Redis (db=3) via orchestrator_db / clean_orchestrator_db
fixtures provided by conftest.py.

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators
are needed on async test functions.
"""

import sys
import os
import pytest

# Ensure orchestrator path is available (conftest already adds it but be explicit)
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

from workflow_types import WorkflowStep, WorkflowDefinition  # noqa: E402
from workflow_store import WorkflowStore  # noqa: E402


# ---------------------------------------------------------------------------
# ENG-01: WorkflowStep tests
# ---------------------------------------------------------------------------


async def test_workflow_step_fields():
    """WorkflowStep has name, action, compensation fields (ENG-01)."""
    async def some_action():
        return "action_result"

    async def some_compensation():
        return "compensation_result"

    step = WorkflowStep(
        name="reserve",
        action=some_action,
        compensation=some_compensation,
    )

    assert step.name == "reserve"
    assert step.action is some_action
    assert step.compensation is some_compensation


async def test_workflow_step_callables_async():
    """WorkflowStep action and compensation can be awaited (ENG-01)."""
    async def my_action():
        return 42

    async def my_compensation():
        return "undone"

    step = WorkflowStep(name="pay", action=my_action, compensation=my_compensation)

    action_result = await step.action()
    compensation_result = await step.compensation()

    assert action_result == 42
    assert compensation_result == "undone"


# ---------------------------------------------------------------------------
# ENG-02: WorkflowDefinition tests
# ---------------------------------------------------------------------------


async def test_workflow_definition_fields():
    """WorkflowDefinition has name, steps, strategy fields (ENG-02)."""
    async def noop():
        pass

    step1 = WorkflowStep(name="step1", action=noop, compensation=noop)

    wf = WorkflowDefinition(name="checkout", steps=[step1], strategy="2pc")

    assert wf.name == "checkout"
    assert wf.steps == [step1]
    assert wf.strategy == "2pc"


async def test_workflow_definition_strategy():
    """WorkflowDefinition strategy defaults to 'saga' and accepts '2pc' (ENG-02)."""
    wf_default = WorkflowDefinition(name="checkout")
    assert wf_default.strategy == "saga"

    wf_2pc = WorkflowDefinition(name="x", strategy="2pc")
    assert wf_2pc.strategy == "2pc"


async def test_workflow_definition_independent_steps():
    """Two WorkflowDefinition instances have independent steps lists -- no mutable default bug (ENG-02)."""
    wf1 = WorkflowDefinition(name="wf1")
    wf2 = WorkflowDefinition(name="wf2")

    async def noop():
        pass

    step = WorkflowStep(name="s", action=noop, compensation=noop)
    wf1.steps.append(step)

    # wf2.steps must be unaffected
    assert len(wf2.steps) == 0
    assert len(wf1.steps) == 1


# ---------------------------------------------------------------------------
# ENG-04: WorkflowStore create and transition tests
# ---------------------------------------------------------------------------


async def test_workflow_store_create(orchestrator_db, clean_orchestrator_db):
    """create() initializes a Redis hash and returns True (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    result = await store.create("wf-1", "STARTED")
    assert result is True

    record = await store.get("wf-1")
    assert record is not None
    assert record["state"] == "STARTED"
    assert record["workflow_id"] == "wf-1"
    assert record["started_at"].isdigit()
    assert record["updated_at"].isdigit()


async def test_workflow_store_create_duplicate(orchestrator_db, clean_orchestrator_db):
    """create() returns True on first call and False on duplicate (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    first = await store.create("wf-dup", "STARTED")
    second = await store.create("wf-dup", "STARTED")
    assert first is True
    assert second is False


async def test_workflow_store_create_metadata(orchestrator_db, clean_orchestrator_db):
    """create() stores optional metadata dict fields in the hash (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    await store.create(
        "wf-m",
        "STARTED",
        metadata={"order_id": "ord-1", "items": '[{"id":"x"}]'},
    )
    record = await store.get("wf-m")
    assert record is not None
    assert record["order_id"] == "ord-1"
    assert record["items"] == '[{"id":"x"}]'


async def test_workflow_store_create_ttl(orchestrator_db, clean_orchestrator_db):
    """create() sets a 7-day TTL on the workflow hash key (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-ttl", "STARTED")
    ttl = await orchestrator_db.ttl("{workflow:wf-ttl}")
    assert ttl > 0
    assert ttl <= 604800  # 7 days in seconds


async def test_workflow_store_transition_valid(orchestrator_db, clean_orchestrator_db):
    """transition() returns True and updates state when from_state matches (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-t", "STARTED")
    result = await store.transition("wf-t", "STARTED", "STEP_1_DONE")
    assert result is True

    record = await store.get("wf-t")
    assert record["state"] == "STEP_1_DONE"


async def test_workflow_store_transition_mismatch(orchestrator_db, clean_orchestrator_db):
    """transition() returns False and leaves state unchanged when from_state is wrong (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-m2", "STARTED")
    result = await store.transition("wf-m2", "WRONG_STATE", "STEP_1_DONE")
    assert result is False

    record = await store.get("wf-m2")
    assert record["state"] == "STARTED"


async def test_workflow_store_transition_with_flag(orchestrator_db, clean_orchestrator_db):
    """transition() with flag_field/flag_value writes the extra field atomically (ENG-04)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-f", "STARTED")
    result = await store.transition(
        "wf-f", "STARTED", "NEXT", flag_field="step_0_done", flag_value="1"
    )
    assert result is True

    record = await store.get("wf-f")
    assert record["state"] == "NEXT"
    assert record["step_0_done"] == "1"


# ---------------------------------------------------------------------------
# ENG-05: Per-step completion flags
# ---------------------------------------------------------------------------


async def test_workflow_store_mark_step_done(orchestrator_db, clean_orchestrator_db):
    """mark_step_done() writes step_N_done = '1' into the hash (ENG-05)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-s", "STARTED")
    await store.mark_step_done("wf-s", 0)

    record = await store.get("wf-s")
    assert record["step_0_done"] == "1"


async def test_workflow_store_multiple_steps(orchestrator_db, clean_orchestrator_db):
    """Multiple step flags coexist without collision (ENG-05)."""
    store = WorkflowStore(orchestrator_db)
    await store.create("wf-multi", "STARTED")
    await store.mark_step_done("wf-multi", 0)
    await store.mark_step_done("wf-multi", 1)
    await store.mark_step_done("wf-multi", 2)

    record = await store.get("wf-multi")
    assert record["step_0_done"] == "1"
    assert record["step_1_done"] == "1"
    assert record["step_2_done"] == "1"


# ---------------------------------------------------------------------------
# get() edge case
# ---------------------------------------------------------------------------


async def test_workflow_store_get_nonexistent(orchestrator_db, clean_orchestrator_db):
    """get() returns None for a workflow_id that does not exist."""
    store = WorkflowStore(orchestrator_db)
    result = await store.get("does-not-exist")
    assert result is None
