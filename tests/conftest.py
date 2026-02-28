import asyncio
import os
import sys
import importlib

import pytest
import pytest_asyncio
import redis.asyncio as redis
import grpc.aio
from msgspec import msgpack, Struct

# ---------------------------------------------------------------------------
# sys.path manipulation to import service modules
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stock_path = os.path.join(_repo_root, "stock")
_payment_path = os.path.join(_repo_root, "payment")
_orchestrator_path = os.path.join(_repo_root, "orchestrator")

# Import stock servicer and pb2 stubs
sys.path.insert(0, _stock_path)
import grpc_server as stock_grpc_mod  # noqa: E402

StockServiceServicer = stock_grpc_mod.StockServiceServicer
from stock_pb2_grpc import add_StockServiceServicer_to_server  # noqa: E402
sys.path.pop(0)

# Import payment servicer and pb2 stubs (clear grpc_server from cache first)
sys.path.insert(0, _payment_path)
if "grpc_server" in sys.modules:
    del sys.modules["grpc_server"]
import grpc_server as payment_grpc_mod  # noqa: E402

PaymentServiceServicer = payment_grpc_mod.PaymentServiceServicer
from payment_pb2_grpc import add_PaymentServiceServicer_to_server  # noqa: E402
sys.path.pop(0)

# Keep orchestrator on path for tests to import client functions
sys.path.insert(0, _orchestrator_path)

# Import orchestrator servicer and pb2 stubs (clear grpc_server from cache)
if "grpc_server" in sys.modules:
    del sys.modules["grpc_server"]
import grpc_server as orchestrator_grpc_mod  # noqa: E402

OrchestratorServiceServicer = orchestrator_grpc_mod.OrchestratorServiceServicer
from orchestrator_pb2_grpc import (  # noqa: E402
    OrchestratorServiceStub,
    add_OrchestratorServiceServicer_to_server,
)


# ---------------------------------------------------------------------------
# Msgspec structs for seeding Redis test data
# ---------------------------------------------------------------------------

class StockValue(Struct):
    stock: int
    price: int


class UserValue(Struct):
    credit: int


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_db():
    """Connect to Redis, flush test DB, yield, then flush and close."""
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD", None)
    db_index = int(os.environ.get("REDIS_DB", "0"))

    db = redis.Redis(host=host, port=port, password=password, db=db_index)
    await db.flushdb()
    yield db
    await db.flushdb()
    await db.aclose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def orchestrator_db():
    """Separate Redis client for orchestrator SAGA records (db=3).

    Uses a different DB index from Stock (db=0) and Payment (db=0 in tests)
    to avoid key collisions.
    """
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD", None)

    db = redis.Redis(host=host, port=port, password=password, db=3)
    await db.flushdb()
    yield db
    await db.flushdb()
    await db.aclose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def stock_grpc_server(redis_db):
    """Start the Stock gRPC server on :50051 backed by the test Redis DB."""
    server = grpc.aio.server()
    add_StockServiceServicer_to_server(StockServiceServicer(redis_db), server)
    server.add_insecure_port("[::]:50051")
    await server.start()
    yield server
    await server.stop(grace=0)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def payment_grpc_server(redis_db):
    """Start the Payment gRPC server on :50052 backed by the test Redis DB."""
    server = grpc.aio.server()
    add_PaymentServiceServicer_to_server(PaymentServiceServicer(redis_db), server)
    server.add_insecure_port("[::]:50052")
    await server.start()
    yield server
    await server.stop(grace=0)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_test_data(redis_db):
    """Seed known test data into Redis for integration tests."""
    await redis_db.set("test-item-1", msgpack.encode(StockValue(stock=100, price=10)))
    await redis_db.set("test-user-1", msgpack.encode(UserValue(credit=1000)))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def grpc_clients(stock_grpc_server, payment_grpc_server, seed_test_data):
    """Initialise orchestrator gRPC client stubs pointing at test servers."""
    from client import init_grpc_clients, close_grpc_clients
    await init_grpc_clients(
        stock_addr="localhost:50051",
        payment_addr="localhost:50052",
    )
    yield
    await close_grpc_clients()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def orchestrator_grpc_server(orchestrator_db, grpc_clients):
    """Start the Orchestrator gRPC server on :50053 backed by the orchestrator Redis DB.

    Depends on grpc_clients to ensure Stock/Payment gRPC clients are initialized
    before the orchestrator server starts (orchestrator calls them during checkout).
    """
    server = grpc.aio.server()
    add_OrchestratorServiceServicer_to_server(
        OrchestratorServiceServicer(orchestrator_db), server
    )
    server.add_insecure_port("[::]:50053")
    await server.start()
    yield server
    await server.stop(grace=0)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def orchestrator_stub(orchestrator_grpc_server):
    """Create a gRPC stub for the Orchestrator service on :50053."""
    channel = grpc.aio.insecure_channel("localhost:50053")
    stub = OrchestratorServiceStub(channel)
    yield stub
    await channel.close()


# ---------------------------------------------------------------------------
# Function-scoped fixtures for test isolation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function", loop_scope="session")
async def clean_orchestrator_db(orchestrator_db):
    """Flush the orchestrator Redis DB before each test for SAGA record isolation."""
    await orchestrator_db.flushdb()
    yield
    # No cleanup needed after — next test will flush at start
