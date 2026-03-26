# Technology Stack

**Project:** DDS26-8 v3.0 — Abstract Workflow Engine & Refactoring
**Researched:** 2026-03-26
**Confidence:** HIGH (pure codebase analysis — no new packages, no version questions)

---

## Key Finding: Zero New Dependencies (Again)

The existing stack provides everything needed for v3.0. The abstract workflow engine is a **pure application-layer refactoring**: move hardcoded SAGA/2PC logic from `grpc_server.py` into a generic engine that accepts workflow definitions as data structures. The engine itself uses Python `dataclasses`, `typing.Protocol`, and `asyncio` — all part of Python's standard library.

This is the correct outcome because:
1. **Workflow step definitions** are `@dataclass` structs holding references to async callables — no framework needed
2. **Engine state persistence** uses the same Redis hash + Lua CAS pattern already proven in `saga.py` and `tpc.py`
3. **Execution strategies** (SAGA compensation vs 2PC prepare/commit/abort) are engine-level concerns that dispatch to the same transport functions in `transport.py`
4. **Step registration** is a Python `dict` or class-level list — no registry library, no plugin system

---

## Existing Stack (Unchanged for v3.0)

| Technology | Version | Current Use | v3.0 Role |
|------------|---------|-------------|-----------|
| Python | 3.13 | All services | `dataclasses`, `typing.Protocol`, `typing.TypeVar` used for engine API design |
| redis[hiredis] | 5.0.3 | SAGA/2PC state, Lua CAS, Streams | Engine state persistence via same Redis hash + Lua CAS pattern |
| quart | 0.20.0 | HTTP API, background tasks | Unchanged |
| uvicorn | 0.34.0 | ASGI server | Unchanged |
| grpcio | 1.78.0 | gRPC transport (fallback) | Unchanged |
| protobuf | >=6.31.1 | gRPC message definitions | Unchanged |
| msgspec | 0.18.6 | JSON/msgpack serialization | Serializing engine context/step results to Redis |
| circuitbreaker | 2.1.3 | Fault tolerance on transport calls | Unchanged — wrapped around step action callables |

No new PyPI packages. No version bumps required.

---

## What NOT to Add

| Rejected Addition | Why Not |
|-------------------|---------|
| **temporalio** (Temporal Python SDK) | Full Temporal requires a running Temporal server — not deployable in the course's Docker Compose / Kubernetes setup. The *concepts* (workflow + activities separation, state persistence) are what to copy, not the library. |
| **prefect / airflow / celery** | Job orchestration frameworks built for data pipelines or task queues — bring their own brokers, databases, and UI. Massive overkill and incompatible with the Redis-only infrastructure constraint. |
| **python-statemachine** | Adds a state machine library on top of the custom Lua CAS transition pattern that's already proven and battle-tested under the benchmark. An ORM over your state machine is worse than the hand-rolled version here. |
| **transitions** (FSM library) | Same objection as python-statemachine. The existing `VALID_TRANSITIONS` dict + Lua CAS is simpler, more performant, and already tested. |
| **tenacity / stamina** | Already rejected in v1.0. `retry_forever` and `retry_forward` in `grpc_server.py` are simple, well-understood, and carry forward unchanged into the engine. |
| **pydantic** | `dataclasses` + `msgspec` already handle validation and serialization. Pydantic adds 60ms import overhead with zero benefit here. |
| **typing_extensions** | Python 3.13 ships `Protocol`, `TypeVar`, `dataclasses`, `TypedDict` natively. No backport needed. |

---

## Implementation Patterns Using Existing Stack

### Core Pattern: Workflow Definition as Data Structure

The engine decouples *workflow definition* (what steps to run, in what order, with what compensations) from *workflow execution* (how to run them, retry them, persist state). The checkout logic becomes a definition; the engine becomes generic infrastructure.

```python
# orchestrator/engine/types.py  — pure Python stdlib, zero imports from outside
from dataclasses import dataclass, field
from typing import Protocol, Awaitable, Any

class StepFn(Protocol):
    """A step action or compensation: accepts a context dict, returns success dict."""
    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]: ...

@dataclass
class WorkflowStep:
    name: str                        # e.g. "reserve_stock", "charge_payment"
    action: StepFn                   # forward callable: reserve stock, charge payment
    compensation: StepFn | None      # reverse callable: release stock, refund payment
    idempotency_key_fn: callable     # derives key from (workflow_id, step_name) -> str

@dataclass
class WorkflowDefinition:
    name: str                        # e.g. "checkout"
    steps: list[WorkflowStep]        # ordered list; compensation runs in reverse
    strategy: str = "saga"           # "saga" or "2pc"
```

