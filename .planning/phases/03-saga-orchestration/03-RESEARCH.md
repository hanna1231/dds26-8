# Phase 3: SAGA Orchestration - Research

**Researched:** 2026-02-28
**Domain:** SAGA orchestration, Redis hash state machine, idempotent compensation, asyncio exponential backoff, Quart gRPC service
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Checkout response model**
- Synchronous: checkout endpoint blocks until SAGA reaches COMPLETED or FAILED
- On failure/compensation: return error with reason (e.g., "insufficient stock", "payment declined") and that compensation completed
- Response contains final outcome only — no SAGA state transitions exposed to caller
- Order service's existing /checkout HTTP endpoint stays; internally proxies to orchestrator via gRPC

**Compensation behavior**
- Compensation persists and retries on recovery — never silently dropped, never gives up
- Per-step tracking within the SAGA record (e.g., refund_done=true, stock_restored=false) to enable resuming partial compensation
- Only undo steps that actually completed — if payment was never charged, don't attempt refund
- Compensation runs in reverse order: refund payment → restore stock → mark failed

**Orchestrator boundary**
- Separate service with its own container and gRPC port
- Order service proxies /checkout to orchestrator via gRPC (external HTTP API unchanged)
- Dedicated Redis instance for the orchestrator (not shared with domain services)
- Positions well for Phase 6 Redis Cluster per-domain work

### Claude's Discretion
- Duplicate checkout handling: return original result vs 409 Conflict (pick what fits existing API patterns)
- Compensation retry backoff parameters (timing, ceiling, total budget)
- SAGA record TTL for completed/failed records
- Staleness timeout for in-progress SAGAs (timeout-based vs startup-only recovery)
- Timestamp granularity in SAGA records (per-transition vs start/end only)
- Redis key structure (single hash vs namespaced keys)
- Orchestrator gRPC API surface beyond StartCheckout (whether to include GetSagaStatus)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SAGA-01 | Every checkout creates a persistent SAGA record in Redis before any side effects | Redis HSET hash-per-saga pattern; write-before-execute ordering |
| SAGA-02 | SAGA state machine has explicit states (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPLETED, COMPENSATING, FAILED) with validated transitions | Lua CAS script for atomic state transition; valid next-state table |
| SAGA-03 | Dedicated SAGA orchestrator coordinates checkout: reserve stock → charge payment → confirm order | Separate Quart+gRPC service; orchestrator.py calls client.py wrappers |
| SAGA-04 | Orchestrator drives compensation in reverse on failure: refund payment → restore stock → mark failed | Per-step boolean flags in SAGA hash; reverse loop over completed steps |
| SAGA-05 | Compensating transactions retry with exponential backoff until success (never silently dropped) | asyncio.sleep + 2**attempt backoff; no library needed; infinite loop with cap |
| SAGA-06 | Checkout endpoint returns exactly-once semantics using order_id as idempotency key | SAGA record lookup before execution; return stored result on duplicate |
| SAGA-07 | SAGA orchestrator designed with clean interface boundary for Phase 4 extraction | Single StartCheckout RPC; proto file in protos/; stubs committed |
| IDMP-01 | Stock subtract/add operations accept idempotency key and skip re-execution if already processed | Already implemented in Phase 2 grpc_server.py; verify key format matches |
| IDMP-02 | Payment pay/refund operations accept idempotency key and skip re-execution if already processed | Already implemented in Phase 2 grpc_server.py; verify key format matches |
| IDMP-03 | Redis read-modify-write operations use Lua scripts for atomicity (prevent concurrent overselling) | Already implemented in Phase 2 IDEMPOTENCY_ACQUIRE_LUA; additional Lua needed for SAGA state transitions |
</phase_requirements>

## Summary

Phase 3 builds a dedicated SAGA orchestrator service that coordinates distributed checkout by sequencing gRPC calls to Stock and Payment, persisting all state to a Redis hash before each side effect, and running retry-until-success compensation in reverse if anything fails. The orchestrator is a new Python service (Quart + grpc.aio server) that receives a `StartCheckout` RPC from Order service, runs the SAGA, and returns the final outcome synchronously.

