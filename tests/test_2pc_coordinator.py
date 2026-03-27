"""
Unit tests for TPC-07: TRANSACTION_PATTERN toggle.

Tests verify that OrchestratorServiceServicer.StartCheckout routes to the
correct strategy (saga vs 2pc) based on TRANSACTION_PATTERN env var.

Coverage for TPC-04 (coordinator), TPC-05 (WAL), and TPC-06 (recovery) is
provided by test_strategies.py (TwoPhaseStrategy unit tests) and
test_workflow_store.py (WorkflowStore state machine tests).

All tests use:
  - Real Redis (db=3 for TPC records via tpc_db fixture)
  - unittest.mock.patch for transport functions (no real gRPC calls)
  - Unique order_ids per test (uuid4) to prevent cross-test interference

With asyncio_mode = auto in pytest.ini no @pytest.mark.asyncio decorators needed.
"""

import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)

from grpc_server import OrchestratorServiceServicer
from workflow_store import WorkflowStore
from workflow_engine import WorkflowEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def new_order_id() -> str:
    return f"order-{uuid.uuid4().hex}"


ITEMS = [{"item_id": "item-abc", "quantity": 3}]
TOTAL_COST = 250
USER_ID = "user-test-1"


# ---------------------------------------------------------------------------
# TPC-07: Toggle -- TRANSACTION_PATTERN=saga uses SAGA path
# ---------------------------------------------------------------------------

async def test_pattern_toggle_saga(tpc_db, clean_tpc_db):
    """TRANSACTION_PATTERN=saga -> StartCheckout uses saga strategy via engine."""
    store = WorkflowStore(tpc_db)
    engine = WorkflowEngine(store=store, db=tpc_db)

    with patch.object(engine, "execute", new_callable=AsyncMock,
                      return_value={"success": True, "error_message": ""}) as mock_execute, \
         patch("grpc_server.TRANSACTION_PATTERN", "saga"):

        servicer = OrchestratorServiceServicer(tpc_db, engine)

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

    mock_execute.assert_called_once()
    call_args = mock_execute.call_args
    # Verify definition uses saga strategy
    definition = call_args[0][1]  # second positional arg
    assert definition.strategy == "saga"


# ---------------------------------------------------------------------------
# TPC-07: Toggle -- TRANSACTION_PATTERN=2pc uses 2PC path
# ---------------------------------------------------------------------------

async def test_pattern_toggle_2pc(tpc_db, clean_tpc_db):
    """TRANSACTION_PATTERN=2pc -> StartCheckout uses 2pc strategy via engine."""
    store = WorkflowStore(tpc_db)
    engine = WorkflowEngine(store=store, db=tpc_db)

    with patch.object(engine, "execute", new_callable=AsyncMock,
                      return_value={"success": True, "error_message": ""}) as mock_execute, \
         patch("grpc_server.TRANSACTION_PATTERN", "2pc"):

        servicer = OrchestratorServiceServicer(tpc_db, engine)

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

    mock_execute.assert_called_once()
    call_args = mock_execute.call_args
    # Verify definition uses 2pc strategy
    definition = call_args[0][1]  # second positional arg
    assert definition.strategy == "2pc"
