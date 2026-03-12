# Architecture Patterns

**Domain:** 2PC and Redis Streams request/reply messaging for distributed checkout system
**Researched:** 2026-03-12
**Context:** v2.0 milestone -- adding 2PC as alternative transaction pattern and Redis Streams message queues as default inter-service communication to existing SAGA+gRPC system

---

## Current Architecture (v1.0 Baseline)

```
Client -> Nginx -> Order Service --(gRPC)--> Orchestrator --(gRPC)--> Stock Service
                                                          --(gRPC)--> Payment Service

Orchestrator --(Redis Streams)--> {saga:events}:checkout  (fire-and-forget audit/compensation events)
All services --(Redis Cluster)--> per-domain Redis (data storage + SAGA state)
```

**Key properties of v1.0 that constrain v2.0 design:**

| Property | Detail | Implication for v2.0 |
|----------|--------|---------------------|
| Order->Orchestrator is gRPC `StartCheckout` | Synchronous request/response | This entry point does NOT change -- only internal orchestrator logic changes |
| SAGA state in Redis hash `{saga:<order_id>}` | Lua CAS transitions | 2PC needs analogous `{2pc:<order_id>}` hash with its own state machine |
| Idempotency keys on all mutation RPCs | Stored in Redis with TTL | 2PC reuses same idempotency mechanism |
| `client.py` wraps gRPC stubs with circuit breakers | `reserve_stock()`, `charge_payment()`, etc. | Queue client must expose identical function signatures |
| Redis Streams used ONLY for fire-and-forget events | NOT for inter-service commands | v2.0 adds a second, separate stream topology for request/reply commands |
| Compensation retries with `retry_forever` | Exponential backoff, never gives up | 2PC commit/abort retries use identical pattern |
| Recovery scanner on startup | Scans for stale non-terminal SAGAs | 2PC needs its own recovery scanner for stale non-terminal 2PC records |
| Orchestrator single replica | Avoids split-brain | Same constraint applies to 2PC coordinator and queue reply consumer |

---

## Recommended Architecture for v2.0

### Two Orthogonal Configuration Axes

1. **Transaction pattern** -- `TRANSACTION_PATTERN=saga|2pc` (env var)
2. **Communication mode** -- `COMM_MODE=grpc|queue` (env var)

These are ORTHOGONAL. Four valid combinations exist:

| Combination | Status |
|------------|--------|
| `saga + grpc` | Current v1.0 (unchanged) |
| `saga + queue` | NEW: SAGA over message queues |
| `2pc + grpc` | NEW: 2PC over gRPC |
| `2pc + queue` | NEW: 2PC over message queues |

### Component Boundaries

| Component | Responsibility | What Changes in v2.0 |
|-----------|---------------|---------------------|
| **Order Service** | Order CRUD, checkout entry point | NO CHANGE -- calls orchestrator gRPC `StartCheckout`; orchestrator's internal pattern is transparent |
| **Orchestrator** | Transaction coordination (SAGA or 2PC) | NEW: 2PC coordinator, queue client adapter, env var routing, reply consumer |
| **Stock Service** | Stock CRUD, reserve/release operations | NEW: queue consumer loop (when COMM_MODE=queue), 2PC participant operations (prepare/commit/abort) |
| **Payment Service** | Payment CRUD, charge/refund operations | NEW: queue consumer loop (when COMM_MODE=queue), 2PC participant operations (prepare/commit/abort) |
| **Queue Layer** (new logical component) | Request/reply over Redis Streams | NEW: shared protocol for request routing and correlation |

### High-Level v2.0 Diagram

```
                                 ENV: TRANSACTION_PATTERN = saga | 2pc
                                 ENV: COMM_MODE = queue | grpc

Client -> Nginx -> Order Service --(gRPC StartCheckout)--> Orchestrator
                                                               |
                                                       ┌───────┴───────┐
                                                       |               |
                                                 SAGA module     2PC module
                                                       |               |
                                                       └───────┬───────┘
                                                               |
                                                       transport.py
                                                               |
                                                       ┌───────┴───────┐
                                                       |               |
                                                 client.py       queue_client.py
                                                 (gRPC stubs)    (Redis Streams
                                                       |          request/reply)
                                                       |               |
                                                       └───────┬───────┘
                                                               |
                                                       Stock / Payment
                                                       (gRPC server OR
                                                        queue consumer)
```

