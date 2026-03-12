"""
Unit tests for 2PC coordinator, WAL, recovery, and TRANSACTION_PATTERN toggle.

Covers: TPC-04 (coordinator), TPC-05 (WAL), TPC-06 (recovery), TPC-07 (toggle)

All tests use:
  - Real Redis (db=3 for TPC records via tpc_db fixture)
  - unittest.mock.patch for transport functions (no real gRPC calls)
  - Unique order_ids per test (uuid4) to prevent cross-test interference

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators needed.
"""

import json
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch, call

# Ensure orchestrator path is available
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

from tpc import create_tpc_record, transition_tpc_state, get_tpc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_order_id() -> str:
    return f"order-{uuid.uuid4().hex}"


ITEMS = [{"item_id": "item-abc", "quantity": 3}]
TOTAL_COST = 250
USER_ID = "user-test-1"


# ---------------------------------------------------------------------------
# TPC-04: Coordinator happy path -- all prepare YES -> commit
# ---------------------------------------------------------------------------

async def test_2pc_all_prepare_yes_commits(tpc_db, clean_tpc_db):
    """All prepare votes YES -> coordinator commits, TPC ends in COMMITTED."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ps, \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_pp, \
         patch("grpc_server.commit_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_cs, \
         patch("grpc_server.commit_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_cp:

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is True
    assert result["error_message"] == ""

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "COMMITTED"

    # Transport functions should have been called
    mock_ps.assert_called_once()
    mock_pp.assert_called_once()
    mock_cs.assert_called_once()
    mock_cp.assert_called_once()


# ---------------------------------------------------------------------------
# TPC-04: Coordinator -- prepare NO vote -> abort
# ---------------------------------------------------------------------------

async def test_2pc_prepare_no_aborts(tpc_db, clean_tpc_db):
    """Payment prepare returns NO -> coordinator aborts, TPC ends in ABORTED."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ps, \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock,
               return_value={"success": False, "error_message": "insufficient funds"}) as mock_pp, \
         patch("grpc_server.abort_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_as, \
         patch("grpc_server.abort_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ap:

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is False

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "ABORTED"

    # Abort should have been called
    mock_as.assert_called_once()
    mock_ap.assert_called_once()


# ---------------------------------------------------------------------------
# TPC-04: Coordinator -- prepare exception -> abort
# ---------------------------------------------------------------------------

async def test_2pc_prepare_exception_aborts(tpc_db, clean_tpc_db):
    """prepare_stock raises Exception -> coordinator aborts, TPC ends ABORTED."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock,
               side_effect=Exception("connection refused")) as mock_ps, \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_pp, \
         patch("grpc_server.abort_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_as, \
         patch("grpc_server.abort_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ap:

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is False

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "ABORTED"


# ---------------------------------------------------------------------------
# TPC-04: Exactly-once -- already committed returns success
# ---------------------------------------------------------------------------

async def test_2pc_exactly_once(tpc_db, clean_tpc_db):
    """Already COMMITTED TPC record -> returns success without calling transport."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()
    # Pre-create a COMMITTED record
    await create_tpc_record(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)
    tpc_key = f"{{tpc:{order_id}}}"
    await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    await transition_tpc_state(tpc_db, tpc_key, "PREPARING", "COMMITTING")
    await transition_tpc_state(tpc_db, tpc_key, "COMMITTING", "COMMITTED")

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock) as mock_ps, \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock) as mock_pp:

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is True
    mock_ps.assert_not_called()
    mock_pp.assert_not_called()


# ---------------------------------------------------------------------------
# TPC-05: WAL -- COMMITTING persisted BEFORE commit transport calls
# ---------------------------------------------------------------------------

