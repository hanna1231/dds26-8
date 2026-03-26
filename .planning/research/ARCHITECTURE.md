# Architecture Research

**Domain:** Abstract workflow engine orchestrator for distributed checkout microservices
**Researched:** 2026-03-26
**Confidence:** HIGH (derived entirely from direct codebase analysis of the v2.0 implementation)

---

## Context: What Exists and What Changes

This is a **subsequent milestone** research document. The v2.0 codebase is already shipped and running. The question is specifically: how does an abstract workflow engine integrate with the existing orchestrator internals?

The constraint from the milestone brief is explicit: **the orchestrator service stays — its internals change from hardcoded logic to generic engine**. No service boundary changes, no new services, no API contract changes.

---

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Orchestrator Service (unchanged external shape)    │
│                                                                      │
│  ┌──────────────┐     ┌──────────────────────────────────────────┐   │
│  │  grpc_server │────>│          workflow_engine.py              │   │
│  │   (thin      │     │  WorkflowEngine.execute(definition, ctx) │   │
│  │   servicer)  │     │                                          │   │
│  └──────────────┘     │  Strategies:                             │   │
│                       │  - SagaStrategy (compensating steps)     │   │
│  ┌──────────────┐     │  - TwoPhaseStrategy (prepare/commit)     │   │
│  │  recovery.py │────>│                                          │   │
│  │  (startup    │     │  State Store: workflow_store.py          │   │
│  │   scanner)   │     │  (replaces saga.py + tpc.py)             │   │
│  └──────────────┘     └──────────────────────────────────────────┘   │
│                                           │                          │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │                     transport.py (unchanged)                  │    │
│  │  reserve_stock / release_stock / charge_payment / ...         │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                           │                          │
└───────────────────────────────────────────┼──────────────────────────┘
                                            │
             gRPC / Redis Streams (COMM_MODE toggle, unchanged)
                                            │
                            ┌───────────────┴───────────────┐
                            │  Stock Service                 │
                            │  Payment Service               │
                            └───────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | v3.0 Change |
|-----------|----------------|-------------|
| `grpc_server.py` | Receive `StartCheckout` gRPC call, delegate to engine | Becomes thin: imports `workflow_engine`, calls `engine.execute(checkout_workflow, ctx)` |
| `workflow_engine.py` | Execute a workflow definition with a chosen strategy | NEW — the generic engine |
| `workflow_store.py` | Persist workflow execution state in Redis | NEW — replaces `saga.py` + `tpc.py` |
| `workflows/checkout.py` | The checkout workflow defined as data | NEW — replaces `run_checkout` / `run_2pc_checkout` logic |
| `saga.py` | SAGA-specific state machine (create/transition/get) | DELETED or superseded by `workflow_store.py` |
| `tpc.py` | 2PC-specific state machine | DELETED or superseded by `workflow_store.py` |
| `recovery.py` | Startup scanner for stuck transactions | MODIFIED — calls `engine.resume(workflow_id)` instead of protocol-specific logic |
| `transport.py` | Inter-service call abstraction | UNCHANGED |
| `consumers.py` | Compensation event consumer loops | MODIFIED — drives engine compensation via engine API |

---

## Recommended Project Structure

```
orchestrator/
├── app.py                     # Unchanged entrypoint (modified only for new init)
├── grpc_server.py             # Thin servicer — calls engine, returns response
├── workflow_engine.py         # NEW: WorkflowEngine class (execute, resume, compensate)
├── workflow_store.py          # NEW: generic Redis state persistence for workflows
├── strategy/
│   ├── __init__.py
│   ├── saga_strategy.py       # NEW: SagaStrategy (sequential + compensation)
│   └── tpc_strategy.py        # NEW: TwoPhaseStrategy (prepare/commit/abort)
├── workflows/
│   ├── __init__.py
│   └── checkout.py            # NEW: checkout workflow definition (data, not code)
├── transport.py               # UNCHANGED
├── client.py                  # UNCHANGED
├── queue_client.py            # UNCHANGED
├── recovery.py                # MODIFIED: calls engine.resume() instead of SAGA/TPC-specific code
├── consumers.py               # MODIFIED: compensation consumer calls engine compensation
├── events.py                  # UNCHANGED
├── circuit.py                 # UNCHANGED
├── reply_listener.py          # UNCHANGED
│
│   # These are superseded — keep temporarily for reference, delete after engine is stable
├── saga.py                    # SUPERSEDED by workflow_store.py
└── tpc.py                     # SUPERSEDED by workflow_store.py
```