---

## New Component 1: 2PC Coordinator

### 2PC vs SAGA: Fundamental Difference

SAGA executes steps **sequentially** and **compensates** on failure (undo completed work).
2PC asks all participants to **prepare concurrently**, then makes a single **commit-or-abort decision**.

SAGA is eventually consistent during compensation. 2PC provides atomic commit -- either all participants commit or all abort. In Redis (which has no native XA support), "prepare" actually performs the mutation optimistically, and "abort" undoes it -- structurally similar to SAGA compensation but with a different timing model.

### 2PC State Machine

```
                              2PC State Machine

     ┌──────────┐  all vote YES  ┌────────────┐  all ack   ┌───────────┐
     │ PREPARING │──────────────>│ COMMITTING │───────────>│ COMMITTED │
     └──────────┘               └────────────┘            └───────────┘
          │
          │ any vote NO or timeout
          ▼
     ┌──────────┐  all ack   ┌──────────┐
     │ ABORTING │───────────>│ ABORTED  │
     └──────────┘            └──────────┘
```

**Valid transitions (for Lua CAS):**

```python
TPC_VALID_TRANSITIONS = {
    "PREPARING":  {"COMMITTING", "ABORTING"},
    "COMMITTING": {"COMMITTED"},
    "ABORTING":   {"ABORTED"},
}
```

### 2PC Redis State Record

```
Redis Hash: {2pc:<order_id>}
Fields:
  state:            PREPARING | COMMITTING | COMMITTED | ABORTING | ABORTED
  order_id:         string
  user_id:          string
  total_cost:       string (int)
  items_json:       string (JSON array)
  stock_vote:       pending | yes | no
  payment_vote:     pending | yes | no
  decision:         none | commit | abort
  error_message:    string
  started_at:       unix timestamp
  updated_at:       unix timestamp
```

### 2PC Execution Flow

```
1. Create 2PC record (PREPARING state, both votes "pending")
2. Send Prepare to Stock + Payment CONCURRENTLY (asyncio.gather)
3. Collect votes:
   - Both YES  -> set decision="commit", transition PREPARING -> COMMITTING
   - Any NO    -> set decision="abort",  transition PREPARING -> ABORTING
   - Timeout   -> set decision="abort",  transition PREPARING -> ABORTING
4a. COMMITTING: Send Commit to both (retry-forever), then -> COMMITTED
4b. ABORTING:   Send Abort to both (retry-forever), then -> ABORTED
5. Return success (COMMITTED) or failure (ABORTED) to caller
```

### 2PC vs SAGA: Implementation Mapping

| SAGA Concept | 2PC Equivalent | Code Reuse |
|-------------|----------------|------------|
| `saga.py` (create_saga_record, transition_state, get_saga) | `tpc.py` (create_2pc_record, transition_state, get_2pc) | Reuse TRANSITION_LUA verbatim; different state constants |
| `run_checkout` in grpc_server.py | `run_checkout_2pc` in tpc_coordinator.py | Different flow but same retry patterns |
| Sequential: reserve stock THEN charge payment | Concurrent: prepare stock AND payment via asyncio.gather | Structural change |
| `run_compensation` (retry-forever reverse order) | `run_commit` or `run_abort` (retry-forever to all) | Same retry_forever function |
| `recovery.py` (resume stale SAGAs) | `tpc_recovery.py` (resume stale 2PCs) | Same scan pattern, different state machine |
| Idempotency keys `{saga:<oid>}:step:reserve:<iid>` | Idempotency keys `{2pc:<oid>}:step:prepare_stock` | Same mechanism, different prefix |

### 2PC Participant Operations

**Critical insight:** Redis has no native prepare/commit split. In this system, "prepare" performs the actual mutation (same as current ReserveStock/ChargePayment). "Commit" is an acknowledgment (no-op on data). "Abort" undoes the mutation (same as current ReleaseStock/RefundPayment).

**Stock Service needs:**
- `PrepareReserve(tx_id, items[])` -- reserve ALL items atomically, vote YES/NO
- `CommitReserve(tx_id)` -- acknowledge commit (data already mutated in prepare)
- `AbortReserve(tx_id)` -- release all reserved stock (equivalent to ReleaseStock per item)