The critical insight is that IDMP-01, IDMP-02, and IDMP-03 are **already done**: Phase 2 implemented idempotent Stock and Payment gRPC operations with Lua-based TOCTOU prevention in `stock/grpc_server.py` and `payment/grpc_server.py`. Phase 3 builds on top of that foundation — it does not need to re-implement service-level idempotency, only orchestrator-level SAGA persistence and state machine logic.

The key new work in Phase 3 is: (1) a Redis hash per SAGA with atomic Lua state transitions, (2) a `StartCheckout` gRPC service in the orchestrator, (3) wiring Order's `/checkout` to proxy to that gRPC service, (4) an exponential-backoff compensation loop that reads per-step boolean flags from the SAGA record, and (5) Docker Compose additions for the orchestrator service and its dedicated Redis instance.

**Primary recommendation:** Use a single Redis hash per SAGA (key: `saga:{order_id}`) with string fields for state and boolean string fields for per-step completion. Use a Lua script for validated atomic state transitions. Implement compensation retry as a plain asyncio while-loop with `asyncio.sleep(min(cap, base * 2**attempt))` — no external retry library needed. Duplicate checkout (SAGA-06) should look up the SAGA record first and return the stored result if already COMPLETED or FAILED.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| grpcio | 1.78.0 | gRPC async server and client | Already in use; same version as Phase 2 |
| redis[hiredis] | 5.0.3 | Async Redis client with Lua eval | Already in use; `redis.asyncio` API |
| quart | 0.20.1 | Async HTTP framework for orchestrator | Matches existing services |
| uvicorn | 0.34.0 | ASGI server | Matches existing services |
| msgspec | 0.18.6 | Fast msgpack encoding | Already in all services |
| protobuf | >=6.31.1 | Proto stubs | Already in requirements |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio (stdlib) | 3.11+ | `asyncio.sleep()` for backoff | No external retry library needed for simple exponential backoff |
| grpcio-tools | 1.78.0 | Proto compilation | Already installed via pip3 system Python (Phase 2 lesson) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain asyncio backoff loop | tenacity library | tenacity adds a dependency; plain loop is 5 lines and fully readable |
| Redis hash (HSET) | Redis JSON / multiple keys | Hash is atomic for multi-field updates and reads; simpler key namespace |
| Lua CAS for state transitions | WATCH/MULTI/EXEC optimistic lock | Lua is cleaner, eliminates TOCTOU, consistent with Phase 2 approach |
| Quart for orchestrator HTTP | No HTTP (gRPC only) | Quart needed for health check endpoint and to follow project service template |

**Installation (orchestrator/requirements.txt additions):**
```bash
# No new dependencies — orchestrator uses same stack as domain services
quart==0.20.1
uvicorn==0.34.0
redis[hiredis]==5.0.3
msgspec==0.18.6
grpcio==1.78.0
protobuf>=6.31.1
```

## Architecture Patterns

### Recommended Project Structure

```
orchestrator/
├── app.py              # Quart HTTP shell (health endpoint, before_serving hooks)
├── saga.py             # SAGA state machine: Redis hash + Lua transitions + run_checkout()
├── grpc_server.py      # StartCheckout gRPC servicer + serve_grpc()
├── client.py           # (existing) Stock/Payment gRPC client wrappers (Phase 2)
├── orchestrator_pb2.py          # Generated stubs for orchestrator.proto
├── orchestrator_pb2_grpc.py     # Generated stubs
├── stock_pb2.py        # (existing)
├── payment_pb2.py      # (existing)
├── requirements.txt
protos/
├── stock.proto         # (existing)
├── payment.proto       # (existing)
└── orchestrator.proto  # NEW: StartCheckout RPC
order/
└── app.py              # /checkout now calls orchestrator via gRPC (replaces HTTP fan-out)
```

### Pattern 1: Redis Hash as SAGA Record

**What:** One Redis hash per SAGA, keyed by `saga:{order_id}`. All fields written atomically where needed. Hash fields cover state, per-step completion booleans, error info, and timestamps.

**When to use:** Any time you need a persistent, inspectable record of a SAGA's progress that survives pod restarts.