**Why this design:**
- `StepFn` as a `Protocol` means existing callables from `transport.py` satisfy the interface without modification — structural subtyping, no inheritance required
- `WorkflowStep` wraps what `run_checkout()` already does inline: action + compensation as a paired unit
- `WorkflowDefinition` is how Temporal/Cadence model it: a workflow is a named, ordered sequence of activities

### Core Pattern: Generic Engine Execution

```python
# orchestrator/engine/executor.py
class WorkflowEngine:
    def __init__(self, db):
        self._db = db
        self._definitions: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        """Register a workflow definition by name."""
        self._definitions[definition.name] = definition

    async def execute(self, workflow_name: str, workflow_id: str, ctx: dict) -> dict:
        """Execute a registered workflow. Returns {success, error_message}."""
        defn = self._definitions[workflow_name]
        if defn.strategy == "saga":
            return await self._execute_saga(defn, workflow_id, ctx)
        elif defn.strategy == "2pc":
            return await self._execute_2pc(defn, workflow_id, ctx)
        raise ValueError(f"Unknown strategy: {defn.strategy}")
```

The engine's `_execute_saga` replaces the body of `run_checkout()` in `grpc_server.py`, but operates on a generic `WorkflowDefinition` rather than hardcoded step logic. The engine calls `step.action(ctx)`, transitions state using the existing Lua CAS pattern, and calls `step.compensation(ctx)` in reverse order on failure.

### State Persistence Pattern: Generalized Redis Hash

The engine persists workflow state with the same Redis hash + Lua CAS approach already validated by `saga.py` and `tpc.py`. The key difference: state field names are engine-generated from step names, not hardcoded.

```
Redis key:  {workflow:<workflow_id>}
Fields:
  state          — engine state: STARTED | STEP_N_DONE | COMPLETED | COMPENSATING | FAILED
  workflow_name  — "checkout"
  strategy       — "saga" or "2pc"
  context_json   — msgspec.json encoded context dict (items, user_id, total_cost, etc.)
  step_<name>    — "done" | "compensated" per step (idempotency flags)
  error_message  — set on failure
  started_at     — unix timestamp
  updated_at     — unix timestamp (updated by Lua transition)
```

The Lua CAS script from `saga.py` is **reused verbatim** — it operates on any hash key and is not SAGA-specific. The engine uses the same `TRANSITION_LUA` constant.

```python
# engine/state.py — reuses the existing Lua CAS pattern
TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""
# This is literally the same script as in saga.py and tpc.py — extract to shared module
```

**Key decision:** Extract `TRANSITION_LUA` to a single `engine/state.py` module. Both `saga.py` and `tpc.py` duplicate this script today — that duplication is the refactoring target.

### Workflow Registration Pattern

Checkout registers itself as a workflow at startup, not hardcoded in `grpc_server.py`:

```python
# orchestrator/workflows/checkout.py — the definition, not the engine
from engine.types import WorkflowDefinition, WorkflowStep
from transport import reserve_stock, release_stock, charge_payment, refund_payment

def make_checkout_workflow(items: list[dict], user_id: str, total_cost: int) -> WorkflowDefinition:
    """
    Build a checkout WorkflowDefinition from order data.
    Steps are closures capturing the specific items/user for this order.
    """
    steps = []
    for item in items:
        iid, qty = item["item_id"], item["quantity"]
        steps.append(WorkflowStep(
            name=f"reserve_stock_{iid}",
            action=lambda ctx, i=iid, q=qty: reserve_stock(i, q, ctx["idempotency_keys"][f"reserve_{i}"]),
            compensation=lambda ctx, i=iid, q=qty: release_stock(i, q, ctx["idempotency_keys"][f"release_{i}"]),
            idempotency_key_fn=lambda wid, sname, i=iid: f"{{saga:{wid}}}:step:reserve:{i}",
        ))
    steps.append(WorkflowStep(
        name="charge_payment",
        action=lambda ctx: charge_payment(ctx["user_id"], ctx["total_cost"], ctx["idempotency_keys"]["charge"]),
        compensation=lambda ctx: refund_payment(ctx["user_id"], ctx["total_cost"], ctx["idempotency_keys"]["refund"]),
        idempotency_key_fn=lambda wid, _: f"{{saga:{wid}}}:step:charge",
    ))
    return WorkflowDefinition(name="checkout", steps=steps, strategy="saga")
```

This is the critical abstraction: checkout logic moves from `grpc_server.py` into a workflow definition, and the engine is strategy-agnostic.

### Execution Strategy Separation