**Payment Service needs:**
- `PrepareCharge(tx_id, user_id, amount)` -- deduct credit, vote YES/NO
- `CommitCharge(tx_id)` -- acknowledge commit (data already mutated in prepare)
- `AbortCharge(tx_id)` -- refund credit (equivalent to RefundPayment)

**Key design decision: 2PC prepares ALL items in one call.** Unlike SAGA which reserves item-by-item, 2PC's prepare must be atomic across all items -- either all can be reserved or none. This requires a new multi-item Lua script for Stock.

### Proto Additions

```protobuf
// stock.proto additions
rpc PrepareReserve(PrepareReserveRequest) returns (VoteResponse);
rpc CommitReserve(TxRequest) returns (AckResponse);
rpc AbortReserve(TxRequest) returns (AckResponse);

message PrepareReserveRequest {
  string tx_id = 1;
  repeated ReserveStockRequest items = 2;  // reuse existing message type
}

message VoteResponse {
  bool vote_yes = 1;
  string error_message = 2;
}

message TxRequest {
  string tx_id = 1;
}

message AckResponse {
  bool acknowledged = 1;
  string error_message = 2;
}
```

```protobuf
// payment.proto additions
rpc PrepareCharge(PrepareChargeRequest) returns (VoteResponse);
rpc CommitCharge(TxRequest) returns (AckResponse);
rpc AbortCharge(TxRequest) returns (AckResponse);

message PrepareChargeRequest {
  string tx_id = 1;
  string user_id = 2;
  int32 amount = 3;
}
```

---

## New Component 2: Redis Streams Request/Reply

### Why a New Stream Topology (Not Reusing Existing)

The existing Redis Streams (`{saga:events}:checkout`) are fire-and-forget event streams for observability. The new queue system is fundamentally different: it carries commands that require responses. These MUST be separate streams to avoid:
- Mixing commands with audit events
- Different delivery guarantees (at-most-once events vs. exactly-once commands)
- Different consumer group semantics

### Request/Reply Protocol

**Request streams** (one per target service):
- `{queue}:stock:requests` -- commands destined for Stock Service
- `{queue}:payment:requests` -- commands destined for Payment Service

**Reply stream** (one for orchestrator):
- `{queue}:orchestrator:replies` -- all replies flow back here

**Request message format** (XADD fields):
```
correlation_id:  UUID (links reply to request)
command:         "ReserveStock" | "ReleaseStock" | "PrepareReserve" | "CommitReserve" | "AbortReserve" | etc.
payload:         JSON-encoded request body
reply_to:        "{queue}:orchestrator:replies"
timestamp:       unix epoch
```

**Reply message format** (XADD fields):
```
correlation_id:  UUID (matches request)
success:         "true" | "false"
payload:         JSON-encoded response body (for commands that return data)
error_message:   string
timestamp:       unix epoch
```

### Request/Reply Data Flow

```
Orchestrator                    Redis Streams                    Stock Service
     |                               |                               |
     |  1. Register Future for       |                               |
     |     correlation_id in         |                               |
     |     pending_requests map      |                               |
     |                               |                               |
     |  2. XADD {queue}:stock:      |                               |
     |     requests {correlation_id, |                               |
     |     command, payload}         |                               |
     |------------------------------>|                               |
     |                               |  3. XREADGROUP (consumer      |
     |                               |     group: stock-workers)     |
     |                               |<------------------------------|
     |                               |                               |
     |                               |     4. Dispatch to business   |
     |                               |        logic based on command |
     |                               |                               |
     |                               |  5. XADD {queue}:orchestrator:|
     |                               |     replies {correlation_id,  |
     |                               |     success, payload}         |
     |                               |<------------------------------|
     |                               |                               |
     |  6. Reply consumer reads      |                               |
     |     from replies stream,      |                               |
     |     resolves Future by        |                               |
     |     correlation_id            |                               |
     |<------------------------------|                               |
     |                               |                               |
     |  7. Caller awaits Future,     |                               |
     |     gets result               |                               |
```

### Correlation and Waiting: Future-Based Pattern

The orchestrator must block waiting for a specific reply without busy-polling.

