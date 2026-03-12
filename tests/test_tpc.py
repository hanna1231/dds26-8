"""
Unit tests for 2PC (Two-Phase Commit) state machine.

Covers: TPC-01

All tests use:
  - Real Redis (db=3 for TPC records, same DB as SAGA but different key prefix {tpc:})
  - Unique order_ids per test (uuid4) to prevent cross-test interference

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators
are needed on async test functions.
"""

import json
import sys
import os
import uuid

import pytest

# Ensure orchestrator path is available
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

from tpc import create_tpc_record, transition_tpc_state, get_tpc, TPC_VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_order_id() -> str:
    return f"order-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Test 1: TPC record created with correct fields
# ---------------------------------------------------------------------------

async def test_tpc_record_created(tpc_db, clean_tpc_db):
    """TPC-01: create_tpc_record persists record with state=INIT, protocol=2pc."""
    order_id = new_order_id()
    user_id = f"user-{uuid.uuid4().hex}"
    items = [{"item_id": "item-abc", "quantity": 3}]
    total_cost = 250

    created = await create_tpc_record(tpc_db, order_id, user_id, items, total_cost)
    assert created is True, "create_tpc_record should return True for new record"

    record = await get_tpc(tpc_db, order_id)
    assert record is not None, "TPC record must exist in Redis after creation"

    assert record["state"] == "INIT"
    assert record["protocol"] == "2pc"
    assert record["order_id"] == order_id
    assert record["user_id"] == user_id
    assert record["total_cost"] == str(total_cost)
    assert record["stock_prepared"] == "0"
    assert record["payment_prepared"] == "0"
    assert json.loads(record["items_json"]) == items


# ---------------------------------------------------------------------------
# Test 2: Duplicate TPC record creation prevented
# ---------------------------------------------------------------------------

async def test_tpc_duplicate_creation_prevented(tpc_db, clean_tpc_db):
    """TPC-01: Second create_tpc_record for same order_id returns False."""
    order_id = new_order_id()

    first = await create_tpc_record(tpc_db, order_id, "u1", [], 0)
    assert first is True

    second = await create_tpc_record(tpc_db, order_id, "u1", [], 0)
    assert second is False

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "INIT"


# ---------------------------------------------------------------------------
# Test 3: Valid state transitions succeed
# ---------------------------------------------------------------------------

async def test_tpc_valid_transitions(tpc_db, clean_tpc_db):
    """TPC-01: Valid 2PC transitions succeed (commit path and abort path)."""
    # Commit path: INIT -> PREPARING -> COMMITTING -> COMMITTED
    order_id_commit = new_order_id()
    await create_tpc_record(tpc_db, order_id_commit, "u1", [], 0)
    tpc_key = f"{{tpc:{order_id_commit}}}"

    ok = await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    assert ok is True

    ok = await transition_tpc_state(tpc_db, tpc_key, "PREPARING", "COMMITTING")
    assert ok is True

    ok = await transition_tpc_state(tpc_db, tpc_key, "COMMITTING", "COMMITTED")
    assert ok is True

    record = await get_tpc(tpc_db, order_id_commit)
    assert record["state"] == "COMMITTED"

    # Abort path: INIT -> PREPARING -> ABORTING -> ABORTED
    order_id_abort = new_order_id()
    await create_tpc_record(tpc_db, order_id_abort, "u1", [], 0)
    tpc_key_abort = f"{{tpc:{order_id_abort}}}"

    ok = await transition_tpc_state(tpc_db, tpc_key_abort, "INIT", "PREPARING")
    assert ok is True

    ok = await transition_tpc_state(tpc_db, tpc_key_abort, "PREPARING", "ABORTING")
    assert ok is True

    ok = await transition_tpc_state(tpc_db, tpc_key_abort, "ABORTING", "ABORTED")
    assert ok is True

    record = await get_tpc(tpc_db, order_id_abort)
    assert record["state"] == "ABORTED"


# ---------------------------------------------------------------------------
# Test 4: Invalid state transitions rejected with ValueError
# ---------------------------------------------------------------------------

async def test_tpc_invalid_transitions_rejected(tpc_db, clean_tpc_db):
    """TPC-01: Invalid transitions raise ValueError and state is unchanged."""
    order_id = new_order_id()
    await create_tpc_record(tpc_db, order_id, "u1", [], 0)
    tpc_key = f"{{tpc:{order_id}}}"

    # INIT -> COMMITTED is not valid
    with pytest.raises(ValueError):
        await transition_tpc_state(tpc_db, tpc_key, "INIT", "COMMITTED")

    # PREPARING -> COMMITTED is not valid (must go through COMMITTING)
    ok = await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    assert ok is True

    with pytest.raises(ValueError):
        await transition_tpc_state(tpc_db, tpc_key, "PREPARING", "COMMITTED")

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "PREPARING"


# ---------------------------------------------------------------------------
# Test 5: CAS rejects stale state
# ---------------------------------------------------------------------------

async def test_tpc_cas_rejects_stale_state(tpc_db, clean_tpc_db):
    """TPC-01: Transition from wrong current state returns False (CAS safety)."""
    order_id = new_order_id()
    await create_tpc_record(tpc_db, order_id, "u1", [], 0)
    tpc_key = f"{{tpc:{order_id}}}"

    # Advance to PREPARING
    ok = await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    assert ok is True

    # Now try INIT -> PREPARING again (state is already PREPARING, not INIT)
    ok = await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    assert ok is False

    # State must still be PREPARING
    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "PREPARING"