The engine dispatches to strategy-specific execution based on `WorkflowDefinition.strategy`:

| Strategy | Engine Method | What It Does |
|----------|--------------|--------------|
| `"saga"` | `_execute_saga(defn, wf_id, ctx)` | Sequential forward steps, reverse compensation on failure, exponential backoff retry |
| `"2pc"` | `_execute_2pc(defn, wf_id, ctx)` | Concurrent prepare phase via `asyncio.gather`, WAL-persist commit/abort decision, parallel phase-2 |

Both strategies reuse the existing `retry_forward()` and `retry_forever()` utilities — those are extracted to `engine/retry.py` (currently duplicated in `grpc_server.py`).

### Recovery Integration

The existing `recovery.py` scanner (`recover_incomplete_sagas`, `recover_incomplete_tpc`) becomes `engine/recovery.py`:

```python
# engine/recovery.py — generic scanner, replaces service-specific scanners
async def recover_incomplete_workflows(db, engine: WorkflowEngine) -> None:
    """Scan for non-terminal workflow records and resume them."""
    async for key in db.scan_iter(match="{workflow:*", count=100):
        raw = await db.hgetall(key)
        if not raw:
            continue
        record = {k.decode(): v.decode() for k, v in raw.items()}
        if record.get("state") not in NON_TERMINAL_STATES:
            continue
        await engine.resume(record)  # engine dispatches to strategy-specific resume
```

The current duplication between `recover_incomplete_sagas` and `recover_incomplete_tpc` collapses into one function that dispatches through the engine.

---

## File Structure for New Engine Module

```
orchestrator/
  engine/
    __init__.py         — exports WorkflowEngine, WorkflowDefinition, WorkflowStep
    types.py            — dataclasses: WorkflowStep, WorkflowDefinition, StepResult
    state.py            — Redis hash CRUD + TRANSITION_LUA (extracted from saga.py / tpc.py)
    executor.py         — WorkflowEngine class: register(), execute(), resume()
    strategies/
      __init__.py
      saga.py           — _execute_saga(), run_compensation() (extracted from grpc_server.py)
      tpc.py            — _execute_2pc() (extracted from grpc_server.py)
    retry.py            — retry_forever(), retry_forward() (extracted from grpc_server.py)
    recovery.py         — recover_incomplete_workflows() (replaces recovery.py)
  workflows/
    __init__.py
    checkout.py         — make_checkout_workflow() definition (the ONLY checkout-specific file)
```

**What gets deleted or gutted:**
- `orchestrator/saga.py` — replaced by `engine/state.py` + `engine/strategies/saga.py`
- `orchestrator/tpc.py` — replaced by `engine/state.py` + `engine/strategies/tpc.py`
- `orchestrator/recovery.py` — replaced by `engine/recovery.py`
- `orchestrator/grpc_server.py` — `run_checkout()` and `run_2pc_checkout()` move into strategy modules; `OrchestratorServiceServicer` calls `engine.execute()` instead

---

## Serialization: msgspec for Context Persistence

The workflow context dict (items, user_id, total_cost, idempotency keys) is persisted to Redis as `context_json`. Use `msgspec.json.encode/decode` — already a dependency, already used for event payloads:

```python
# engine/state.py
import msgspec.json

async def create_workflow_record(db, workflow_id: str, workflow_name: str,
                                  strategy: str, ctx: dict) -> bool:
    key = f"{{workflow:{workflow_id}}}"
    created = await db.hsetnx(key, "state", "STARTED")
    if not created:
        return False
    now = str(int(time.time()))
    await db.hset(key, mapping={
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "strategy": strategy,
        "context_json": msgspec.json.encode(ctx).decode(),
        "started_at": now,
        "updated_at": now,
    })
    await db.expire(key, 7 * 24 * 3600)
    return True
```

**Why msgspec over json stdlib:** Already in requirements. 3-10x faster encode/decode than stdlib json. Consistent with how all other services serialize payloads. No reason to switch.

---

## Typing Conventions for the Engine API

Using Python 3.13 standard library types — no `typing_extensions` needed:

```python
# engine/types.py
from dataclasses import dataclass, field
from typing import Protocol, Any, runtime_checkable

@runtime_checkable
class StepFn(Protocol):
    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]: ...

type WorkflowId = str    # Python 3.12+ type alias syntax
type StepName = str
```

**Why `Protocol` not `ABC`:** Step functions are plain async functions or lambdas — they should satisfy the interface without inheriting from anything. Protocol's structural subtyping means the existing transport functions (`reserve_stock`, `charge_payment`) satisfy `StepFn` without modification. ABC would require wrapping every transport function.