**Chosen approach: asyncio.Future map**

```python
# Conceptual flow in queue_client.py:

pending_requests: dict[str, asyncio.Future] = {}

async def send_and_wait(target_service, command, payload, timeout=5.0):
    correlation_id = str(uuid.uuid4())
    future = asyncio.get_event_loop().create_future()
    pending_requests[correlation_id] = future

    await db.xadd(f"{{queue}}:{target_service}:requests", {
        "correlation_id": correlation_id,
        "command": command,
        "payload": json.dumps(payload),
        "reply_to": "{queue}:orchestrator:replies",
        "timestamp": str(int(time.time())),
    })

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        pending_requests.pop(correlation_id, None)
        return {"success": False, "error_message": "queue reply timeout"}
```

**Background reply consumer** runs as a Quart background task, reads from `{queue}:orchestrator:replies`, resolves Futures by correlation_id.

**Why this over per-request reply streams:** Creating/deleting thousands of streams under load doesn't scale. Redis Cluster key-distribution overhead per stream. A single reply stream with a pending map is clean and efficient.

### Consumer Groups for Load Balancing

| Stream | Consumer Group | Consumers |
|--------|---------------|-----------|
| `{queue}:stock:requests` | `stock-workers` | Each Stock replica joins same group |
| `{queue}:payment:requests` | `payment-workers` | Each Payment replica joins same group |
| `{queue}:orchestrator:replies` | `orch-replies` | Single orchestrator consumer |

**Advantage over gRPC:** When Stock scales to N replicas via HPA, each replica joins the `stock-workers` consumer group. Redis automatically load-balances requests across replicas. With gRPC, load balancing requires client-side logic or a service mesh.

### Stream Key Hash Tags

All queue streams share the `{queue}` hash tag:
- `{queue}:stock:requests`
- `{queue}:payment:requests`
- `{queue}:orchestrator:replies`

This forces all queue keys to the same Redis Cluster hash slot (single node). At the 20-CPU benchmark scale, this is acceptable. Queue throughput is not the bottleneck -- business logic execution is.

### Stale Message Handling

Use XAUTOCLAIM to reclaim messages from crashed consumers (same pattern as existing compensation consumer in `consumers.py`). If a Stock replica crashes mid-processing, another replica in the same consumer group can claim the idle message.

Queue consumers must ACK messages only after sending the reply. If a consumer crashes between processing and ACK, the message will be reclaimed and reprocessed. The idempotency layer on business logic functions ensures no double-execution.

---

## Architecture Refactoring: Service Internals

### Stock Service (before and after)

**Before (v1.0):**
```
stock/
  app.py          -- Quart HTTP routes + startup
  grpc_server.py  -- gRPC servicer with embedded Lua business logic
```

**After (v2.0):**
```
stock/
  app.py              -- Modified: conditionally starts queue consumer based on COMM_MODE
  grpc_server.py      -- Modified: delegates to operations.py instead of inline Lua
  operations.py       -- NEW: extracted business logic (reserve, release, check + 2PC prepare/commit/abort)
  queue_consumer.py   -- NEW: Redis Streams consumer, dispatches commands to operations.py
```

### Payment Service (same pattern)

**Before (v1.0):**
```
payment/
  app.py          -- Quart HTTP routes + startup
  grpc_server.py  -- gRPC servicer with embedded Lua business logic
```

**After (v2.0):**
```
payment/
  app.py              -- Modified: conditionally starts queue consumer based on COMM_MODE
  grpc_server.py      -- Modified: delegates to operations.py
  operations.py       -- NEW: extracted business logic (charge, refund, check + 2PC prepare/commit/abort)
  queue_consumer.py   -- NEW: Redis Streams consumer, dispatches commands to operations.py
```

### Orchestrator (before and after)

**Before (v1.0):**
```
orchestrator/
  app.py          -- Quart startup
  saga.py         -- SAGA state machine
  grpc_server.py  -- gRPC servicer + run_checkout + run_compensation
  client.py       -- gRPC client stubs (circuit breaker wrapped)
  circuit.py      -- Circuit breaker instances
  events.py       -- Fire-and-forget event publishing
  consumers.py    -- Event stream consumers (compensation-handler, audit-logger)
  recovery.py     -- Startup SAGA recovery scanner
```