### Structure Rationale

- **`workflow_engine.py`:** Single class that knows how to run a `WorkflowDefinition` using a `Strategy`. It is transport-agnostic and protocol-agnostic. Think of it as the runtime.
- **`workflow_store.py`:** Generic Redis persistence layer. Replaces the two duplicated Redis hash + Lua CAS state machines in `saga.py` and `tpc.py`. States are stored per-workflow with a protocol tag.
- **`strategy/`:** The SAGA and 2PC execution strategies. Strategy selection happens once at startup via `TRANSACTION_PATTERN` env var, exactly as before.
- **`workflows/`:** Workflow definitions are Python dataclasses or dicts — data, not code. The checkout workflow is registered here as a sequence of named steps with action callables and compensation callables.

---

## Architectural Patterns

### Pattern 1: Workflow Definition as Data

**What:** A `WorkflowDefinition` is a Python object (dataclass) that describes the steps of a workflow as a list of `Step` objects. Each `Step` names an action (a callable via `transport.*`) and an optional compensation callable. The engine executes the steps; the definition owns no execution logic.

**When to use:** This is the core abstraction that makes the engine generic. All protocol-specific behavior is in the Strategy, not the definition.

**Trade-offs:**
- Pro: Checkout changes become data changes (add a step, change a step's compensation)
- Pro: Testing the workflow definition doesn't require running the engine
- Con: Callables inside the definition are still functions, not strings; the definition isn't fully serializable without extra indirection. For this project's scope (single workflow), that's acceptable.

**Example:**
```python
# orchestrator/workflows/checkout.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class Step:
    name: str
    action: Callable[..., Any]        # async fn(ctx) -> {"success": bool, ...}
    compensation: Callable[..., Any]  # async fn(ctx) -> {"success": bool, ...}
    idempotency_key_template: str     # e.g. "{workflow_id}:step:reserve:{item_id}"


def build_checkout_workflow(items, user_id, total_cost):
    """Returns a WorkflowDefinition for the checkout transaction."""
    from transport import reserve_stock, release_stock, charge_payment, refund_payment

    steps = []
    for item in items:
        iid, qty = item["item_id"], item["quantity"]
        steps.append(Step(
            name=f"reserve_stock:{iid}",
            action=lambda iid=iid, qty=qty, ctx=None: reserve_stock(
                iid, qty, f"{{workflow:{ctx['workflow_id']}}}:step:reserve:{iid}"
            ),
            compensation=lambda iid=iid, qty=qty, ctx=None: release_stock(
                iid, qty, f"{{workflow:{ctx['workflow_id']}}}:step:release:{iid}"
            ),
            idempotency_key_template=f"reserve:{iid}",
        ))

    steps.append(Step(
        name="charge_payment",
        action=lambda ctx=None: charge_payment(
            user_id, total_cost, f"{{workflow:{ctx['workflow_id']}}}:step:charge"
        ),
        compensation=lambda ctx=None: refund_payment(
            user_id, total_cost, f"{{workflow:{ctx['workflow_id']}}}:step:refund"
        ),
        idempotency_key_template="charge_payment",
    ))

    return WorkflowDefinition(
        name="checkout",
        steps=steps,
        workflow_id=None,  # set at execution time
    )
```

### Pattern 2: Strategy as Execution Protocol

**What:** The `WorkflowEngine` accepts a `Strategy` object that knows how to execute the step list. The `SagaStrategy` runs steps sequentially and compensates in reverse on failure. The `TwoPhaseStrategy` runs prepare-phase on all steps concurrently, then commit or abort.

**When to use:** Each time a `WorkflowDefinition` is executed, the engine selects the strategy once. The strategy knows about SAGA/2PC mechanics; the engine knows about state persistence.

**Trade-offs:**
- Pro: Adding a third execution protocol (e.g., saga with parallel stock reservations) means adding a new Strategy class, not touching the engine or the definition
- Con: The two protocols have genuinely different step signatures (SAGA: action+compensation per step; 2PC: prepare+commit+abort per step). The Step dataclass must accommodate both, either via optional fields or via protocol-specific Step subclasses. Use optional fields — it keeps the definition simpler and the checkout workflow only defines one workflow anyway.

**Example:**
```python
# orchestrator/strategy/saga_strategy.py
class SagaStrategy:
    async def execute(self, steps, ctx, store, db) -> dict:
        """Run steps sequentially. On failure, compensate in reverse."""
        executed = []
        for step in steps:
            result = await retry_forward(lambda: step.action(ctx))
            if not result["success"]:
                await store.transition(ctx["workflow_id"], db, "COMPENSATING")
                await self._compensate(executed, ctx, store, db)
                return {"success": False, "error_message": result["error_message"]}
            executed.append(step)
            await store.mark_step_done(ctx["workflow_id"], step.name, db)
        await store.transition(ctx["workflow_id"], db, "COMPLETED")
        return {"success": True, "error_message": ""}

    async def _compensate(self, executed_steps, ctx, store, db) -> None:
        for step in reversed(executed_steps):
            await retry_forever(lambda: step.compensation(ctx))
        await store.transition(ctx["workflow_id"], db, "FAILED")


# orchestrator/strategy/tpc_strategy.py
class TwoPhaseStrategy:
    async def execute(self, steps, ctx, store, db) -> dict:
        """Prepare all concurrently. Commit all or abort all."""
        await store.transition(ctx["workflow_id"], db, "PREPARING")
        results = await asyncio.gather(*[step.action(ctx) for step in steps], return_exceptions=True)
        all_yes = all(
            not isinstance(r, Exception) and r.get("success")
            for r in results
        )
        if all_yes:
            await store.transition(ctx["workflow_id"], db, "COMMITTING")  # WAL before phase 2
            await asyncio.gather(*[step.commit(ctx) for step in steps], return_exceptions=True)
            await store.transition(ctx["workflow_id"], db, "COMMITTED")
            return {"success": True, "error_message": ""}
        else:
            await store.transition(ctx["workflow_id"], db, "ABORTING")  # WAL before phase 2
            await asyncio.gather(*[step.abort(ctx) for step in steps], return_exceptions=True)
            await store.transition(ctx["workflow_id"], db, "ABORTED")
            first_error = next(
                (str(r) if isinstance(r, Exception) else r.get("error_message", "")
                 for r in results if isinstance(r, Exception) or not r.get("success")), ""
            )
            return {"success": False, "error_message": first_error}
```

### Pattern 3: Generic Workflow Store (replaces saga.py + tpc.py)

**What:** `workflow_store.py` provides a single Redis persistence layer for all workflow executions, regardless of protocol. It uses the same Lua CAS pattern already proven in `saga.py` and `tpc.py`, but parameterized: the valid transitions are passed by the Strategy at creation time rather than hardcoded.

**When to use:** Whenever any code needs to read, create, or transition workflow state. The store is transport-agnostic and protocol-agnostic.

**Trade-offs:**
- Pro: The SAGA Lua script and the 2PC Lua script are identical — this eliminates the duplication that already exists between `saga.py` and `tpc.py`
- Pro: Recovery scanner only needs to know "workflow store" not "is this a saga or tpc record"
- Con: State namespaces still need to be separate (`{workflow:saga:<id>}` vs `{workflow:tpc:<id>}`) to avoid cross-protocol confusion. The store can enforce this via a `protocol` field.

**Example:**
```python
# orchestrator/workflow_store.py

TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""


class WorkflowStore:
    def __init__(self, valid_transitions: dict[str, set[str]], key_prefix: str):
        self.valid_transitions = valid_transitions
        self.key_prefix = key_prefix  # e.g. "workflow:saga" or "workflow:tpc"

    def key(self, workflow_id: str) -> str:
        return f"{{{self.key_prefix}:{workflow_id}}}"

    async def create(self, db, workflow_id: str, metadata: dict) -> bool:
        k = self.key(workflow_id)
        created = await db.hsetnx(k, "state", "STARTED")
        if not created:
            return False
        await db.hset(k, mapping={**metadata, "workflow_id": workflow_id,
                                  "started_at": str(int(time.time())),
                                  "updated_at": str(int(time.time()))})
        await db.expire(k, 7 * 24 * 3600)
        return True

    async def transition(self, workflow_id: str, db, from_state: str, to_state: str,
                         flag_field: str = "", flag_value: str = "") -> bool:
        allowed = self.valid_transitions.get(from_state, set())
        if to_state not in allowed:
            raise ValueError(f"Invalid transition: {from_state} -> {to_state}")
        return bool(await db.eval(
            TRANSITION_LUA, 1, self.key(workflow_id),
            from_state, to_state, flag_field, flag_value,
        ))

    async def get(self, db, workflow_id: str) -> dict | None:
        raw = await db.hgetall(self.key(workflow_id))
        if not raw:
            return None
        return {k.decode(): v.decode() for k, v in raw.items()}


# Pre-built stores for SAGA and 2PC
SAGA_STORE = WorkflowStore(
    valid_transitions={
        "STARTED": {"STOCK_RESERVED", "COMPENSATING"},
        "STOCK_RESERVED": {"PAYMENT_CHARGED", "COMPENSATING"},
        "PAYMENT_CHARGED": {"COMPLETED", "COMPENSATING"},
        "COMPENSATING": {"FAILED"},
    },
    key_prefix="workflow:saga",
)

TPC_STORE = WorkflowStore(
    valid_transitions={
        "INIT": {"PREPARING"},
        "PREPARING": {"COMMITTING", "ABORTING"},
        "COMMITTING": {"COMMITTED"},
        "ABORTING": {"ABORTED"},
    },
    key_prefix="workflow:tpc",
)
```

---

## Data Flow

### Request Flow (v3.0)

```
Order Service
    │ gRPC StartCheckout
    ▼
grpc_server.py (OrchestratorServiceServicer.StartCheckout)
    │ build_checkout_workflow(items, user_id, total_cost)
    │ engine.execute(workflow_def, ctx={workflow_id, user_id, ...}, db)
    ▼
WorkflowEngine.execute()
    │ store.create(db, workflow_id, metadata)
    │ publish_event("checkout_started", ...)
    │ strategy.execute(steps, ctx, store, db)
    ▼
SagaStrategy / TwoPhaseStrategy
    │ calls step.action(ctx) via transport.*
    │ updates store state at each step boundary
    │ on failure: strategy drives compensation/abort
    ▼
transport.py (reserve_stock / charge_payment / prepare_* / commit_* / abort_*)
    │ gRPC or Redis Streams (COMM_MODE toggle, unchanged)
    ▼
Stock Service / Payment Service
```

### State Management

```
WorkflowStore (Redis Hash per workflow_id)
    │
    ├── SAGA path: STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED
    │                      -> COMPENSATING -> FAILED
    │
    └── 2PC path:  INIT -> PREPARING -> COMMITTING -> COMMITTED
                                     -> ABORTING  -> ABORTED
```

### Key Data Flows

1. **New checkout execution:** `grpc_server` calls `engine.execute()` with a freshly-built `WorkflowDefinition` and the workflow ID. Engine creates the store record, delegates to the strategy, strategy drives transport calls.
2. **Compensation trigger:** Strategy calls `store.transition(id, "COMPENSATING")`, then iterates reversed executed steps calling `step.compensation(ctx)` through `retry_forever`.
3. **Recovery on restart:** `recovery.py` scans `{workflow:saga:*}` and `{workflow:tpc:*}` keys, reads the state field, calls `engine.resume(workflow_id, strategy, db)` for each non-terminal record.
4. **Consumer-driven compensation:** `consumers.py` `compensation_consumer` receives `compensation_triggered` event, calls `engine.compensate(workflow_id, db)` instead of importing `run_compensation` from `grpc_server`.

---

## Component Change Map

This is the highest-value section for roadmap planning. Every file in the orchestrator is listed with its disposition.

### New Files

| File | Purpose | Notes |
|------|---------|-------|
| `orchestrator/workflow_engine.py` | `WorkflowEngine` class: `execute()`, `resume()`, `compensate()` | Core engine. ~100 lines. |
| `orchestrator/workflow_store.py` | Generic Redis state persistence, Lua CAS transitions | Replaces `saga.py` + `tpc.py`. ~120 lines. |
| `orchestrator/strategy/saga_strategy.py` | `SagaStrategy`: sequential forward + reverse compensation | Extracted from `run_checkout` + `run_compensation` in `grpc_server.py`. ~80 lines. |
| `orchestrator/strategy/tpc_strategy.py` | `TwoPhaseStrategy`: concurrent prepare + commit/abort | Extracted from `run_2pc_checkout` in `grpc_server.py`. ~80 lines. |
| `orchestrator/workflows/checkout.py` | `build_checkout_workflow()` — the checkout workflow as data | Contains the step list that previously lived inline in `run_checkout`. ~60 lines. |

### Modified Files

| File | What Changes | What Stays |
|------|-------------|-----------|
| `orchestrator/grpc_server.py` | `run_checkout` and `run_2pc_checkout` replaced by `engine.execute(build_checkout_workflow(...), ctx, db)`. `retry_forever` / `retry_forward` move to `strategy/`. `run_compensation` moves to `SagaStrategy`. | `OrchestratorServiceServicer`, `serve_grpc`, `stop_grpc_server`. |
| `orchestrator/recovery.py` | `resume_saga` and `resume_tpc` replaced by `engine.resume(workflow_id, db)`. `recover_incomplete_sagas` / `recover_incomplete_tpc` replaced by single `recover_incomplete_workflows` that scans both prefixes. | Staleness threshold logic, scan pattern. |
| `orchestrator/consumers.py` | `_handle_compensation_message` calls `engine.compensate(order_id, db)` instead of importing `run_compensation` from `grpc_server`. | Consumer group setup, `XAUTOCLAIM`, `XREADGROUP` loops. |
| `orchestrator/app.py` | Startup initializes `WorkflowEngine` with the strategy selected by `TRANSACTION_PATTERN`. Engine instance passed to or imported by `grpc_server`. | Redis init, COMM_MODE branching, background task setup, health endpoint. |

### Unchanged Files

| File | Reason |
|------|--------|
| `orchestrator/transport.py` | Transport abstraction is orthogonal to engine abstraction — this is still the correct layer |
| `orchestrator/client.py` | gRPC transport implementation — unchanged |
| `orchestrator/queue_client.py` | Queue transport implementation — unchanged |
| `orchestrator/reply_listener.py` | Queue reply handling — unchanged |
| `orchestrator/events.py` | Event publishing — unchanged |
| `orchestrator/circuit.py` | Circuit breaker instances — unchanged |
| `orchestrator/saga.py` | SUPERSEDED — keep until `workflow_store.py` is validated, then delete |
| `orchestrator/tpc.py` | SUPERSEDED — keep until `workflow_store.py` is validated, then delete |
| All stock/* files | Domain service internals unchanged — transport layer handles the interface |
| All payment/* files | Domain service internals unchanged |
| All order/* files | No changes to external API or service boundary |
| `protos/*.proto` | No new gRPC definitions needed — v3.0 is a pure orchestrator-internal refactor |
| `docker-compose.yml`, `k8s/`, `helm-config/` | Deployment configs unchanged — same services, same env vars, same ports |

---

## Integration Points

### External Services (unchanged)

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Stock Service | `transport.reserve_stock / release_stock / prepare_stock / ...` | No change — engine calls transport, transport calls stock |
| Payment Service | `transport.charge_payment / refund_payment / prepare_payment / ...` | No change |
| Order Service | gRPC `StartCheckout` RPC | No change — Order still calls this; only orchestrator internals change |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `grpc_server` ↔ `workflow_engine` | Direct function call: `engine.execute(definition, ctx, db)` | Engine is a plain Python class instance |
| `workflow_engine` ↔ `strategy/*` | Method call: `strategy.execute(steps, ctx, store, db)` | Strategy is selected once at startup, injected into engine |
| `strategy/*` ↔ `transport` | Direct async function call: `transport.reserve_stock(...)` | Step `action` callables close over transport functions |
| `workflow_engine` ↔ `workflow_store` | Method calls on `WorkflowStore` instance | Store is injected into engine at construction |
| `recovery` ↔ `workflow_engine` | `engine.resume(workflow_id, db)` | Recovery scanner delegates to engine, engine uses stored state to resume |
| `consumers` ↔ `workflow_engine` | `engine.compensate(workflow_id, db)` | Compensation consumer no longer imports from `grpc_server` |

---

## Build Order

Dependencies flow: workflow store and workflow definition have no mutual dependency and can be built in parallel. Strategy classes depend on both. The engine wires them together.

### Step 1: WorkflowStore (replaces saga.py / tpc.py)

**New file:** `orchestrator/workflow_store.py`
**Replaces:** The Lua CAS logic in `saga.py` and `tpc.py`
**Depends on:** Nothing new
**Why first:** Every other component (engine, strategies, recovery) reads/writes workflow state. The store must exist before anything else can be built.
**Validation:** Unit tests that create records, transition states, and verify Lua CAS rejection of invalid transitions. Run existing integration tests with `workflow_store.py` wired into `grpc_server.py` alongside existing saga.py/tpc.py (dual-write for safety).

### Step 2: WorkflowDefinition + Checkout Workflow (define the data model)

**New files:** `orchestrator/workflow_engine.py` (data classes only: `Step`, `WorkflowDefinition`, `WorkflowContext`), `orchestrator/workflows/checkout.py`
**Depends on:** Step 1 (idempotency key format must match store's key prefix)
**Why second:** The step and definition types are needed by both the strategies and the engine. Defining them as data structures before implementing execution logic prevents circular design.
**Validation:** Unit test `build_checkout_workflow()` produces the expected step list without executing anything.

### Step 3: Strategy Classes

**New files:** `orchestrator/strategy/saga_strategy.py`, `orchestrator/strategy/tpc_strategy.py`
**Depends on:** Step 2 (step types), Step 1 (store transitions), `retry_forever`/`retry_forward` (moved here from `grpc_server.py`)
**Why third:** The strategies encapsulate `run_checkout` / `run_2pc_checkout` / `run_compensation` logic. They can be unit-tested in isolation against a mock store before the engine is complete.
**Validation:** Unit tests driving SagaStrategy with mock steps, verifying compensation is called in reverse order. Unit tests driving TwoPhaseStrategy with mock steps, verifying WAL (COMMITTING before commit messages).

### Step 4: WorkflowEngine (wires everything)

**New file:** `orchestrator/workflow_engine.py` (the `WorkflowEngine` class, extending the Step/Definition types from Step 2)
**Depends on:** Steps 1–3
**Why fourth:** Engine is the integrator. `execute()` calls `store.create()`, publishes the start event, calls `strategy.execute()`. `resume()` reads the current state from the store and re-enters the strategy at the right point. `compensate()` reads the store state and calls `strategy.compensate()`.
**Validation:** Integration test: full checkout happy path through engine. Integration test: checkout with stock failure triggers compensation. Integration test: checkout with payment failure after stock reserved triggers partial compensation.

### Step 5: Wire Into grpc_server and recovery

**Modified files:** `orchestrator/grpc_server.py`, `orchestrator/recovery.py`, `orchestrator/consumers.py`, `orchestrator/app.py`
**Depends on:** Step 4
**Why fifth:** Swap out the hardcoded `run_checkout` / `run_2pc_checkout` calls in `grpc_server.py`. Update `recovery.py` to use `engine.resume()`. Update `consumers.py` to use `engine.compensate()`. Wire the engine instance in `app.py`.
**Validation:** All existing integration tests pass. Kill-test produces 0 consistency violations. Both TRANSACTION_PATTERN modes work.

### Step 6: Delete saga.py and tpc.py

**Deleted files:** `orchestrator/saga.py`, `orchestrator/tpc.py`
**Depends on:** Step 5 (fully validated)
**Why last:** Removing the old modules only after the new ones are proven prevents regression risk. This also confirms that no other file still imports from the old modules.
**Validation:** `grep -r "from saga import\|from tpc import" orchestrator/` returns nothing. Full integration test suite passes.

### Build Order Dependency Graph

```
Step 1: WorkflowStore
    │
    ├──> Step 2: WorkflowDefinition + Checkout Workflow
    │       │
    │       └──> Step 3: SagaStrategy + TwoPhaseStrategy
    │                       │
    │                       └──> Step 4: WorkflowEngine
    │                                       │
    │                                       └──> Step 5: Wire grpc_server + recovery + consumers
    │                                                       │
    │                                                       └──> Step 6: Delete saga.py + tpc.py
```

---

## Anti-Patterns

### Anti-Pattern 1: Engine Knows About Checkout

**What people do:** Put checkout-specific logic (reserve stock per item, charge payment) inside `WorkflowEngine` methods as special cases.
**Why it's wrong:** The engine becomes non-generic. Adding a second workflow (e.g., refund, restock) requires modifying the engine class.
**Do this instead:** `WorkflowEngine.execute()` takes a `WorkflowDefinition`. All checkout knowledge lives in `workflows/checkout.py`. The engine runs whatever steps it is given.

### Anti-Pattern 2: Strategy Knows the Step Names

**What people do:** `SagaStrategy` checks `if step.name == "charge_payment": do_payment_specific_thing`.
**Why it's wrong:** Couples the execution protocol to a specific workflow. SagaStrategy must work for any workflow definition.
**Do this instead:** Steps carry all the information the strategy needs (`action`, `compensation`, completion flags). Strategy operates on the step interface, not on step names.

### Anti-Pattern 3: Keep saga.py and tpc.py as Live Modules

**What people do:** Keep the old state machines running in parallel with `WorkflowStore` indefinitely "just in case."
**Why it's wrong:** Dual-write logic creates split-brain. Recovery scanner must check both old and new key prefixes. State can diverge.
**Do this instead:** Run in parallel only during the validation window (Step 5 above). Delete once the new store is proven correct under integration tests.

### Anti-Pattern 4: New Redis Key Format for Workflow Store

**What people do:** Use a completely different key format (`workflow:{id}`) that doesn't match existing recovery scanner patterns.
**Why it's wrong:** In-flight v2.0 records (keys like `{saga:<id>}` and `{tpc:<id>}>`) exist in Redis when v3.0 is deployed. Recovery must handle both old and new key formats during rollover.
**Do this instead:** During migration, keep the key prefixes identical (`{saga:<id>}`, `{tpc:<id>}`) so recovery still works. After all old records expire (7-day TTL), optionally migrate to a unified prefix. This is the zero-risk path.

### Anti-Pattern 5: Workflow Definition as Global State

**What people do:** Define the checkout workflow as a module-level constant imported from `workflows/checkout.py`.
**Why it's wrong:** Each checkout execution needs a fresh definition with closures over the specific `items`, `user_id`, and `total_cost` for that order. A shared global cannot carry per-request data.
**Do this instead:** `build_checkout_workflow(items, user_id, total_cost)` is a factory function called per request inside `StartCheckout`.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Current (20 CPU benchmark) | Single orchestrator replica is correct and sufficient — no change |
| >20 CPUs, multiple orchestrators | Workflow engine + Lua CAS are already safe for concurrent execution: HSETNX creates exactly one record, Lua CAS rejects duplicate transitions. Multiple orchestrators can safely run the same engine code. |
| New workflow types | Add a new file to `workflows/`. No engine or strategy changes needed. |
| New execution strategy | Add a new class to `strategy/`. No engine or workflow definition changes needed. |

---

## Sources

- Direct codebase analysis: `orchestrator/grpc_server.py`, `orchestrator/saga.py`, `orchestrator/tpc.py`, `orchestrator/recovery.py`, `orchestrator/transport.py`, `orchestrator/consumers.py`, `orchestrator/app.py` — HIGH confidence
- PROJECT.md v3.0 milestone goal: "Generic workflow engine that executes abstract step sequences (action + compensation) without knowing about specific services" — HIGH confidence (primary requirement source)
- Temporal/Cadence architecture patterns (workflow as data, strategy separation) — MEDIUM confidence (inspiration source; full feature set explicitly out of scope per PROJECT.md)
- Existing v2.0 ARCHITECTURE.md (this file's predecessor) — HIGH confidence for what is unchanged

---

*Architecture research for: v3.0 Abstract Orchestrator — workflow engine integration*
*Researched: 2026-03-26*