---

## Integration Points: What Changes vs. What Stays

### Code That Changes

| File | Change | Approach |
|------|--------|----------|
| `orchestrator/grpc_server.py` | `run_checkout()`, `run_2pc_checkout()` move to engine strategies | Servicer calls `engine.execute("checkout", order_id, ctx)` |
| `orchestrator/saga.py` | Gutted — logic extracted to `engine/` | Delete file after extraction |
| `orchestrator/tpc.py` | Gutted — logic extracted to `engine/` | Delete file after extraction |
| `orchestrator/recovery.py` | Replaced by `engine/recovery.py` | Single generic scanner replaces two duplicates |
| `orchestrator/app.py` | Create engine, register checkout workflow, call engine recovery | Minor additions to `startup()` |

### Code That Does NOT Change

| Component | Reason Unchanged |
|-----------|-----------------|
| `transport.py` | Transport adapter is already abstract — engine calls the same functions |
| `client.py` + `queue_client.py` | Transport implementations — untouched |
| `events.py` + `consumers.py` | Event publishing and audit consumer — remain outside engine |
| All `requirements.txt` files | Zero new dependencies |
| Proto definitions + gRPC stubs | gRPC layer unaffected |
| Stock / Payment services | No changes to domain services for this milestone |
| Docker Compose / Kubernetes configs | No infrastructure changes |
| External HTTP API (Order service routes) | API contract unchanged |

---

## What the Refactoring Does NOT Include

Based on PROJECT.md out-of-scope items, explicitly avoid:

| Anti-scope | Why Excluded |
|------------|-------------|
| Full event sourcing (append-only event log) | Adds a new infrastructure concern for zero grade benefit |
| Workflow versioning (Temporal-style) | Complex to implement correctly in 6 days; not needed for course |
| Signals, queries, child workflows | Temporal features beyond what course requires |
| Plugin/dynamic loading of workflow definitions | Over-engineering; only one workflow (checkout) exists |
| Replacing Redis hash state with event log | Breaks startup recovery scanner; no benefit |

---

## Confidence Assessment

| Claim | Confidence | Basis |
|-------|------------|-------|
| No new PyPI dependencies needed | HIGH | Full codebase analysis — every engine primitive maps to existing stdlib or dependencies |
| `Protocol` structural subtyping works for `StepFn` | HIGH | Python 3.13 stdlib — verified `Protocol` works with async `__call__` since Python 3.8 |
| Lua CAS script is reusable verbatim | HIGH | Script in `saga.py` and `tpc.py` are identical — trivially extractable |
| `msgspec.json` sufficient for context serialization | HIGH | Already used for stream message payloads in `queue_client.py`; handles dict serialization |
| `dataclasses` sufficient for `WorkflowStep` / `WorkflowDefinition` | HIGH | Pure data holders; no ORM, no validation beyond type hints needed |
| Lambda closures work for step action/compensation | MEDIUM | Standard Python closure semantics — but requires careful `i=iid` default arg binding to avoid late-binding bugs. Known pattern; must be applied consistently. |
| Engine recovery can replace both SAGA and TPC scanners | HIGH | Both scanners use identical scan_iter + hgetall + state-check pattern; differ only in dispatch logic |

---

## Installation

```bash
# No changes. Existing requirements.txt is sufficient for v3.0.
# All engine primitives are Python stdlib (dataclasses, typing, asyncio) or already-installed packages.
```

---

## Sources

- Codebase analysis: `orchestrator/saga.py`, `orchestrator/tpc.py`, `orchestrator/grpc_server.py`, `orchestrator/recovery.py`, `orchestrator/transport.py`, all `requirements.txt`
- [Temporal Workflow Engine Design Principles](https://temporal.io/blog/workflow-engine-principles) — state persistence model, workflow-as-state-machine concept
- [Python Protocol structural subtyping (PEP 544)](https://peps.python.org/pep-0544/) — async `__call__` Protocol for step functions
- [Temporal Saga compensation pattern](https://temporal.io/blog/compensating-actions-part-of-a-complete-breakfast-with-sagas) — workflow step + compensation pairing
- [Python dataclasses stdlib docs](https://docs.python.org/3/library/dataclasses.html) — `@dataclass` for `WorkflowStep` / `WorkflowDefinition`

---

*Stack research for: DDS26-8 v3.0 Abstract Workflow Engine*
*Researched: 2026-03-26*
*Stack conclusion: Zero new dependencies. v3.0 is a pure application-code refactoring using Python stdlib (dataclasses, Protocol, asyncio) and the already-proven Redis hash + Lua CAS persistence pattern.*