**After (v2.0):**
```
orchestrator/
  app.py              -- Modified: startup reads env vars, selects pattern + comm mode
  saga.py             -- UNCHANGED
  tpc.py              -- NEW: 2PC state machine (create, transition, get)
  grpc_server.py      -- Modified: delegates to saga_coordinator or tpc_coordinator
  saga_coordinator.py -- NEW: extracted SAGA run_checkout + run_compensation from grpc_server.py
  tpc_coordinator.py  -- NEW: 2PC coordinator (run_checkout_2pc, run_commit, run_abort)
  client.py           -- UNCHANGED: gRPC client stubs
  queue_client.py     -- NEW: Redis Streams request/reply client (send_and_wait)
  transport.py        -- NEW: selects client.py or queue_client.py based on COMM_MODE
  reply_consumer.py   -- NEW: background consumer for reply stream, resolves Futures
  circuit.py          -- UNCHANGED (only active when COMM_MODE=grpc)
  events.py           -- UNCHANGED
  consumers.py        -- UNCHANGED
  recovery.py         -- Modified: dispatches to SAGA or 2PC recovery based on TRANSACTION_PATTERN
  tpc_recovery.py     -- NEW: 2PC recovery scanner for stale transactions
```

---

## Patterns to Follow

### Pattern 1: Transport Adapter (Strategy Pattern)

**What:** The transport layer is selected once at startup via env var. Transaction logic never imports from transport implementations directly.

**When:** All inter-service calls from orchestrator to Stock/Payment.

**How:**
```python
# orchestrator/transport.py
import os

COMM_MODE = os.environ.get("COMM_MODE", "grpc")

if COMM_MODE == "queue":
    from queue_client import (
        reserve_stock, release_stock, charge_payment, refund_payment,
        prepare_reserve, commit_reserve, abort_reserve,
        prepare_charge, commit_charge, abort_charge,
        init_transport, close_transport,
    )
else:
    from client import (
        reserve_stock, release_stock, charge_payment, refund_payment,
        init_grpc_clients as init_transport,
        close_grpc_clients as close_transport,
    )
    from client_2pc import (
        prepare_reserve, commit_reserve, abort_reserve,
        prepare_charge, commit_charge, abort_charge,
    )
```

Both `saga_coordinator.py` and `tpc_coordinator.py` import from `transport.py`, never from `client.py` or `queue_client.py` directly.

### Pattern 2: Transaction Coordinator Selector

**What:** Select SAGA or 2PC coordinator at startup. The gRPC servicer delegates to whichever is active.

**How:**
```python
# orchestrator/grpc_server.py (modified)
import os

TRANSACTION_PATTERN = os.environ.get("TRANSACTION_PATTERN", "saga")

if TRANSACTION_PATTERN == "2pc":
    from tpc_coordinator import run_checkout
else:
    from saga_coordinator import run_checkout

class OrchestratorServiceServicer(...):
    async def StartCheckout(self, request, context):
        items = [{"item_id": item.item_id, "quantity": item.quantity} for item in request.items]
        result = await run_checkout(self.db, request.order_id, request.user_id, items, request.total_cost)
        return CheckoutResponse(success=result["success"], error_message=result["error_message"])
```

### Pattern 3: Business Logic Extraction

**What:** Pure async functions that take a Redis client + request parameters and return result dicts. No gRPC or queue awareness.

**When:** All Stock/Payment operations that both gRPC servicer and queue consumer need.

**How:**
```python
# stock/operations.py
async def reserve_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    """Returns {"success": bool, "error_message": str}"""
    # Existing Lua CAS logic moved here from grpc_server.py
    ...

async def prepare_reserve(db, tx_id: str, items: list[dict]) -> dict:
    """Returns {"vote_yes": bool, "error_message": str}"""
    # NEW: multi-item atomic reservation for 2PC
    ...
```

Both `grpc_server.py` and `queue_consumer.py` call these functions.

### Pattern 4: Reuse Existing Lua Idempotency for 2PC

**What:** 2PC operations use the same Lua-based idempotency mechanism already proven in v1.0.

