# Phase 2: gRPC Communication - Research

**Researched:** 2026-02-28
**Domain:** gRPC AsyncIO (grpcio 1.78), Protocol Buffers (proto3), Redis Lua idempotency, Quart dual-server integration
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Proto contract scope**
- SAGA-only RPCs: define only operations the orchestrator needs (Stock: reserve, release, check; Payment: charge, refund, check)
- One proto file per service: `stock.proto` and `payment.proto`
- Proto files live in a top-level `protos/` directory at the repo root; generated Python stubs go into each service
- Minimal message fields: business data + idempotency_key only; tracing/timestamps go in gRPC metadata headers, not proto fields

**Idempotency key design**
- Composite key format: `saga:{saga_id}:step:{step_name}` — deterministic, debuggable, tied to SAGA lifecycle
- Orchestrator generates all idempotency keys; services only receive and deduplicate
- Idempotency records stored in Redis with TTL (e.g., 24h) — auto-expires, no cleanup needed
- Deduplication happens at the Lua script level: atomically check idempotency_key + execute operation in a single Redis call (no TOCTOU race)

**gRPC error handling**
- Business errors communicated via response status fields (success bool + error_message string), not gRPC status codes
- gRPC status codes reserved for transport/system errors only
- On duplicate idempotency key, return the stored result transparently — caller can't distinguish retry from first call
- Minimal error info: just success/fail + error message, no internal state leakage (stock levels, balances)
- Simple per-call deadline (e.g., 5s) for timeouts; no client-side retry logic (Phase 4 adds circuit breakers)

**Orchestrator scope in this phase**
- Create a thin gRPC client layer: async wrapper functions (reserve_stock, charge_payment, etc.) in an `orchestrator/` directory
- No orchestrator service in this phase — just the client module that Phase 3 imports
- All existing HTTP endpoints on Stock and Payment remain fully functional; gRPC is inter-service only
- Include basic integration tests (client → gRPC server → Redis) to prove wiring works

### Claude's Discretion
- Exact proto field types and naming conventions
- gRPC server startup integration with Quart (asyncio event loop sharing)
- Generated stub output directory structure
- Test fixture design and setup/teardown approach
- Specific deadline value (5s suggested, Claude can adjust)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| GRPC-01 | Proto definitions exist for Stock and Payment service operations used by the orchestrator | Standard proto3 structure documented; grpcio-tools 1.78 generates _pb2.py and _pb2_grpc.py stubs from .proto files |
| GRPC-02 | Stock and Payment services expose gRPC server alongside HTTP (dual-server: HTTP :5000, gRPC :50051) | Confirmed: `app.add_background_task()` in `before_serving` hook starts grpc.aio server as background coroutine; `asyncio.create_task` pattern verified via aiohttp/Quart parallelism |
| GRPC-03 | SAGA orchestrator communicates with Stock and Payment via gRPC (not HTTP) | Thin client module pattern: grpc.aio.insecure_channel + stub opened at startup, stored as module-level globals, closed at shutdown |
| GRPC-04 | gRPC calls include idempotency_key field in all mutation requests | Redis Lua atomic check-and-return pattern confirmed; idempotency_key as proto string field in every mutation RPC request message |
</phase_requirements>

---

## Summary

This phase adds gRPC servers to the Stock and Payment services alongside the existing Quart HTTP servers, defines proto3 contracts for all orchestrator-facing RPCs, implements Redis Lua-based idempotency deduplication, and provides a thin async client module in `orchestrator/` that Phase 3 will import.

The core technical challenge is running two async servers (Quart/Uvicorn on :5000 and grpc.aio on :50051) within a single asyncio event loop per service container. Quart's `app.add_background_task()` hook, called from `before_serving`, is the correct mechanism: it launches the gRPC server's `serve()` coroutine as a managed background task on the same event loop that Uvicorn owns. This avoids all event loop conflict issues documented in the grpc/grpc GitHub issue tracker. The channel must be closed in `after_serving` via `await server.stop()`.

The idempotency pattern is straightforward: every mutation RPC includes an `idempotency_key: string` field. The servicer runs a Lua script against Redis that atomically checks whether the key exists and either returns the cached result (duplicate) or stores the result and executes the operation. The Lua script must be registered in the `before_serving` hook so it is loaded once per worker. No TOCTOU race is possible because the entire check-and-write runs in a single Redis-serialized Lua execution.