**SAGA hash field schema:**
```
saga:{order_id}:
  state              = STARTED | STOCK_RESERVED | PAYMENT_CHARGED | COMPLETED | COMPENSATING | FAILED
  order_id           = <string>
  user_id            = <string>
  item_id            = <string>
  quantity           = <int as string>
  total_cost         = <int as string>
  stock_reserved     = "0" | "1"   # True once ReserveStock succeeded
  payment_charged    = "0" | "1"   # True once ChargePayment succeeded
  refund_done        = "0" | "1"   # True once RefundPayment succeeded (compensation)
  stock_restored     = "0" | "1"   # True once ReleaseStock succeeded (compensation)
  error_message      = <string>    # Set on failure
  started_at         = <unix timestamp>
  updated_at         = <unix timestamp>
```

**Example (Python redis.asyncio):**
```python
# Source: redis.io/docs/latest/develop/data-types/hashes/
async def create_saga_record(db, order_id: str, user_id: str, item_id: str, quantity: int, total_cost: int):
    """Write SAGA record before any side effects (SAGA-01)."""
    key = f"saga:{order_id}"
    import time
    now = str(int(time.time()))
    await db.hset(key, mapping={
        "state": "STARTED",
        "order_id": order_id,
        "user_id": user_id,
        "item_id": item_id,
        "quantity": str(quantity),
        "total_cost": str(total_cost),
        "stock_reserved": "0",
        "payment_charged": "0",
        "refund_done": "0",
        "stock_restored": "0",
        "error_message": "",
        "started_at": now,
        "updated_at": now,
    })
    await db.expire(key, 86400 * 7)  # TTL: 7 days for completed/failed records
```

### Pattern 2: Lua Script for Validated State Transitions (SAGA-02)

**What:** Atomic CAS on the `state` field of the SAGA hash. Only transitions from expected states succeed; invalid jumps return 0.

**When to use:** Every time the orchestrator advances the SAGA state. Prevents duplicate execution from racing callers.

**Lua transition script:**
```lua
-- KEYS[1] = saga hash key (e.g., "saga:{order_id}")
-- ARGV[1] = expected current state
-- ARGV[2] = new state
-- ARGV[3] = additional field name to set (e.g., "stock_reserved"), or ""
-- ARGV[4] = additional field value (e.g., "1"), or ""
-- Returns 1 on success, 0 on state mismatch (invalid transition)
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then
  redis.call('HSET', KEYS[1], ARGV[3], ARGV[4])
end
return 1
```

**Python wrapper:**
```python
TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""

async def transition_state(db, saga_key: str, from_state: str, to_state: str,
                            flag_field: str = "", flag_value: str = "") -> bool:
    result = await db.eval(TRANSITION_LUA, 1, saga_key, from_state, to_state, flag_field, flag_value)
    return bool(result)
```

**Valid state transitions table:**
```
STARTED          → STOCK_RESERVED    (after ReserveStock succeeds)
STARTED          → COMPENSATING      (if ReserveStock fails — nothing to undo)
STOCK_RESERVED   → PAYMENT_CHARGED   (after ChargePayment succeeds)
STOCK_RESERVED   → COMPENSATING      (if ChargePayment fails)
PAYMENT_CHARGED  → COMPLETED         (after order marked paid)
PAYMENT_CHARGED  → COMPENSATING      (if order update fails — rare)
COMPENSATING     → FAILED            (after all compensation steps done)
```

### Pattern 3: Orchestrator gRPC Service (SAGA-03, SAGA-07)

**What:** A `grpc.aio.Server` in the orchestrator runs alongside Quart HTTP, started via `app.add_background_task(serve_grpc, db)` — identical to the proven Phase 2 pattern in `stock/app.py`.

**orchestrator.proto:**
```protobuf
syntax = "proto3";
package orchestrator;

service OrchestratorService {
  rpc StartCheckout(CheckoutRequest) returns (CheckoutResponse);
}

message CheckoutRequest {
  string order_id    = 1;
  string user_id     = 2;
  string item_id     = 3;
  int32  quantity    = 4;
  int32  total_cost  = 5;
}

message CheckoutResponse {
  bool   success       = 1;
  string error_message = 2;
}
```