async def test_2pc_wal_commit_persisted(tpc_db, clean_tpc_db):
    """WAL: transition to COMMITTING happens BEFORE any commit_stock/commit_payment."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()
    call_order = []

    async def track_transition(db, key, from_s, to_s, flag_field="", flag_value=""):
        call_order.append(("transition", from_s, to_s))
        # Call real function
        from tpc import transition_tpc_state as real_transition
        return await real_transition(db, key, from_s, to_s, flag_field, flag_value)

    async def track_commit_stock(*args, **kwargs):
        call_order.append(("commit_stock",))
        return {"success": True, "error_message": ""}

    async def track_commit_payment(*args, **kwargs):
        call_order.append(("commit_payment",))
        return {"success": True, "error_message": ""}

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}), \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}), \
         patch("grpc_server.commit_stock", side_effect=track_commit_stock), \
         patch("grpc_server.commit_payment", side_effect=track_commit_payment), \
         patch("grpc_server.transition_tpc_state", side_effect=track_transition):

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is True

    # Find the COMMITTING transition and commit calls
    committing_idx = next(
        i for i, c in enumerate(call_order)
        if c[0] == "transition" and c[2] == "COMMITTING"
    )
    commit_stock_idx = next(
        i for i, c in enumerate(call_order) if c[0] == "commit_stock"
    )
    commit_payment_idx = next(
        i for i, c in enumerate(call_order) if c[0] == "commit_payment"
    )

    assert committing_idx < commit_stock_idx, "COMMITTING WAL must precede commit_stock"
    assert committing_idx < commit_payment_idx, "COMMITTING WAL must precede commit_payment"


# ---------------------------------------------------------------------------
# TPC-05: WAL -- ABORTING persisted BEFORE abort transport calls
# ---------------------------------------------------------------------------

async def test_2pc_wal_abort_persisted(tpc_db, clean_tpc_db):
    """WAL: transition to ABORTING happens BEFORE any abort_stock/abort_payment."""
    from grpc_server import run_2pc_checkout

    order_id = new_order_id()
    call_order = []

    async def track_transition(db, key, from_s, to_s, flag_field="", flag_value=""):
        call_order.append(("transition", from_s, to_s))
        from tpc import transition_tpc_state as real_transition
        return await real_transition(db, key, from_s, to_s, flag_field, flag_value)

    async def track_abort_stock(*args, **kwargs):
        call_order.append(("abort_stock",))
        return {"success": True, "error_message": ""}

    async def track_abort_payment(*args, **kwargs):
        call_order.append(("abort_payment",))
        return {"success": True, "error_message": ""}

    with patch("grpc_server.prepare_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}), \
         patch("grpc_server.prepare_payment", new_callable=AsyncMock,
               return_value={"success": False, "error_message": "insufficient funds"}), \
         patch("grpc_server.abort_stock", side_effect=track_abort_stock), \
         patch("grpc_server.abort_payment", side_effect=track_abort_payment), \
         patch("grpc_server.transition_tpc_state", side_effect=track_transition):

        result = await run_2pc_checkout(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)

    assert result["success"] is False

    # Find the ABORTING transition and abort calls
    aborting_idx = next(
        i for i, c in enumerate(call_order)
        if c[0] == "transition" and c[2] == "ABORTING"
    )
    abort_stock_idx = next(
        i for i, c in enumerate(call_order) if c[0] == "abort_stock"
    )
    abort_payment_idx = next(
        i for i, c in enumerate(call_order) if c[0] == "abort_payment"
    )

    assert aborting_idx < abort_stock_idx, "ABORTING WAL must precede abort_stock"
    assert aborting_idx < abort_payment_idx, "ABORTING WAL must precede abort_payment"


# ---------------------------------------------------------------------------
# TPC-06: Recovery -- PREPARING state -> presumed abort
# ---------------------------------------------------------------------------

async def test_recovery_preparing_aborts(tpc_db, clean_tpc_db):
    """Recovery: stale PREPARING record -> abort_stock + abort_payment -> ABORTED."""
    from recovery import recover_incomplete_tpc

    order_id = new_order_id()
    await create_tpc_record(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)
    tpc_key = f"{{tpc:{order_id}}}"
    await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")

    # Make stale (updated_at = 0)
    await tpc_db.hset(tpc_key, "updated_at", "0")

    with patch("recovery.abort_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_as, \
         patch("recovery.abort_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ap, \
         patch("recovery.commit_stock", new_callable=AsyncMock) as mock_cs, \
         patch("recovery.commit_payment", new_callable=AsyncMock) as mock_cp:

        await recover_incomplete_tpc(tpc_db)

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "ABORTED"

    mock_as.assert_called_once()
    mock_ap.assert_called_once()
    mock_cs.assert_not_called()
    mock_cp.assert_not_called()


# ---------------------------------------------------------------------------
# TPC-06: Recovery -- COMMITTING state -> re-send commits
# ---------------------------------------------------------------------------

async def test_recovery_committing_commits(tpc_db, clean_tpc_db):
    """Recovery: stale COMMITTING record -> commit_stock + commit_payment -> COMMITTED."""
    from recovery import recover_incomplete_tpc

    order_id = new_order_id()
    await create_tpc_record(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)
    tpc_key = f"{{tpc:{order_id}}}"
    await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    await transition_tpc_state(tpc_db, tpc_key, "PREPARING", "COMMITTING")

    # Make stale
    await tpc_db.hset(tpc_key, "updated_at", "0")

    with patch("recovery.commit_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_cs, \
         patch("recovery.commit_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_cp, \
         patch("recovery.abort_stock", new_callable=AsyncMock) as mock_as, \
         patch("recovery.abort_payment", new_callable=AsyncMock) as mock_ap:

        await recover_incomplete_tpc(tpc_db)

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "COMMITTED"

    mock_cs.assert_called_once()
    mock_cp.assert_called_once()
    mock_as.assert_not_called()
    mock_ap.assert_not_called()


# ---------------------------------------------------------------------------
# TPC-06: Recovery -- ABORTING state -> re-send aborts
# ---------------------------------------------------------------------------

async def test_recovery_aborting_aborts(tpc_db, clean_tpc_db):
    """Recovery: stale ABORTING record -> abort_stock + abort_payment -> ABORTED."""
    from recovery import recover_incomplete_tpc

    order_id = new_order_id()
    await create_tpc_record(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)
    tpc_key = f"{{tpc:{order_id}}}"
    await transition_tpc_state(tpc_db, tpc_key, "INIT", "PREPARING")
    await transition_tpc_state(tpc_db, tpc_key, "PREPARING", "ABORTING")

    # Make stale
    await tpc_db.hset(tpc_key, "updated_at", "0")

    with patch("recovery.abort_stock", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_as, \
         patch("recovery.abort_payment", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_ap:

        await recover_incomplete_tpc(tpc_db)

    record = await get_tpc(tpc_db, order_id)
    assert record["state"] == "ABORTED"

    mock_as.assert_called_once()
    mock_ap.assert_called_once()


# ---------------------------------------------------------------------------
# TPC-06: Recovery -- skips SAGA records
# ---------------------------------------------------------------------------

async def test_recovery_skips_saga(tpc_db, clean_tpc_db):
    """Recovery scanner only processes {tpc:*} keys, not {saga:*} keys."""
    from recovery import recover_incomplete_tpc
    from saga import create_saga_record, get_saga

    order_id = new_order_id()

    # Create a SAGA record in STARTED state
    await create_saga_record(tpc_db, order_id, USER_ID, ITEMS, TOTAL_COST)
    # Make it stale
    saga_key = f"{{saga:{order_id}}}"
    await tpc_db.hset(saga_key, "updated_at", "0")

    with patch("recovery.abort_stock", new_callable=AsyncMock) as mock_as, \
         patch("recovery.abort_payment", new_callable=AsyncMock) as mock_ap, \
         patch("recovery.commit_stock", new_callable=AsyncMock) as mock_cs, \
         patch("recovery.commit_payment", new_callable=AsyncMock) as mock_cp:

        await recover_incomplete_tpc(tpc_db)

    # SAGA record state unchanged
    saga = await get_saga(tpc_db, order_id)
    assert saga["state"] == "STARTED"

    # No transport functions called
    mock_as.assert_not_called()
    mock_ap.assert_not_called()
    mock_cs.assert_not_called()
    mock_cp.assert_not_called()


# ---------------------------------------------------------------------------
# TPC-07: Toggle -- TRANSACTION_PATTERN=saga uses SAGA path
# ---------------------------------------------------------------------------

async def test_pattern_toggle_saga(tpc_db, clean_tpc_db):
    """TRANSACTION_PATTERN=saga -> StartCheckout calls run_checkout."""
    from grpc_server import OrchestratorServiceServicer

    with patch("grpc_server.TRANSACTION_PATTERN", "saga"), \
         patch("grpc_server.run_checkout", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_saga, \
         patch("grpc_server.run_2pc_checkout", new_callable=AsyncMock) as mock_2pc:

        servicer = OrchestratorServiceServicer(tpc_db)

        # Create a minimal mock request
        class MockItem:
            def __init__(self):
                self.item_id = "item-1"
                self.quantity = 1

        class MockRequest:
            order_id = "order-toggle-saga"
            user_id = "user-1"
            total_cost = 100
            items = [MockItem()]

        result = await servicer.StartCheckout(MockRequest(), None)

    mock_saga.assert_called_once()
    mock_2pc.assert_not_called()


# ---------------------------------------------------------------------------
# TPC-07: Toggle -- TRANSACTION_PATTERN=2pc uses 2PC path
# ---------------------------------------------------------------------------

async def test_pattern_toggle_2pc(tpc_db, clean_tpc_db):
    """TRANSACTION_PATTERN=2pc -> StartCheckout calls run_2pc_checkout."""
    from grpc_server import OrchestratorServiceServicer

    with patch("grpc_server.TRANSACTION_PATTERN", "2pc"), \
         patch("grpc_server.run_checkout", new_callable=AsyncMock) as mock_saga, \
         patch("grpc_server.run_2pc_checkout", new_callable=AsyncMock,
               return_value={"success": True, "error_message": ""}) as mock_2pc:

        servicer = OrchestratorServiceServicer(tpc_db)

        class MockItem:
            def __init__(self):
                self.item_id = "item-1"
                self.quantity = 1

        class MockRequest:
            order_id = "order-toggle-2pc"
            user_id = "user-1"
            total_cost = 100
            items = [MockItem()]

        result = await servicer.StartCheckout(MockRequest(), None)

    mock_2pc.assert_called_once()
    mock_saga.assert_not_called()