**Why:** The existing `RESERVE_STOCK_ATOMIC_LUA` and `CHARGE_PAYMENT_ATOMIC_LUA` scripts already provide exactly-once semantics via CAS. 2PC prepare is the same mutation with a different idempotency key prefix (`{2pc:<oid>}:step:prepare_stock` instead of `{saga:<oid>}:step:reserve:<iid>`).

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Four Separate Code Paths

**What:** Writing distinct checkout flows for each of the 4 env var combinations.

**Why bad:** Quadratic code growth, impossible to test all combinations, bugs in one path not caught in others.

**Instead:** Layer the abstractions. Transaction logic (SAGA or 2PC) calls transport-agnostic functions. Transport layer (gRPC or queue) implements those functions. The two axes are independent and composable.

### Anti-Pattern 2: Queue Consumer Calling gRPC Servicer

**What:** Stock/Payment queue consumer receives a message, then calls the gRPC servicer class methods.

**Why bad:** Unnecessary serialization/deserialization roundtrip, coupling to gRPC types.

**Instead:** Extract business logic into `operations.py`. Both gRPC servicer and queue consumer call the same operations.

### Anti-Pattern 3: Synchronous Polling for Queue Replies

**What:** After sending a queue request, busy-polling XREAD in a tight loop waiting for the reply.

**Why bad:** Burns CPU, blocks the asyncio event loop, doesn't scale with concurrent requests.

**Instead:** Use the asyncio.Future map with a single background reply consumer (Pattern described above).

### Anti-Pattern 4: Mixing Event Streams and Command Streams

**What:** Sending request/reply commands on the existing `{saga:events}:checkout` stream.

**Why bad:** Different delivery guarantees (fire-and-forget vs. exactly-once), different consumer semantics, pollutes audit trail with operational messages.

**Instead:** Separate stream topology. `{saga:events}:*` for events. `{queue}:*` for commands.

---

## Env Var Configuration Summary

| Variable | Values | Default | Scope | Effect |
|----------|--------|---------|-------|--------|
| `TRANSACTION_PATTERN` | `saga`, `2pc` | `saga` | Orchestrator only | Selects SAGA or 2PC coordinator logic |
| `COMM_MODE` | `grpc`, `queue` | `grpc` | All services | Orchestrator: selects client.py or queue_client.py. Stock/Payment: starts queue consumer if `queue` |

**Deployment constraint:** Both env vars must be set consistently across ALL services. Mixed modes will fail silently. Kubernetes ConfigMap or Helm values should set these once for the entire deployment.

---

## Suggested Build Order

Dependencies flow bottom-up: extract logic -> build transport -> build transaction pattern -> integrate.

### Phase 1: Extract Business Logic (prerequisite for everything)

**New files:** `stock/operations.py`, `payment/operations.py`
**Modified files:** `stock/grpc_server.py`, `payment/grpc_server.py`

Extract Lua-script-based business logic from gRPC servicers into standalone async functions. gRPC servicers become thin wrappers calling operations.py. This is a pure refactor -- zero behavior change.

**Why first:** Both queue consumers AND 2PC participant operations need to call the same business logic. Without this extraction, we'd have to duplicate Lua scripts or create awkward coupling.

**Validation:** All existing integration tests pass unchanged after refactor.

### Phase 2: Redis Streams Request/Reply Infrastructure

**New files:** `orchestrator/queue_client.py`, `orchestrator/reply_consumer.py`, `stock/queue_consumer.py`, `payment/queue_consumer.py`
**Modified files:** `orchestrator/app.py`, `stock/app.py`, `payment/app.py` (conditional startup)

Build the queue communication layer. Orchestrator sends commands via streams and receives replies. Stock/Payment consume from request streams and produce replies.

**Why second:** This is the new transport layer. Both SAGA and 2PC can use it. Building it with the existing SAGA flow means we can validate it against the known-good SAGA behavior.

**Validation:** SAGA + queue mode passes all existing integration tests (same behavior, different transport).

### Phase 3: Transport Adapter + COMM_MODE Toggle

**New files:** `orchestrator/transport.py`
**Modified files:** `orchestrator/saga_coordinator.py` (extracted from grpc_server.py), `orchestrator/grpc_server.py`

Wire up the env var toggle. Extract `run_checkout` and `run_compensation` from `grpc_server.py` into `saga_coordinator.py`. Both import from `transport.py` instead of `client.py` directly.