**Startup pattern (orchestrator/app.py) — matches Phase 2 convention:**
```python
@app.before_serving
async def startup():
    global db
    db = redis.Redis(host=os.environ['REDIS_HOST'], port=int(os.environ['REDIS_PORT']),
                     password=os.environ['REDIS_PASSWORD'], db=int(os.environ['REDIS_DB']))
    await init_grpc_clients()           # Stock + Payment channels (from client.py)
    app.add_background_task(serve_grpc, db)   # Orchestrator gRPC server

@app.after_serving
async def shutdown():
    await stop_grpc_server()
    await close_grpc_clients()
    await db.aclose()
```

**Order service /checkout proxy (order/app.py):**
```python
# Replace HTTP fan-out with single gRPC call to orchestrator
async def checkout(order_id: str):
    order_entry = await get_order_from_db(order_id)
    # ...compute items_quantities, total_cost...
    resp = await orchestrator_stub.StartCheckout(CheckoutRequest(
        order_id=order_id,
        user_id=order_entry.user_id,
        item_id=item_id,         # orchestrator iterates multi-item; or flatten to single call
        quantity=quantity,
        total_cost=order_entry.total_cost,
    ))
    if not resp.success:
        abort(400, resp.error_message)
    order_entry.paid = True
    await db.set(order_id, msgpack.encode(order_entry))
    return Response("Checkout successful", status=200)
```

> **Multi-item note:** The current Order model stores `items: list[tuple[str, int]]` — multiple (item_id, quantity) pairs. The CheckoutRequest proto and run_checkout() will need to handle a list of items. Consider a repeated `LineItem` message in the proto, or have Order service aggregate total_cost and pass a single-reserve call per item. Design choice for planner — see Open Questions.

### Pattern 4: Compensation with Per-Step Flags (SAGA-04)

**What:** Compensation runs in reverse. The SAGA record has boolean flags (`stock_reserved`, `payment_charged`) that tell compensation exactly which steps to undo. Compensation only runs `ReleaseStock` if `stock_reserved == "1"` and only `RefundPayment` if `payment_charged == "1"`.

**Example:**
```python
async def run_compensation(db, saga: dict):
    """Undo completed steps in reverse. Called when SAGA enters COMPENSATING state."""
    saga_key = f"saga:{saga['order_id']}"

    # Step 1: Refund payment (only if charged)
    if saga.get("payment_charged") == "1" and saga.get("refund_done") != "1":
        ikey = f"saga:{saga['order_id']}:step:refund"
        await retry_forever(lambda: refund_payment(saga['user_id'], int(saga['total_cost']), ikey))
        await db.hset(saga_key, mapping={"refund_done": "1", "updated_at": str(int(time.time()))})

    # Step 2: Restore stock (only if reserved)
    if saga.get("stock_reserved") == "1" and saga.get("stock_restored") != "1":
        ikey = f"saga:{saga['order_id']}:step:release"
        await retry_forever(lambda: release_stock(saga['item_id'], int(saga['quantity']), ikey))
        await db.hset(saga_key, mapping={"stock_restored": "1", "updated_at": str(int(time.time()))})

    # Mark FAILED — transition from COMPENSATING
    await transition_state(db, saga_key, "COMPENSATING", "FAILED")
```

### Pattern 5: Exponential Backoff Retry (SAGA-05)

**What:** Plain asyncio loop, no library. `retry_forever()` retries a callable until it returns `{"success": True}`, sleeping `min(cap, base * 2**attempt)` between attempts.

**When to use:** Compensation steps only. Forward steps fail fast (business error → trigger compensation). Compensation must never give up.

**Implementation:**
```python
import asyncio
import logging

BASE_BACKOFF = 0.5   # seconds (Claude's discretion)
CAP_BACKOFF  = 30.0  # seconds ceiling

async def retry_forever(fn, base: float = BASE_BACKOFF, cap: float = CAP_BACKOFF):
    """Call async fn() until it returns success=True. Exponential backoff, no give-up."""
    attempt = 0
    while True:
        try:
            result = await fn()
            if result.get("success"):
                return result
            # Business error (e.g., "operation in progress, retry") — keep retrying
        except Exception as exc:
            logging.warning("compensation retry attempt %d failed: %s", attempt, exc)
        delay = min(cap, base * (2 ** attempt))
        await asyncio.sleep(delay)
        attempt += 1
```