**Primary recommendation:** Use `grpcio==1.78.0` + `grpcio-tools==1.78.0` + `protobuf>=6.31.1`. Start the gRPC server via `app.add_background_task(serve_grpc)` in `before_serving`. Use Lua scripts for atomic idempotency. Use per-RPC `timeout=5.0` on all client stubs.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| grpcio | 1.78.0 | gRPC runtime (server + client, async) | Official Google gRPC Python implementation; grpc.aio namespace is the asyncio API |
| grpcio-tools | 1.78.0 | protoc compiler + Python code generator | Bundles protoc, generates _pb2.py and _pb2_grpc.py from .proto files |
| protobuf | >=6.31.1 | Protocol Buffers runtime (message serialization) | Bundled dependency of grpcio-tools; version must be >= what grpcio-tools bundles |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest-asyncio | latest | async test fixtures for pytest | Required for session-scoped gRPC server fixtures in integration tests |
| pytest | latest | test runner | Existing project test pattern uses unittest; integration tests need pytest-asyncio |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| grpcio / grpc.aio | grpclib (pure Python) | grpclib is pure Python (no C extension), but less battle-tested, smaller community, does not implement all gRPC features |
| grpcio / grpc.aio | purerpc | Inactive project (last commit 2021); not viable |
| redis Lua dedup | WATCH/MULTI/EXEC | Lua is faster (single round trip), no WatchError contention, simpler code |

**Installation (per service: stock, payment; plus orchestrator dir):**
```bash
# Add to each service's requirements.txt
grpcio==1.78.0
protobuf>=6.31.1

# Dev/build only (not in service requirements.txt — run at codegen time):
grpcio-tools==1.78.0
```

**Code generation command (run from repo root):**
```bash
python -m grpc_tools.protoc \
  -I protos \
  --python_out=stock \
  --pyi_out=stock \
  --grpc_python_out=stock \
  protos/stock.proto

python -m grpc_tools.protoc \
  -I protos \
  --python_out=payment \
  --pyi_out=payment \
  --grpc_python_out=payment \
  protos/payment.proto

python -m grpc_tools.protoc \
  -I protos \
  --python_out=orchestrator \
  --pyi_out=orchestrator \
  --grpc_python_out=orchestrator \
  protos/stock.proto protos/payment.proto
```

---

## Architecture Patterns

### Recommended Project Structure
```
protos/
├── stock.proto            # canonical proto definition for Stock service
└── payment.proto          # canonical proto definition for Payment service

stock/
├── app.py                 # existing Quart HTTP server (unchanged routes)
├── grpc_server.py         # gRPC servicer impl + serve() coroutine
├── stock_pb2.py           # generated (do not edit)
├── stock_pb2.pyi          # generated type stubs
├── stock_pb2_grpc.py      # generated (do not edit)
└── requirements.txt       # add grpcio==1.78.0, protobuf>=6.31.1

payment/
├── app.py                 # existing Quart HTTP server (unchanged routes)
├── grpc_server.py         # gRPC servicer impl + serve() coroutine
├── payment_pb2.py         # generated
├── payment_pb2.pyi        # generated
├── payment_pb2_grpc.py    # generated
└── requirements.txt       # add grpcio==1.78.0, protobuf>=6.31.1

orchestrator/
├── client.py              # thin async gRPC client wrapper functions
├── stock_pb2.py           # generated (copy of stock stubs)
├── stock_pb2_grpc.py      # generated
├── payment_pb2.py         # generated (copy of payment stubs)
└── payment_pb2_grpc.py    # generated

tests/
└── test_grpc_integration.py  # integration tests (client → gRPC server → Redis)
```

### Pattern 1: Dual-Server Startup via Quart Background Task

**What:** Start the grpc.aio server as a background task from `before_serving`. Quart's `add_background_task` runs the coroutine on the same event loop that Uvicorn owns. Stop in `after_serving`.

**When to use:** Any Quart service that needs a long-lived background async server alongside the HTTP server.

