# Workflow Engine Architecture (v3.0)

**Purpose:** Explain how the abstract workflow engine replaced hardcoded SAGA/2PC orchestration, and how checkout flows through the system after the v3.0 refactoring.

---

## Motivation

Before v3.0, checkout coordination lived in two monolithic functions inside `grpc_server.py`:

- `run_checkout()` (~160 lines) — hardcoded SAGA step sequence with inline retry, compensation, and state transitions
- `run_2pc_checkout()` (~100 lines) — hardcoded 2PC prepare/commit/abort with inline voting logic

Both duplicated the same Redis state management patterns, retry logic, and event publishing. The recovery scanner (`recovery.py`) had parallel duplication — separate functions for resuming SAGA and 2PC workflows.

v3.0 extracts this into a generic **workflow engine** inspired by Temporal/Cadence. The engine drives execution without knowing about Stock or Payment. Checkout is expressed as a `WorkflowDefinition` — a data structure describing steps and their compensations.

---

## Architecture Overview

```
                    StartCheckout (gRPC)
                           |
                    grpc_server.py
                           |
                  make_checkout_workflow()     <-- checkout_workflow.py
                           |
                   WorkflowEngine.execute()    <-- workflow_engine.py
                     /            \
            SagaStrategy     TwoPhaseStrategy  <-- saga_strategy.py / tpc_strategy.py
                     \            /
                   WorkflowStore (Redis)       <-- workflow_store.py
                           |
                   transport.py (gRPC or Queue)
                     /            \
              Stock Service    Payment Service
```

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `workflow_types.py` | `WorkflowStep` and `WorkflowDefinition` dataclasses |
| `workflow_store.py` | Redis persistence with Lua CAS atomic transitions |
| `saga_strategy.py` | Sequential execution with reverse compensation |
| `tpc_strategy.py` | Concurrent prepare with WAL commit/abort |
| `workflow_engine.py` | Single entry point — routes to strategy, publishes events |
| `checkout_workflow.py` | Factory that binds transport functions to workflow steps |
| `transport.py` | Facade over gRPC or Redis Streams (selected by `COMM_MODE` env var) |

---

## Data Model

### WorkflowStep

A named pair of async callables — one for the forward action and one for compensation:

```python
@dataclass
class WorkflowStep:
    name: str                                    # e.g. "reserve_stock"
    action: Callable[..., Awaitable[Any]]        # forward operation
    compensation: Callable[..., Awaitable[Any]]  # undo operation
```

### WorkflowDefinition

An ordered sequence of steps with a strategy selector:

```python
@dataclass
class WorkflowDefinition:
    name: str                                    # e.g. "checkout_saga"
    steps: list[WorkflowStep]                    # ordered step list
    strategy: Literal["saga", "2pc"] = "saga"    # selects execution path
```

The strategy field determines which execution strategy the engine uses. Both SAGA and 2PC accept the same `WorkflowDefinition` — the engine is protocol-agnostic.

---

## Checkout Flow

### 1. Request Arrives

`grpc_server.py` receives a `StartCheckout` RPC. The servicer builds a context dict and creates a workflow definition:

```python
definition = make_checkout_workflow(TRANSACTION_PATTERN)  # "saga" or "2pc"
result = await self.engine.execute(order_id, definition, context)
```

### 2. Engine Orchestrates

`WorkflowEngine.execute()` is the single entry point:

1. **Validate strategy** — looks up `definition.strategy` in its internal registry
2. **Create workflow record** — calls `store.create()` with the initial state (`STARTED` for SAGA, `INIT` for 2PC). If the record already exists (duplicate request), returns the cached result
3. **Publish `workflow_started` event** — fire-and-forget to Redis Stream
4. **Delegate to strategy** — `strategy.execute(workflow_id, definition, context, store)`
5. **Publish result event** — `workflow_succeeded` or `workflow_failed`

```python
class WorkflowEngine:
    def __init__(self, store: WorkflowStore, db):
        self._store = store
        self._db = db
        self._strategies = {
            "saga": SagaStrategy(),
            "2pc": TwoPhaseStrategy(),
        }
```