**Why third:** Connects Phase 2 to existing SAGA flow through a clean abstraction.

**Validation:** Toggle `COMM_MODE` between `grpc` and `queue`, run full test suite -- both pass.

### Phase 4: 2PC State Machine + Participant Operations

**New files:** `orchestrator/tpc.py`, 2PC RPCs added to protos, 2PC functions in `stock/operations.py` and `payment/operations.py`
**Modified files:** `stock/grpc_server.py`, `payment/grpc_server.py` (add 2PC RPC handlers), `stock/queue_consumer.py`, `payment/queue_consumer.py` (add 2PC command dispatch)

Build the 2PC state machine (create record, Lua CAS transitions) and participant-side prepare/commit/abort operations. Add new RPCs to proto files.

**Why fourth:** Depends on extracted business logic (Phase 1). The 2PC state machine mirrors saga.py closely and can be unit-tested in isolation.

**Validation:** Unit tests for 2PC state transitions. Prepare/commit/abort operations work in isolation.

### Phase 5: 2PC Coordinator + Recovery

**New files:** `orchestrator/tpc_coordinator.py`, `orchestrator/tpc_recovery.py`
**Modified files:** `orchestrator/recovery.py` (dispatch to SAGA or 2PC), `orchestrator/grpc_server.py` (TRANSACTION_PATTERN toggle)

Implement the 2PC coordination flow (concurrent prepare, decision, commit/abort with retry-forever) and the recovery scanner.

**Why fifth:** Depends on 2PC state machine (Phase 4) and transport adapter (Phase 3). This is the highest-complexity new code.

**Validation:** 2PC + gRPC end-to-end. 2PC + queue end-to-end. Recovery for stuck 2PC transactions.

### Phase 6: Integration Testing + Benchmark

**Modified files:** `orchestrator/app.py` (final wiring), integration test suite
**New files:** Test configurations for all 4 combinations

Wire everything together. Update startup code. Run integration tests for all 4 env var combinations. Run benchmark with 0 consistency violations.

**Validation:** All 4 combinations pass integration tests. Benchmark passes for each.

### Build Order Dependency Graph

```
Phase 1: Extract Business Logic
    |
    +---> Phase 2: Queue Infrastructure
    |         |
    |         +---> Phase 3: Transport Adapter
    |                   |
    +---> Phase 4: 2PC State Machine + Participants
              |         |
              +----+----+
                   |
                   v
             Phase 5: 2PC Coordinator
                   |
                   v
             Phase 6: Integration
```

Phases 2 and 4 can be developed in parallel after Phase 1.

---

## Scalability Considerations

| Concern | v1.0 (SAGA+gRPC) | v2.0 Queue Mode | v2.0 2PC Mode |
|---------|-------------------|-----------------|---------------|
| Inter-service latency | ~1ms (gRPC direct) | ~3-5ms (XADD + XREADGROUP + XADD reply) | Same as transport layer |
| Concurrent requests | Limited by gRPC channel capacity | Limited by reply consumer throughput | Slightly better -- parallel prepare |
| Load balancing | Single gRPC stub (no balancing) | Automatic via consumer groups | Same as transport layer |
| Service scaling | HPA but gRPC routes to one pod | Consumer groups auto-distribute | Same |
| Failure detection | Circuit breaker + gRPC timeout | Reply timeout + XAUTOCLAIM | Prepare timeout = vote NO |
| Message durability | None (gRPC is transient) | Redis Streams with AOF | Same |
| Orchestrator replicas | 1 (split-brain avoidance) | 1 (single reply consumer) | 1 (single coordinator) |

---

## Sources

- Existing v1.0 codebase (direct code reading) -- HIGH confidence
- Redis Streams XREADGROUP, XADD, consumer groups -- HIGH confidence (already used in v1.0 for events)
- 2PC protocol semantics (distributed systems literature) -- HIGH confidence (well-established algorithm)
- Redis limitation re: no native XA/prepare-commit -- HIGH confidence (confirmed in PROJECT.md, inherent to Redis design)
- asyncio.Future for correlation-based request/reply -- HIGH confidence (standard Python asyncio pattern)

---

*Research authored: 2026-03-12 for v2.0 milestone*