```python
# stock/grpc_server.py
import grpc
import grpc.aio
from stock_pb2_grpc import add_StockServicer_to_server
from stock_servicer import StockServicer  # your impl

GRPC_PORT = 50051

async def serve_grpc(db) -> None:
    """Long-running coroutine: start gRPC server and wait for termination."""
    server = grpc.aio.server()
    add_StockServicer_to_server(StockServicer(db), server)
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    await server.start()
    await server.wait_for_termination()
```

```python
# stock/app.py  (additions to existing file)
from grpc_server import serve_grpc

@app.before_serving
async def startup():
    global db
    db = redis.Redis(...)
    # Start gRPC server on the same event loop
    app.add_background_task(serve_grpc, db)

@app.after_serving
async def shutdown():
    await db.aclose()
    # Background task is cancelled automatically by Quart on shutdown
```

**Source:** Quart background tasks docs (https://quart.palletsprojects.com/en/latest/how_to_guides/background_tasks/); aiohttp+gRPC dual-server pattern (https://blog.sneawo.com/blog/2022/01/23/how-to-use-asyncio-grpc-in-aiohttp-microservices/)

### Pattern 2: gRPC Servicer with Lua Idempotency

**What:** Servicer method checks idempotency key atomically in Redis before executing; returns cached result on duplicate.

**When to use:** All mutation RPCs (reserve, release, charge, refund).

```python
# stock/stock_servicer.py
import grpc
import grpc.aio
import json
from stock_pb2 import ReserveStockResponse
from stock_pb2_grpc import StockServicer as StockServicerBase

# Lua script: atomically check idempotency key and optionally store result
# Returns 1 (key already existed → return cached result) or 0 (new)
IDEMPOTENCY_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
return nil
"""

IDEMPOTENCY_SET_LUA = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return ARGV[1]
"""

class StockServicer(StockServicerBase):
    def __init__(self, db):
        self.db = db

    async def ReserveStock(self, request, context: grpc.aio.ServicerContext):
        ikey = f"idempotency:{request.idempotency_key}"
        ttl = 86400  # 24h

        # Atomic check
        cached = await self.db.eval(IDEMPOTENCY_LUA, 1, ikey)
        if cached is not None:
            stored = json.loads(cached)
            return ReserveStockResponse(**stored)

        # Execute business logic
        # ... subtract stock, build response ...
        response = ReserveStockResponse(success=True, error_message="")

        # Store result atomically
        result_json = json.dumps({"success": response.success,
                                  "error_message": response.error_message})
        await self.db.eval(IDEMPOTENCY_SET_LUA, 1, ikey, result_json, str(ttl))
        return response
```

**Note:** The two-Lua-call approach shown above still has a TOCTOU window between check and set. The locked decision requires a single atomic Lua that does both check-and-set in one call. See Code Examples section for the correct single-script pattern.

### Pattern 3: Thin Client Module (orchestrator/)

**What:** Module-level channel and stubs initialized once, reused per process. No connection created per RPC call (expensive).

**When to use:** gRPC client in any long-lived async process.

```python
# orchestrator/client.py
import grpc.aio
from stock_pb2 import ReserveStockRequest
from stock_pb2_grpc import StockStub
from payment_pb2 import ChargePaymentRequest
from payment_pb2_grpc import PaymentStub

_stock_channel = None
_payment_channel = None
_stock_stub = None
_payment_stub = None

STOCK_ADDR = "stock-service:50051"
PAYMENT_ADDR = "payment-service:50051"
RPC_TIMEOUT = 5.0  # seconds

async def init_clients():
    global _stock_channel, _payment_channel, _stock_stub, _payment_stub
    _stock_channel = grpc.aio.insecure_channel(STOCK_ADDR)
    _payment_channel = grpc.aio.insecure_channel(PAYMENT_ADDR)
    _stock_stub = StockStub(_stock_channel)
    _payment_stub = PaymentStub(_payment_channel)

async def close_clients():
    if _stock_channel:
        await _stock_channel.close()
    if _payment_channel:
        await _payment_channel.close()

async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    response = await _stock_stub.ReserveStock(
        ReserveStockRequest(
            item_id=item_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
        ),
        timeout=RPC_TIMEOUT,
    )
    return {"success": response.success, "error_message": response.error_message}
```

**Source:** gRPC performance best practices (reuse channels); grpc.aio AsyncIO API docs (https://grpc.github.io/grpc/python/grpc_asyncio.html)

### Pattern 4: Proto3 Message Design

**What:** Minimal message fields per locked decision — business data + idempotency_key only.

```protobuf
// protos/stock.proto
syntax = "proto3";
package stock;

service StockService {
  rpc ReserveStock(ReserveStockRequest) returns (StockResponse);
  rpc ReleaseStock(ReleaseStockRequest) returns (StockResponse);
  rpc CheckStock(CheckStockRequest)    returns (CheckStockResponse);
}

message ReserveStockRequest {
  string item_id        = 1;
  int32  quantity       = 2;
  string idempotency_key = 3;
}

message ReleaseStockRequest {
  string item_id        = 1;
  int32  quantity       = 2;
  string idempotency_key = 3;
}

message CheckStockRequest {
  string item_id = 1;
}

message StockResponse {
  bool   success       = 1;
  string error_message = 2;
}

message CheckStockResponse {
  bool   success       = 1;
  string error_message = 2;
  int32  stock         = 3;
  int32  price         = 4;
}
```

```protobuf
// protos/payment.proto
syntax = "proto3";
package payment;

service PaymentService {
  rpc ChargePayment(ChargePaymentRequest) returns (PaymentResponse);
  rpc RefundPayment(RefundPaymentRequest) returns (PaymentResponse);
  rpc CheckPayment(CheckPaymentRequest)   returns (CheckPaymentResponse);
}

message ChargePaymentRequest {
  string user_id        = 1;
  int32  amount         = 2;
  string idempotency_key = 3;
}

message RefundPaymentRequest {
  string user_id        = 1;
  int32  amount         = 2;
  string idempotency_key = 3;
}

message CheckPaymentRequest {
  string user_id = 1;
}

message PaymentResponse {
  bool   success       = 1;
  string error_message = 2;
}

message CheckPaymentResponse {
  bool   success       = 1;
  string error_message = 2;
  int32  credit        = 3;
}
```

**Note on CheckStock/CheckPayment:** These are read-only RPCs — no idempotency_key field needed per locked decision (idempotency_key only in mutation requests).

### Anti-Patterns to Avoid

- **Creating a new channel per RPC call:** gRPC channels are expensive to establish (HTTP/2 negotiation). Always reuse the channel; create once in `init_clients()`.
- **Using gRPC status codes for business errors:** Locked decision explicitly forbids this. All business success/failure goes in response fields, not `context.abort()`.
- **Calling `asyncio.run()` inside a Quart service:** Both Uvicorn and grpc.aio need the same event loop. Never call `asyncio.run()` from within a running ASGI application — it creates a new loop, causing "attached to a different loop" errors.
- **Forgetting `--pyi_out` in protoc invocation:** Without `.pyi` stub files, IDEs and type checkers have no type information for generated code. Always generate with `--pyi_out`.
- **Pinning protobuf to a version lower than what grpcio-tools bundles:** grpcio-tools generates code that requires the protobuf version it was built with. If runtime protobuf is older, you get `TypeError` on import. Use `protobuf>=6.31.1`.
- **Two separate Lua calls for check + set:** Any gap between `GET` and `SET` is a TOCTOU race. The check-and-set must be a single atomic Lua script.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Proto serialization | Custom binary encoding | protobuf (via grpcio) | Binary compatibility, schema evolution, code generation |
| gRPC transport | Raw HTTP/2 socket handling | grpcio / grpc.aio | Handles multiplexing, flow control, keepalive, TLS, retries |
| Async gRPC server threading | Manual thread pool + asyncio bridges | grpc.aio.server() | C extension manages C-level threads; exposing futures to asyncio is handled internally |
| Idempotency atomicity | Python-level lock + two Redis calls | Redis Lua script (single EVAL) | Network round trips create race windows; Lua is server-side atomic |
| Code generation from .proto | Parsing proto3 syntax manually | grpcio-tools (grpc_tools.protoc) | Complete proto3 language support, generates correct Python with proper imports |

**Key insight:** The grpcio C extension manages its own thread pool internally; the grpc.aio interface bridges that to asyncio coroutines without requiring you to manage threads. Never try to "help" it with `loop.run_in_executor`.

---

## Common Pitfalls

### Pitfall 1: Event Loop Conflict Between Uvicorn and grpc.aio
**What goes wrong:** gRPC server objects created on a different event loop than Uvicorn's raise `RuntimeError: Task got Future attached to a different loop`.
**Why it happens:** Uvicorn creates and owns the asyncio event loop. If you call `asyncio.run(serve_grpc())` in a thread or subprocess, a second event loop is created, and gRPC objects from one loop can't be awaited on the other.
**How to avoid:** Use `app.add_background_task(serve_grpc, db)` inside `@app.before_serving`. Quart runs the background task on the same event loop as the ASGI app (which is Uvicorn's loop).
**Warning signs:** `RuntimeError: attached to a different loop` or `RuntimeError: Task got Future <Future ...> attached to a different loop`.

### Pitfall 2: grpcio-tools Version Mismatch with protobuf Runtime
**What goes wrong:** `TypeError: Descriptors cannot be created directly` or `ImportError` when importing generated `_pb2.py` files.
**Why it happens:** grpcio-tools bundles its own protoc and generates code that requires a minimum protobuf runtime version. If `protobuf` in requirements.txt is older than what was used for generation, the generated code fails at import.
**How to avoid:** Pin `protobuf>=6.31.1` in requirements.txt (matches grpcio-tools 1.78.0's bundled version). Always regenerate stubs after upgrading grpcio-tools.
**Warning signs:** Stack trace on `import stock_pb2` with `TypeError` or `ImportError` mentioning `Descriptors`.

### Pitfall 3: Relative Imports in Generated gRPC Stubs
**What goes wrong:** `ImportError: attempted relative import beyond top-level package` when importing `_pb2_grpc.py`.
**Why it happens:** `grpc_tools.protoc` generates `_pb2_grpc.py` with an import like `from . import stock_pb2 as stock__pb2` or `import stock_pb2 as stock__pb2` depending on protoc version. The import style depends on whether the output directory is a Python package.
**How to avoid:** Add an `__init__.py` to `stock/`, `payment/`, and `orchestrator/` directories so they are packages. Alternatively, run protoc with `-I protos` pointing to the proto source dir, not to the output dir. Verify generated import style and add `__init__.py` as needed.
**Warning signs:** `ImportError` on the `_pb2_grpc.py` import when running in a module context.

### Pitfall 4: grpc.aio Server Not Receiving Requests (Port Not Added Before Start)
**What goes wrong:** gRPC server starts but clients get `StatusCode.UNAVAILABLE`.
**Why it happens:** `add_insecure_port()` must be called before `server.start()`. The order is: create server → add servicers → add port → start.
**How to avoid:** Always follow: `grpc.aio.server()` → `add_*Servicer_to_server(...)` → `server.add_insecure_port(...)` → `await server.start()`.
**Warning signs:** `UNAVAILABLE` status on all RPCs; no error logged by the server itself.

### Pitfall 5: Background Task Cancellation vs. Graceful gRPC Shutdown
**What goes wrong:** gRPC server gets hard-cancelled by Quart on shutdown, leaving in-flight RPCs stranded.
**Why it happens:** Quart cancels background tasks on `after_serving`. If the `serve_grpc` coroutine is `await server.wait_for_termination()` without a preceding `await server.stop(grace=...)`, there is no graceful drain.
**How to avoid:** Store the `server` object in a module-level global or close-over it. In `after_serving`, call `await grpc_server.stop(grace=5.0)` before the background task is cancelled by Quart.
**Warning signs:** Logged gRPC cancellation errors during container shutdown.

### Pitfall 6: pytest-asyncio Event Loop Scope Mismatch in Integration Tests
**What goes wrong:** `RuntimeError: Task got Future attached to a different loop` in pytest, or gRPC server fixture is torn down and recreated per test.
**Why it happens:** By default, pytest-asyncio creates a new event loop per test. A session-scoped server fixture created on loop A is used by a test on loop B.
**How to avoid:** Configure `asyncio_mode = "auto"` in `pytest.ini` and use `@pytest_asyncio.fixture(scope="session", loop_scope="session")` for the gRPC server fixture. All tests in the session share one event loop.
**Warning signs:** Tests pass in isolation but fail when run together; "attached to a different loop" in test output.

---

## Code Examples

Verified patterns from official sources and the aiohttp integration blog (cross-verified with grpc.aio API docs):

### Complete Dual-Server Startup (Quart)
```python
# stock/app.py
import grpc.aio
from quart import Quart
import redis.asyncio as redis
import os
from grpc_server import serve_grpc   # defined in stock/grpc_server.py

app = Quart("stock-service")
db: redis.Redis = None

@app.before_serving
async def startup():
    global db
    db = redis.Redis(
        host=os.environ['REDIS_HOST'],
        port=int(os.environ['REDIS_PORT']),
        password=os.environ['REDIS_PASSWORD'],
        db=int(os.environ['REDIS_DB']),
    )
    # Runs serve_grpc(db) as a background coroutine on the Uvicorn event loop
    app.add_background_task(serve_grpc, db)

@app.after_serving
async def shutdown():
    await db.aclose()
    # grpc server stop is handled inside serve_grpc via grpc_server global or graceful stop
```

```python
# stock/grpc_server.py
import grpc
import grpc.aio
from stock_pb2_grpc import add_StockServiceServicer_to_server
from stock_servicer import StockServicer

_grpc_server: grpc.aio.Server = None

async def serve_grpc(db) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_StockServiceServicer_to_server(StockServicer(db), _grpc_server)
    _grpc_server.add_insecure_port("[::]:50051")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()

async def stop_grpc_server():
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5.0)
```

### Atomic Single-Script Idempotency (Correct Pattern)
```python
# stock/stock_servicer.py
import json
import grpc.aio
from stock_pb2 import StockResponse
from stock_pb2_grpc import StockServiceServicer

# Single atomic Lua: check if key exists; if yes return cached; if no, set placeholder
# Caller stores real result in a second set after execution (acceptable: TOCTOU only
# affects concurrent duplicate calls — both will execute, second write wins,
# both return same result because first write TTL prevents further duplicates).
#
# TRUE single-atomic pattern: check + execute must both be in Lua.
# For stock, Lua script does: check idempotency key → if exists return cached →
# else check stock level → if sufficient subtract → set idempotency key with result → return.

RESERVE_STOCK_LUA = """
local ikey = KEYS[1]
local item_key = KEYS[2]
local quantity = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local cached = redis.call('GET', ikey)
if cached then
    return cached
end
local raw = redis.call('GET', item_key)
if not raw then
    local result = cjson.encode({success=false, error_message='item not found'})
    redis.call('SET', ikey, result, 'EX', ttl)
    return result
end
-- raw is msgpack — cannot decode in Lua without a library
-- Return sentinel to signal: idempotency key is new, proceed in Python
redis.call('SET', ikey, '__PROCESSING__', 'EX', 30)
return '__NEW__'
"""
# NOTE: Because stock values are msgpack-encoded, full stock-subtract cannot be done in Lua
# without a msgpack Lua library. The practical pattern is:
#   1. SET idempotency_key = '__PROCESSING__' with short TTL (30s) atomically via SET NX
#   2. Execute operation in Python (Redis-level atomicity for stock itself is handled separately)
#   3. SET idempotency_key = json_result with 24h TTL
# The SET NX in step 1 prevents concurrent duplicates from both executing. Callers that
# find '__PROCESSING__' treat it as "in flight" and can retry after short delay.

IDEMPOTENCY_ACQUIRE_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return existing
end
redis.call('SET', KEYS[1], '__PROCESSING__', 'EX', ARGV[1])
return '__NEW__'
"""
```

### pytest Integration Test Fixture
```python
# tests/test_grpc_integration.py
import pytest
import pytest_asyncio
import grpc.aio
from stock_pb2_grpc import StockServiceStub
from stock_pb2 import ReserveStockRequest
# Assume stock service is running on localhost:50051 (docker-compose or started in fixture)

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def stock_stub():
    channel = grpc.aio.insecure_channel("localhost:50051")
    stub = StockServiceStub(channel)
    yield stub
    await channel.close()

@pytest.mark.asyncio(loop_scope="session")
async def test_reserve_stock_idempotency(stock_stub):
    key = "saga:test-saga-1:step:reserve"
    request = ReserveStockRequest(item_id="test-item", quantity=1, idempotency_key=key)
    r1 = await stock_stub.ReserveStock(request, timeout=5.0)
    r2 = await stock_stub.ReserveStock(request, timeout=5.0)  # duplicate
    assert r1.success == r2.success  # idempotent: same result
```

### Client Wrapper with Timeout
```python
# orchestrator/client.py
import grpc.aio
from stock_pb2_grpc import StockServiceStub
from stock_pb2 import ReserveStockRequest, ReleaseStockRequest, CheckStockRequest
from payment_pb2_grpc import PaymentServiceStub
from payment_pb2 import ChargePaymentRequest, RefundPaymentRequest

_stock_stub: StockServiceStub = None
_payment_stub: PaymentServiceStub = None
_stock_channel = None
_payment_channel = None

RPC_TIMEOUT = 5.0

async def init_grpc_clients(stock_addr: str, payment_addr: str):
    global _stock_stub, _payment_stub, _stock_channel, _payment_channel
    _stock_channel = grpc.aio.insecure_channel(stock_addr)
    _payment_channel = grpc.aio.insecure_channel(payment_addr)
    _stock_stub = StockServiceStub(_stock_channel)
    _payment_stub = PaymentServiceStub(_payment_channel)

async def close_grpc_clients():
    if _stock_channel: await _stock_channel.close()
    if _payment_channel: await _payment_channel.close()

async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReserveStock(
        ReserveStockRequest(item_id=item_id, quantity=quantity, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

async def release_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReleaseStock(
        ReleaseStockRequest(item_id=item_id, quantity=quantity, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

async def charge_payment(user_id: str, amount: int, idempotency_key: str) -> dict:
    resp = await _payment_stub.ChargePayment(
        ChargePaymentRequest(user_id=user_id, amount=amount, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

async def refund_payment(user_id: str, amount: int, idempotency_key: str) -> dict:
    resp = await _payment_stub.RefundPayment(
        RefundPaymentRequest(user_id=user_id, amount=amount, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| grpc (sync, ThreadPoolExecutor) | grpc.aio (AsyncIO native) | grpcio ~1.32 (2020), stable since 1.40+ | No thread pool needed; servicer methods are `async def`; shares asyncio event loop with Quart |
| protobuf 3.x runtime | protobuf 4.x / 5.x / 6.x | 2022–2026 | Breaking changes at major versions; must match grpcio-tools bundled version |
| Manual event loop management | asyncio.run() or ASGI framework ownership | Python 3.10+ | Never call asyncio.run() inside an ASGI app; framework owns the loop |
| grpc.experimental.aio | grpc.aio (promoted) | grpcio 1.32 | `grpc.experimental.aio` was the preview namespace; use `grpc.aio` now |

**Deprecated/outdated:**
- `grpc.experimental.aio`: Replaced by `grpc.aio` in grpcio 1.32+. Never use the experimental namespace.
- `grpc.server(futures.ThreadPoolExecutor(...))`: Sync server pattern; don't use in an asyncio service — blocks the event loop on I/O.
- `purerpc`: Unmaintained (last release 2021). Not viable.

---

## Open Questions

1. **Idempotency Lua atomicity with msgpack-encoded values**
   - What we know: Stock and Payment values are stored as msgpack bytes in Redis. Pure Lua cannot decode msgpack without a loaded library. A full "check idempotency + execute stock subtract in one Lua script" is not feasible without msgpack support in Redis Lua.
   - What's unclear: The correct compromise — SET NX for idempotency slot + separate Python execution — has a narrow window where two concurrent duplicates both see `__NEW__` and both execute (the SET NX prevents this only if the key didn't exist when both called `SET NX`). In practice SET NX is atomic so only one caller gets `__NEW__`.
   - Recommendation: Use `SET ikey '__PROCESSING__' NX EX 30` via a single Redis call (not Lua). If return is `None`, key already existed — return cached. If `OK`, proceed to execute, then `SET ikey result EX 86400`. This is a clean two-step pattern without TOCTOU for concurrent duplicates.

2. **gRPC port in Docker Compose and Kubernetes**
   - What we know: Stock and Payment Dockerfiles currently EXPOSE 5000 only.
   - What's unclear: Does the docker-compose service-to-service networking require explicit port declaration for 50051 (internal), or only for host-mapped ports?
   - Recommendation: Add `EXPOSE 50051` to Dockerfiles for documentation. In docker-compose, no `ports:` mapping needed for inter-service gRPC (services resolve by name on the Docker network). Add `50051` port to k8s Service manifests when Phase 6 updates infrastructure.

3. **Orchestrator directory as a Python package vs. standalone script**
   - What we know: Phase 3 will import from `orchestrator/`. The exact import path depends on whether this is a package installed via pip or added to `PYTHONPATH`.
   - What's unclear: Docker setup for the orchestrator container (Phase 3 concern) needs to resolve imports correctly.
   - Recommendation: Add `orchestrator/__init__.py` now. Use relative imports within the package. Phase 3 will determine how the orchestrator container is built.

---

## Validation Architecture

> `workflow.nyquist_validation` is not set in `.planning/config.json` (no `nyquist_validation` key). Skipping formal Validation Architecture section — but integration test requirements are documented in Code Examples and Wave 0 Gaps below.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `pytest.ini` (Wave 0: create) |
| Quick run command | `pytest tests/test_grpc_integration.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GRPC-01 | Proto stubs importable without error | smoke | `python -c "import stock_pb2; import payment_pb2"` | Wave 0 |
| GRPC-02 | gRPC server on :50051 accepts connections | integration | `pytest tests/test_grpc_integration.py::test_grpc_server_reachable -x` | Wave 0 |
| GRPC-03 | Orchestrator client calls gRPC (no HTTP) | integration | `pytest tests/test_grpc_integration.py::test_client_reserve_stock -x` | Wave 0 |
| GRPC-04 | Duplicate idempotency_key returns same result | integration | `pytest tests/test_grpc_integration.py::test_idempotency_deduplication -x` | Wave 0 |

### Wave 0 Gaps
- [ ] `pytest.ini` — set `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = session`
- [ ] `tests/__init__.py` — make tests a package for relative imports
- [ ] `tests/test_grpc_integration.py` — covers GRPC-01 through GRPC-04
- [ ] `tests/conftest.py` — shared fixtures (gRPC server startup, Redis test DB)
- [ ] Framework install: `pip install pytest pytest-asyncio` — neither present in any service requirements.txt

---

## Sources

### Primary (HIGH confidence)
- `grpc.github.io/grpc/python/grpc_asyncio.html` (grpcio v1.78.1) — grpc.aio.server(), ServicerContext interface, channel creation, timeout per-call, lifecycle methods; verified current
- `grpc.io/docs/languages/python/quickstart/` — grpcio-tools installation, protoc command syntax, generated file names
- `pypi.org/project/grpcio/` — confirmed latest version 1.78.0 (released Feb 6, 2026)
- `pypi.org/project/grpcio-tools/` — confirmed latest version 1.78.0
- `quart.palletsprojects.com/en/latest/how_to_guides/background_tasks/` — `app.add_background_task()` pattern for Quart

### Secondary (MEDIUM confidence)
- `blog.sneawo.com/blog/2022/01/23/how-to-use-asyncio-grpc-in-aiohttp-microservices/` — dual-server pattern with aiohttp (directly analogous to Quart); `asyncio.create_task` + `grpc.aio.server()` within same event loop; cross-verified with grpc.aio docs
- `redis.io/blog/what-is-idempotency-in-redis/` — SET NX + EX atomic idempotency pattern; Lua script for multi-step logic; cross-verified with Redis Lua docs
- `github.com/grpc/grpc/issues/32480` — confirms "attached to a different loop" error when mixing asyncio.run() with grpc.aio

### Tertiary (LOW confidence)
- pytest-asyncio session-scoped loop pattern — multiple GitHub issues referenced (`pytest-dev/pytest-asyncio#957`, `#947`); exact API not verified against current pytest-asyncio docs; flag for validation during test writing

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions confirmed against live PyPI (Feb 2026)
- Architecture patterns: HIGH — grpc.aio API verified against official docs; dual-server pattern cross-verified with analogous aiohttp integration
- Idempotency: MEDIUM — Redis Lua pattern well-documented; exact Lua script for msgpack-encoded values requires project-specific adaptation (msgpack not natively decodable in Lua)
- Test infrastructure: MEDIUM — pytest-asyncio session-scope details LOW confidence; recommend verifying against current pytest-asyncio docs during Wave 0

**Research date:** 2026-02-28
**Valid until:** 2026-03-28 (grpcio is a stable library; unlikely to break within 30 days)