### 3a. SAGA Execution Path

`SagaStrategy.execute()` runs steps **sequentially** with bounded retry:

```
STARTED --> reserve_stock --> STOCK_RESERVED --> charge_payment --> PAYMENT_CHARGED --> COMPLETED
                |                                      |
                | (failure)                            | (failure)
                v                                      v
           COMPENSATING <-------- reverse compensation --------
                |
                v
              FAILED
```

- Each step is retried up to 3 times (`retry_forward`)
- On failure, transitions to `COMPENSATING` and runs compensations in **reverse order** with infinite retry (`retry_forever`)
- `CircuitBreakerError` propagates immediately (never retried)
- Step completion is tracked via `step_N_done` flags in Redis

### 3b. 2PC Execution Path

`TwoPhaseStrategy.execute()` runs prepare **concurrently**, then commits or aborts:

```
INIT --> PREPARING (concurrent prepare_stock + prepare_payment)
              |
         all voted YES?
           /       \
         yes        no
          |          |
     COMMITTING   ABORTING
     (WAL write)  (WAL write)
          |          |
     commit all   abort all
          |          |
     COMMITTED    ABORTED
```

- Phase 1: all step actions fire concurrently via `asyncio.gather()`
- WAL decision written to Redis **before** sending phase-2 messages (crash safety)
- Phase 2a (commit): re-calls `step.action()` concurrently
- Phase 2b (abort): calls `step.compensation()` concurrently

### 4. Checkout Workflow Definition

`checkout_workflow.py` is the **only module** that knows about Stock and Payment. It creates the `WorkflowDefinition` by binding transport functions:

```python
def make_checkout_workflow(strategy: str) -> WorkflowDefinition:
    if strategy == "saga":
        return WorkflowDefinition(
            name="checkout_saga",
            steps=[
                WorkflowStep("reserve_stock", _reserve_all, _release_all),
                WorkflowStep("charge_payment", _charge, _refund),
            ],
            strategy="saga",
        )
```

The step callables (`_reserve_all`, `_charge`, etc.) are module-level async functions that call into `transport.py`. This avoids Python's closure late-binding pitfall.

---

## State Persistence

### WorkflowStore

All workflow state lives in Redis hashes with atomic Lua CAS transitions:

```
Key: {workflow:<order_id>}

Fields:
  state          = "STARTED" | "STOCK_RESERVED" | ... | "COMPLETED" | "FAILED"
  workflow_id    = "<order_id>"
  strategy       = "saga" | "2pc"
  order_id       = "<order_id>"
  user_id        = "<user_id>"
  items          = "[{\"item_id\": ..., \"quantity\": ...}]"
  step_0_done    = "1"   (set after first step completes)
  step_1_done    = "1"   (set after second step completes)
  started_at     = "<unix_timestamp>"
  updated_at     = "<unix_timestamp>"
```

The Lua CAS script ensures atomic state transitions:

```lua
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
return 1
```

This prevents race conditions — if the current state doesn't match the expected state, the transition is rejected.

### Key Design Choices

- **Hash-tagged keys** (`{workflow:<id>}`) — ensures all fields land on the same Redis Cluster slot
- **State-agnostic store** — the store performs blind CAS; strategies own their state enums and valid transitions
- **Step completion markers** (`step_N_done`) — replace hardcoded `stock_reserved`/`payment_charged` fields
- **7-day TTL** — workflow records expire automatically

---

## Recovery

When the orchestrator restarts, `recovery.py` scans for incomplete workflows:

```python
async def recover_incomplete_workflows(db, engine):
    # Scan all {workflow:*} keys
    # For each non-terminal state:
    #   1. Read the stored "strategy" field
    #   2. Reconstruct the WorkflowDefinition via make_checkout_workflow()
    #   3. Call engine.resume(workflow_id, definition, context)
```

`engine.resume()` delegates to the strategy's `resume()` method, which handles each state:

**SAGA recovery:**
- `COMPENSATING` → run compensations for completed steps
- Forward states (`STARTED`, `STOCK_RESERVED`, etc.) → skip completed steps, resume from current position

**2PC recovery:**
- `COMMITTING` → re-send commits (idempotent)
- `INIT` / `PREPARING` → presumed abort (transition to `ABORTING`, send aborts)
- `ABORTING` → re-send aborts (idempotent)

---

## Dependency Injection

`WorkflowEngine` receives all dependencies via its constructor:

```python
# app.py startup
store = WorkflowStore(db)
engine = WorkflowEngine(store=store, db=db)

# Injected into gRPC server
await serve_grpc(db, engine)

# Injected into recovery
await recover_incomplete_workflows(db, engine)

# Injected into compensation consumer
asyncio.create_task(compensation_consumer(db, engine=engine))
```

No module-level singletons or global mutable state. The engine, store, and strategies are all testable in isolation with mock dependencies.

---

## Event Publishing

The engine publishes lifecycle events to a Redis Stream (`{saga:events}:checkout`):

| Event | When |
|-------|------|
| `workflow_started` | Before strategy execution begins |
| `workflow_succeeded` | After strategy returns `success: True` |
| `workflow_failed` | After strategy returns `success: False` |

Events are **fire-and-forget** — failures are logged and counted but never block the checkout path. The stream is capped at 10,000 entries with approximate trimming.

---

## Transport Adapter

`transport.py` conditionally re-exports domain functions based on `COMM_MODE`:

| Mode | Source | Communication |
|------|--------|---------------|
| `grpc` (default) | `client.py` | Direct gRPC calls to Stock/Payment |
| `queue` | `queue_client.py` | Redis Streams request/reply |

Checkout workflow imports from `transport.py` and stays transport-agnostic. The transport mode is selected at startup via environment variable.

---

## Module Dependency Graph

```
app.py
  |-- WorkflowStore(db)
  |-- WorkflowEngine(store, db)
  |-- serve_grpc(db, engine)
  |     |-- OrchestratorServiceServicer(db, engine)
  |           |-- make_checkout_workflow(strategy)  --> checkout_workflow.py
  |           |-- engine.execute(id, definition, ctx)
  |                 |-- store.create()              --> workflow_store.py
  |                 |-- publish_event()             --> events.py
  |                 |-- strategy.execute()          --> saga_strategy.py / tpc_strategy.py
  |                       |-- retry_forward/forever --> retry.py
  |                       |-- store.transition()
  |                       |-- store.mark_step_done()
  |-- recover_incomplete_workflows(db, engine)      --> recovery.py
  |     |-- engine.resume(id, definition, ctx)
  |-- compensation_consumer(db, engine)             --> consumers.py
        |-- engine.resume(id, definition, ctx)
```

---

## What Was Deleted in v3.0

| Deleted | Replaced By |
|---------|-------------|
| `saga.py` (180 lines) | `saga_strategy.py` + `workflow_store.py` |
| `tpc.py` (167 lines) | `tpc_strategy.py` + `workflow_store.py` |
| `run_checkout()` in grpc_server.py (~160 lines) | `engine.execute()` + `SagaStrategy` |
| `run_2pc_checkout()` in grpc_server.py (~100 lines) | `engine.execute()` + `TwoPhaseStrategy` |
| `run_compensation()` in grpc_server.py | `engine.resume()` via consumers.py |
| `recover_incomplete_sagas()` in recovery.py | `recover_incomplete_workflows()` |
| `recover_incomplete_tpc()` in recovery.py | `recover_incomplete_workflows()` |
| `test_saga.py` (485 lines) | `test_strategies.py` + `test_workflow_engine.py` |
| `test_tpc.py` (169 lines) | `test_strategies.py` + `test_workflow_engine.py` |

**Net result:** `grpc_server.py` went from 501 lines to 69 lines. `recovery.py` went from 316 lines to 82 lines. Over 1,000 lines of duplicated orchestration logic were replaced by a clean, testable engine with 97 passing tests.
