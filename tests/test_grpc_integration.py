"""Integration tests covering GRPC-01 through GRPC-04.

With asyncio_mode = auto in pytest.ini, no @pytest.mark.asyncio decorators
are required on async test functions.

All mutating tests use unique idempotency keys to avoid cross-test interference.
The session-scoped fixtures in conftest.py ensure servers and test data are set
up once for the entire test session.
"""

import sys
import os

# Ensure orchestrator path is available for imports
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_orchestrator_path = os.path.join(_repo_root, "orchestrator")
if _orchestrator_path not in sys.path:
    sys.path.insert(0, _orchestrator_path)


# ---------------------------------------------------------------------------
# GRPC-01: Proto stubs import without error (smoke test)
# ---------------------------------------------------------------------------

def test_proto_stubs_importable():
    """GRPC-01: Generated proto stubs import without error."""
    import stock_pb2
    import stock_pb2_grpc
    import payment_pb2
    import payment_pb2_grpc

    # Verify key request/response classes exist
    assert hasattr(stock_pb2, "ReserveStockRequest")
    assert hasattr(stock_pb2, "StockResponse")
    assert hasattr(stock_pb2_grpc, "StockServiceStub")
    assert hasattr(payment_pb2, "ChargePaymentRequest")
    assert hasattr(payment_pb2, "PaymentResponse")
    assert hasattr(payment_pb2_grpc, "PaymentServiceStub")


# ---------------------------------------------------------------------------
# GRPC-02: gRPC server reachable on port 50051
# ---------------------------------------------------------------------------

async def test_grpc_server_reachable(grpc_clients):
    """GRPC-02: Stock gRPC server is reachable on :50051."""
    from client import check_stock

    result = await check_stock("nonexistent-item")
    # Server responded — connection was established and a response was returned
    assert isinstance(result, dict)
    assert "success" in result


# ---------------------------------------------------------------------------
# GRPC-03: Orchestrator client calls gRPC (not HTTP)
# ---------------------------------------------------------------------------

async def test_client_reserve_stock(grpc_clients, seed_test_data):
    """GRPC-03: Orchestrator client calls Stock service via gRPC."""
    from client import reserve_stock

    result = await reserve_stock(
        item_id="test-item-1",
        quantity=1,
        idempotency_key="saga:test-grpc03:step:reserve",
    )
    assert result["success"] is True
    assert result["error_message"] == ""


async def test_client_charge_payment(grpc_clients, seed_test_data):
    """GRPC-03: Orchestrator client calls Payment service via gRPC."""
    from client import charge_payment

    result = await charge_payment(
        user_id="test-user-1",
        amount=10,
        idempotency_key="saga:test-grpc03:step:charge",
    )
    assert result["success"] is True
    assert result["error_message"] == ""


# ---------------------------------------------------------------------------
# GRPC-04: Duplicate idempotency_key returns same result without re-executing
# ---------------------------------------------------------------------------

async def test_idempotency_deduplication(grpc_clients, seed_test_data):
    """GRPC-04: Duplicate idempotency_key returns same result without re-executing."""
    from client import reserve_stock

    ikey = "saga:test-grpc04:step:reserve"

    # First call — should succeed and reserve stock
    r1 = await reserve_stock(item_id="test-item-1", quantity=5, idempotency_key=ikey)
    assert r1["success"] is True

    # Second call with SAME idempotency_key — should return cached result
    r2 = await reserve_stock(item_id="test-item-1", quantity=5, idempotency_key=ikey)
    assert r2["success"] is True

    # Both responses must be identical (idempotent)
    assert r1 == r2


async def test_idempotency_different_keys_execute_separately(grpc_clients, seed_test_data):
    """GRPC-04 corollary: Different idempotency keys execute independently."""
    from client import charge_payment

    r1 = await charge_payment(
        user_id="test-user-1",
        amount=5,
        idempotency_key="saga:test-grpc04-a:step:charge",
    )
    r2 = await charge_payment(
        user_id="test-user-1",
        amount=5,
        idempotency_key="saga:test-grpc04-b:step:charge",
    )
    # Both should succeed independently
    assert r1["success"] is True
    assert r2["success"] is True


# ---------------------------------------------------------------------------
# Business error: insufficient stock returns error in response fields
# ---------------------------------------------------------------------------

async def test_reserve_stock_insufficient(grpc_clients, seed_test_data):
    """Business error returned in response fields, not gRPC status code."""
    from client import reserve_stock

    result = await reserve_stock(
        item_id="test-item-1",
        quantity=999999,
        idempotency_key="saga:test-insufficient:step:reserve",
    )
    # Business failure — success=False, but no gRPC exception raised
    assert result["success"] is False
    assert result["error_message"] != ""