**Note on idempotency during retry:** Compensation steps use fixed idempotency keys (e.g., `saga:{order_id}:step:refund`). The domain services (Phase 2) return the cached result transparently on duplicate keys, so retrying the same compensation step is safe — it will either succeed or return the already-cached result.

### Pattern 6: Exactly-Once Checkout / Duplicate Detection (SAGA-06)

**What:** Before creating a SAGA record, check if one already exists for this `order_id`. If it exists and is COMPLETED or FAILED, return the stored result immediately. If it is STARTED/in-progress, either wait or return 409.

**Recommendation (Claude's discretion):** Return 200 with original success result for COMPLETED (idempotent success), return 400 with original error for FAILED (idempotent failure). Return 409 for in-progress — aligns with HTTP conventions for concurrent duplicate requests.

```python
async def get_saga(db, order_id: str) -> dict | None:
    key = f"saga:{order_id}"
    data = await db.hgetall(key)
    if not data:
        return None
    # redis.asyncio returns bytes keys/values
    return {k.decode(): v.decode() for k, v in data.items()}

async def run_checkout(db, order_id, user_id, item_id, quantity, total_cost) -> dict:
    existing = await get_saga(db, order_id)
    if existing:
        state = existing.get("state")
        if state == "COMPLETED":
            return {"success": True, "error_message": ""}
        if state == "FAILED":
            return {"success": False, "error_message": existing.get("error_message", "checkout failed")}
        # In-progress: return conflict
        return {"success": False, "error_message": "checkout already in progress"}

    # Create SAGA record first (SAGA-01), then execute
    await create_saga_record(db, order_id, user_id, item_id, quantity, total_cost)
    return await _execute_saga(db, order_id, ...)
```

### Anti-Patterns to Avoid

- **Write after execute:** Never call a gRPC service before writing the SAGA state that records the intent. The record must exist before the side effect, so recovery can detect incomplete work.
- **Silent compensation failure:** Never `except: pass` in a compensation loop. Log and retry; every failure must be observable.
- **Shared Redis between orchestrator and domain services:** Keep orchestrator Redis dedicated. Mixing namespaces makes Phase 6 Redis Cluster per-domain work harder and risks key collision.
- **State transition without Lua:** Using Python-level read-then-write for state transitions allows TOCTOU bugs under concurrent duplicate requests. Always use the Lua CAS script.
- **Using gRPC status codes for business errors:** Phase 2 established that business errors go in `success`/`error_message` fields. The orchestrator must inspect `result["success"]`, not catch `grpc.aio.AioRpcError`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exponential backoff | Custom backoff class with state | Plain `asyncio.sleep(min(cap, base * 2**n))` | 3 lines; tenacity is overkill for a single use case in this codebase |
| State transition atomicity | Python-level WATCH/MULTI/EXEC | Lua CAS script (established pattern from Phase 2) | Consistent with IDEMPOTENCY_ACQUIRE_LUA already in stock/grpc_server.py |
| Proto stub generation | Runtime codegen | Compile once with `python3 -m grpc_tools.protoc`, commit stubs | Phase 2 established: stubs committed to repo, no runtime codegen |
| Service lifecycle | asyncio.create_task manually | `app.add_background_task(serve_grpc, db)` | Phase 2 proven pattern in stock/app.py |

**Key insight:** The hardest parts of idempotency and Lua atomicity are already solved at the domain service layer (Phase 2). Phase 3 only needs to solve orchestrator-level SAGA persistence and state sequencing.

## Common Pitfalls

### Pitfall 1: SAGA Record Created After First gRPC Call
**What goes wrong:** Orchestrator calls `reserve_stock` successfully, then crashes before writing any SAGA state. On recovery, the system has no record of the SAGA and cannot compensate — stock is permanently reserved.
**Why it happens:** Developers write state "on success" rather than "on intent."
**How to avoid:** `create_saga_record()` MUST be called before any gRPC call. State is `STARTED` at creation time; `stock_reserved` advances only after the call returns success.
**Warning signs:** Any code path that calls `reserve_stock` before `db.hset(saga_key, ...)`.

### Pitfall 2: Compensation Retrying Already-Compensated Steps
**What goes wrong:** Recovery re-runs compensation from the beginning even though `refund_done=1` and `stock_restored=0`, causing a double refund.
**Why it happens:** Recovery reads stale state or doesn't check per-step flags.
**How to avoid:** Always re-read the SAGA hash at the start of compensation to get current flags. Each step checks its own `refund_done` / `stock_restored` flag before executing.
**Warning signs:** Compensation loop that starts from step 1 without checking flags.

### Pitfall 3: In-Progress SAGA Lock
**What goes wrong:** Two concurrent checkout requests for the same `order_id` both see no existing SAGA, both create records, and both execute — resulting in double charge.
**Why it happens:** `HSETNX` (set if not exists) not used on SAGA creation, or creation and check are not atomic.
**How to avoid:** Use `HSETNX saga:{order_id} state STARTED` for the initial creation. If it returns 0 (key already exists), another process beat you — read the existing record and return its result.
**Warning signs:** `hset` used for initial SAGA creation instead of `hsetnx`.

### Pitfall 4: redis.asyncio Returns Bytes from HGETALL
**What goes wrong:** `saga["state"] == "STARTED"` is always False because `hgetall` returns `{b"state": b"STARTED"}`.
**Why it happens:** redis.asyncio does not auto-decode keys/values unless `decode_responses=True` is set on the Redis client.
**How to avoid:** Either construct the Redis client with `decode_responses=True`, or decode all HGETALL results: `{k.decode(): v.decode() for k, v in data.items()}`. Be consistent — pick one approach.
**Warning signs:** State comparisons that always fail; compensations that always run.

### Pitfall 5: Multi-Item Order Collapse
**What goes wrong:** The current `order/app.py` has `items: list[tuple[str, int]]` — multiple items per order. If the CheckoutRequest only accepts a single item_id+quantity, the orchestrator silently ignores other items.
**Why it happens:** Proto message designed for single-item without accounting for Order data model.
**How to avoid:** Use a `repeated LineItem` in CheckoutRequest, or have Order aggregate multi-item into a single synthetic reserve per item and make one RPC per item. Clarify in plan 03-02.
**Warning signs:** Benchmark failures where only partial stock is reserved.

### Pitfall 6: Payment gRPC Port Already Used by Stock
**What goes wrong:** Both Stock and Payment use port 50051 in their individual service; in tests this causes a bind failure.
**Why it happens:** Copied grpc_server.py without changing port.
**How to avoid:** Stock is on 50051; Payment is on 50052 (established in Phase 2 conftest). Orchestrator's own gRPC server should use 50053.
**Warning signs:** `OSError: [Errno 98] Address already in use` in tests.

## Code Examples

Verified patterns from official sources and project codebase:

### HSETNX for Atomic SAGA Creation (prevents duplicate SAGAs)
```python
# Source: redis.io/commands/hset — HSETNX returns 1 if field was set, 0 if field already existed
# Use SET with NX flag on a sentinel field to get "create if not exists" for hashes:
async def create_saga_record_atomic(db, order_id: str, **fields) -> bool:
    """Returns True if SAGA was created (new), False if already exists."""
    key = f"saga:{order_id}"
    # HSETNX on the 'state' field: set only if key doesn't exist
    created = await db.hsetnx(key, "state", "STARTED")
    if created:
        # Set remaining fields
        await db.hset(key, mapping={**fields, "state": "STARTED"})
        await db.expire(key, 86400 * 7)
    return bool(created)
```

> **Alternative:** Use `SET saga:{order_id}:lock NX EX 60` as a distributed lock guard around SAGA creation if HSETNX on a single field isn't sufficient (HSETNX sets one field; if the hash exists with other fields, behavior differs). Verify this in implementation.

### Full SAGA Execution Skeleton
```python
async def _execute_saga(db, order_id: str, user_id: str, item_id: str, quantity: int, total_cost: int) -> dict:
    saga_key = f"saga:{order_id}"

    # --- Forward: Reserve Stock ---
    ikey_reserve = f"saga:{order_id}:step:reserve"
    result = await reserve_stock(item_id, quantity, ikey_reserve)
    if not result["success"]:
        await db.hset(saga_key, mapping={"state": "COMPENSATING", "error_message": result["error_message"]})
        await run_compensation(db, await get_saga(db, order_id))
        return {"success": False, "error_message": result["error_message"]}

    ok = await transition_state(db, saga_key, "STARTED", "STOCK_RESERVED", "stock_reserved", "1")
    if not ok:
        # Concurrent duplicate won the race — idempotent: read and return existing result
        existing = await get_saga(db, order_id)
        return {"success": existing["state"] == "COMPLETED", "error_message": existing.get("error_message", "")}

    # --- Forward: Charge Payment ---
    ikey_charge = f"saga:{order_id}:step:charge"
    result = await charge_payment(user_id, total_cost, ikey_charge)
    if not result["success"]:
        await transition_state(db, saga_key, "STOCK_RESERVED", "COMPENSATING")
        await db.hset(saga_key, "error_message", result["error_message"])
        await run_compensation(db, await get_saga(db, order_id))
        return {"success": False, "error_message": result["error_message"]}

    await transition_state(db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED", "payment_charged", "1")

    # --- Complete ---
    await transition_state(db, saga_key, "PAYMENT_CHARGED", "COMPLETED")
    return {"success": True, "error_message": ""}
```

### hgetall Decode Pattern
```python
# Source: redis-py docs — asyncio client returns bytes by default
async def get_saga(db, order_id: str) -> dict | None:
    key = f"saga:{order_id}"
    raw = await db.hgetall(key)
    if not raw:
        return None
    return {k.decode(): v.decode() for k, v in raw.items()}
```

### Order Service gRPC Proxy (order/app.py changes)
```python
# New import section in order/app.py
import grpc.aio
from orchestrator_pb2 import CheckoutRequest
from orchestrator_pb2_grpc import OrchestratorServiceStub

_orchestrator_channel = None
_orchestrator_stub: OrchestratorServiceStub = None
ORCHESTRATOR_ADDR = os.environ.get("ORCHESTRATOR_GRPC_ADDR", "orchestrator-service:50053")

@app.before_serving
async def startup():
    global db, http_client, _orchestrator_channel, _orchestrator_stub
    # ... existing Redis + httpx init ...
    _orchestrator_channel = grpc.aio.insecure_channel(ORCHESTRATOR_ADDR)
    _orchestrator_stub = OrchestratorServiceStub(_orchestrator_channel)

@app.post('/checkout/<order_id>')
async def checkout(order_id: str):
    order_entry = await get_order_from_db(order_id)
    # Aggregate items
    items_quantities: dict[str, int] = defaultdict(int)
    for item_id, quantity in order_entry.items:
        items_quantities[item_id] += quantity

    # One RPC per item (or repeated LineItem — see Open Questions)
    for item_id, quantity in items_quantities.items():
        resp = await _orchestrator_stub.StartCheckout(CheckoutRequest(
            order_id=order_id,
            user_id=order_entry.user_id,
            item_id=item_id,
            quantity=quantity,
            total_cost=order_entry.total_cost,
        ), timeout=60.0)  # longer timeout — SAGA is synchronous
        if not resp.success:
            abort(400, resp.error_message)

    order_entry.paid = True
    await db.set(order_id, msgpack.encode(order_entry))
    return Response("Checkout successful", status=200)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| HTTP fan-out from Order (rollback_stock on failure) | gRPC → SAGA orchestrator (Redis-persisted state) | Phase 3 | Crash-safe; rollback survives pod kill |
| In-memory rollback list (removed_items) | Per-step boolean flags in Redis hash | Phase 3 | Compensation can resume after crash |
| No idempotency on Stock/Payment HTTP | Lua-based idempotency in gRPC servers (Phase 2) | Phase 2 done | Retry-safe domain operations |
| Single Redis instance shared by all | Dedicated Redis per service domain | Phase 3+ | Clean isolation; Phase 6 Redis Cluster ready |

**Deprecated/outdated in this project:**
- `rollback_stock()` in `order/app.py`: Replaced by SAGA compensation. Remove in plan 03-02.
- HTTP calls from Order to Stock/Payment for checkout: Replaced by single gRPC call to orchestrator.

## Open Questions

1. **Multi-item order handling in the proto**
   - What we know: `OrderValue.items` is `list[tuple[str, int]]` — multiple items per order. The orchestrator must reserve stock for each item.
   - What's unclear: Should CheckoutRequest have `repeated LineItem items = ...`? Or should Order pass one item at a time and orchestrator be called once per item? Or does orchestrator get total_cost and a list?
   - Recommendation: Use `repeated LineItem` (item_id + quantity) in CheckoutRequest so the orchestrator owns the full SAGA including multi-item stock reservation. This avoids Order needing to know about SAGA step ordering. Plan 03-02 should finalize this and update the proto.

2. **HSETNX vs SET NX for SAGA creation atomicity**
   - What we know: `HSETNX` sets a single hash field only if it doesn't exist. `SET saga:{id}:lock NX EX 60` is a standard distributed lock.
   - What's unclear: If the hash key already exists (from a previous partial creation), HSETNX on `state` returns 0 but the hash has stale fields. Does this create a ghost SAGA?
   - Recommendation: Use `SET saga:{order_id} NX EX 3600` as a creation lock, then `HSET` all fields if the SET returned OK. Simpler and avoids partial-hash edge case. Planner should decide in 03-01.

3. **Staleness timeout for in-progress SAGAs**
   - What we know: CONTEXT.md defers this to Claude's discretion. Phase 4 handles crash recovery (FAULT-02). Phase 3 only needs to decide if in-progress SAGAs block duplicate requests.
   - What's unclear: If the orchestrator pod crashes mid-SAGA, the record stays in STARTED/COMPENSATING forever with no Phase 4 recovery yet. A duplicate checkout for the same order_id would be blocked.
   - Recommendation: For Phase 3, treat any in-progress SAGA as blocking and return 409. Phase 4 will add the startup scan that resolves stale SAGAs. Document this limitation in 03-02 plan.

4. **Order service proto stubs location**
   - What we know: Orchestrator stubs go in `orchestrator/`. Order service needs `orchestrator_pb2.py` to call StartCheckout.
   - What's unclear: Should `orchestrator_pb2.py` be copied into `order/` (like stock/payment do with their domain stubs) or imported from a shared location?
   - Recommendation: Copy generated stubs into `order/` — consistent with Phase 2 pattern where stock stubs are in both `stock/` and `orchestrator/`. Plan 03-02 handles this.

## Validation Architecture

> `workflow.nyquist_validation` is not set in `.planning/config.json` — this section is skipped per instructions.

## Sources

### Primary (HIGH confidence)
- redis.io/docs/latest/develop/data-types/hashes/ — HSET, HGETALL, HSETNX, HINCRBY commands verified
- redis.io/docs/latest/develop/programmability/lua-api/ — Lua CAS pattern, atomic hash updates, key limitations
- Codebase: `stock/grpc_server.py`, `payment/grpc_server.py` — Phase 2 idempotency Lua script (IDEMPOTENCY_ACQUIRE_LUA)
- Codebase: `orchestrator/client.py` — existing gRPC wrapper functions ready to use
- Codebase: `stock/app.py` — `app.add_background_task(serve_grpc, db)` startup pattern
- Codebase: `tests/conftest.py` — session-scoped fixture and asyncio config patterns
- microsoft azure architecture center — Saga Design Pattern (updated 2025-12-09): compensation concepts, pivot transactions, countermeasures
- microservices.io/patterns/data/saga.html — state machine model, orchestration vs choreography

### Secondary (MEDIUM confidence)
- redis.readthedocs.io/en/stable/backoff.html — ExponentialWithJitterBackoff exists in redis-py; plain asyncio sleep preferred for compensation loop
- grpc.github.io/grpc/python/grpc_asyncio.html — `grpc.aio.server()` API verified at Phase 2 implementation

### Tertiary (LOW confidence)
- WebSearch: Python asyncio exponential backoff patterns — confirmed plain loop pattern is standard; no single authoritative source

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in project, no new dependencies
- Architecture: HIGH — SAGA hash pattern verified against Redis docs; Lua CAS verified against official Lua API docs; startup pattern verified from Phase 2 codebase
- Pitfalls: HIGH — bytes-vs-str Redis pitfall verified from codebase patterns; state transition race condition is well-documented; multi-item gap confirmed from order/app.py data model inspection
- Open questions: MEDIUM — multi-item proto design is a real decision gap; HSETNX vs SET NX edge case is genuine ambiguity

**Research date:** 2026-02-28
**Valid until:** 2026-03-28 (stable domain — Redis and grpcio APIs are stable)
