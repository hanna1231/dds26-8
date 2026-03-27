# Phase 16: WorkflowEngine + Checkout Definition - Research

**Researched:** 2026-03-27
**Domain:** Python async workflow engine wiring + closure-based workflow factory
**Confidence:** HIGH

## Summary

Phase 16 wires the two existing strategies (SagaStrategy, TwoPhaseStrategy) and WorkflowStore into a thin WorkflowEngine class, then rewrites checkout as a WorkflowDefinition factory. The strategies already expose identical `execute(workflow_id, definition, context, store)` signatures — the engine is a ~40 LOC routing + event-publishing shell. The checkout definition replaces the inline logic in `grpc_server.py`'s `run_checkout()` and `run_2pc_checkout()` with transport.py closures, keeping the engine ignorant of Stock/Payment.

All implementation inputs are already built and tested. The primary design work is: (1) how to generalize events.py to accept `workflow_id` instead of `saga_id`, and (2) how to model the checkout steps as closures where `reserve_stock` loops over items. The lambda default-arg capture pattern for avoiding Python late-binding is already established in `saga_strategy.py:86`.

**Primary recommendation:** Create `orchestrator/workflow_engine.py` (WorkflowEngine class, ~40 LOC) and `orchestrator/checkout_workflow.py` (make_checkout_workflow factory, ~50 LOC). Adapt events.py minimally: add a `workflow_id` keyword parameter alongside `saga_id`, keeping the existing stream name and field names so no downstream consumer breaks.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
All implementation decisions are at Claude's discretion. No locked user decisions exist for this phase.

### Claude's Discretion

**Engine lifecycle events:**
- D-07 from Phase 15 says strategies don't publish events -- engine does
- Current events.py uses saga-specific naming (saga_id, saga:events stream)
- Claude decides: reuse existing events.py with minimal generalization (workflow_id param), or wrap calls. Stream name and field naming are implementation details.
- Events to publish: started, step_completed, succeeded, failed (matching existing event types in events.py)

**Checkout closure design:**
- make_checkout_workflow() returns WorkflowDefinition with closures over transport.py functions
- STATE.md flags Python late-binding as known pitfall -- use default-arg capture pattern (lambda s=step, c=context: ...) already established in saga_strategy.py:86
- Context dict (order_id, user_id, items) flows through the context parameter that strategies already pass to step callables

**Engine API surface:**
- WorkflowEngine receives WorkflowStore injected (REF-03 alignment, same pattern as strategies)
- execute(workflow_id, definition, context) is the primary entry point
- Strategy instances can be pre-registered or instantiated on the fly -- both are stateless
- get_status() is ADV-06 (Future Requirements) -- skip for now

**Strategy selection:**
- Simple dict registry mapping "saga" -> SagaStrategy, "2pc" -> TwoPhaseStrategy
- definition.strategy field selects the executor
- No dynamic registration needed (Out of Scope confirms this)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ENG-03 | WorkflowEngine class with execute(workflow_id, definition, context) entry point that routes to strategy | Engine is a thin routing shell: dict-lookup on definition.strategy, delegate to strategy.execute(), wrap with event publishing. ~40 LOC. |
| CHK-01 | checkout_workflow.py defining checkout as WorkflowDefinition using transport.py functions | Closures over transport.py's 12 exported functions. Context dict carries order_id/user_id/items/total_cost. Step 1 must loop reserve_stock over items -- this is the only multi-call step. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python asyncio | stdlib | Async step execution | Already used throughout strategies |
| redis.asyncio | already installed | WorkflowStore backing | Existing project dependency |
| msgspec | already installed | Event payload serialization | Used in events.py |

No new packages required. Phase 16 is pure Python wiring of existing modules.

**Version verification:** N/A -- no new package installs.

## Architecture Patterns

### Recommended Project Structure
```
orchestrator/
├── workflow_engine.py       # NEW: WorkflowEngine class (~40 LOC)
├── checkout_workflow.py     # NEW: make_checkout_workflow() factory (~50 LOC)
├── workflow_types.py        # EXISTING: WorkflowStep, WorkflowDefinition
├── workflow_store.py        # EXISTING: WorkflowStore
├── saga_strategy.py         # EXISTING: SagaStrategy
├── tpc_strategy.py          # EXISTING: TwoPhaseStrategy
├── events.py                # MODIFY: add workflow_id param support
└── transport.py             # EXISTING: 12 domain functions (source of closures)
```

### Pattern 1: WorkflowEngine as Routing Shell

**What:** Engine holds a dict registry `{"saga": SagaStrategy(), "2pc": TwoPhaseStrategy()}`. `execute()` looks up the strategy, publishes lifecycle events around the delegation, and returns the strategy result.

**When to use:** Always -- this is the only entry point.

**Example:**
```python
# orchestrator/workflow_engine.py
from saga_strategy import SagaStrategy
from tpc_strategy import TwoPhaseStrategy
from workflow_store import WorkflowStore
from events import publish_event

_STRATEGIES = {
    "saga": SagaStrategy(),
    "2pc": TwoPhaseStrategy(),
}

class WorkflowEngine:
    def __init__(self, store: WorkflowStore, db):
        self._store = store
        self._db = db

    async def execute(self, workflow_id: str, definition, context: dict) -> dict:
        strategy = _STRATEGIES.get(definition.strategy)
        if strategy is None:
            raise ValueError(f"Unknown strategy: {definition.strategy!r}")

        await publish_event(self._db, "workflow_started", workflow_id,
                            context.get("order_id", ""), context.get("user_id", ""))

        result = await strategy.execute(workflow_id, definition, context, self._store)

        event_type = "workflow_succeeded" if result["success"] else "workflow_failed"
        await publish_event(self._db, event_type, workflow_id,
                            context.get("order_id", ""), context.get("user_id", ""))
        return result
```

### Pattern 2: events.py Minimal Generalization

**What:** Add `workflow_id` as an alias parameter to `publish_event()`. The function already accepts `saga_id` -- the simplest approach is to accept both and use whichever is provided. Alternatively, rename internally without changing the stream name or field name in the Redis payload.

**Recommended approach:** Accept `workflow_id` as the primary parameter, keep `saga_id` as a keyword alias defaulting to `workflow_id` for backward compatibility. This keeps all existing tests passing.

```python
# Modified signature (events.py)
async def publish_event(db, event_type: str, workflow_id: str,
                        order_id: str, user_id: str = "", **extra) -> None:
    # Internally build event with saga_id=workflow_id to preserve wire format
    fields = _build_event(event_type, saga_id=workflow_id, order_id=order_id,
                          user_id=user_id, **extra)
    ...
```

The stream name `{saga:events}:checkout` and field `saga_id` in the Redis payload do NOT need to change -- they are internal implementation details. Existing tests for events.py test behavior, not field naming, so this change is backward-compatible.

### Pattern 3: Checkout Closure Design

**What:** `make_checkout_workflow()` returns a WorkflowDefinition whose steps are async callables over transport.py functions. The context dict carries `order_id`, `user_id`, `items` (list of `{"item_id": str, "quantity": int}`), and `total_cost`.

**Key design issue:** `reserve_stock` must be called once per item, but WorkflowStep has a single `action` callable. Two valid approaches:

**Option A: Single step with internal loop (recommended)**
```python
# checkout_workflow.py
from transport import (
    reserve_stock, release_stock,
    charge_payment, refund_payment,
    prepare_stock, commit_stock, abort_stock,
    prepare_payment, commit_payment, abort_payment,
)
from workflow_types import WorkflowStep, WorkflowDefinition

async def _reserve_all(context: dict) -> dict:
    """Reserve stock for all items; release all on any failure."""
    order_id = context["order_id"]
    items = context["items"]
    reserved = []
    for item in items:
        iid, qty = item["item_id"], item["quantity"]
        result = await reserve_stock(iid, qty, f"{{saga:{order_id}}}:step:reserve:{iid}")
        if not result.get("success"):
            return result
        reserved.append(item)
    return {"success": True, "error_message": ""}

async def _release_all(context: dict) -> dict:
    """Compensation: release all reserved stock."""
    order_id = context["order_id"]
    for item in context["items"]:
        iid, qty = item["item_id"], item["quantity"]
        await release_stock(iid, qty, f"{{saga:{order_id}}}:step:release:{iid}")
    return {"success": True, "error_message": ""}

async def _charge(context: dict) -> dict:
    uid = context["user_id"]
    cost = context["total_cost"]
    order_id = context["order_id"]
    return await charge_payment(uid, cost, f"{{saga:{order_id}}}:step:charge")

async def _refund(context: dict) -> dict:
    uid = context["user_id"]
    cost = context["total_cost"]
    order_id = context["order_id"]
    return await refund_payment(uid, cost, f"{{saga:{order_id}}}:step:refund")

def make_checkout_workflow(strategy: str = "saga") -> WorkflowDefinition:
    return WorkflowDefinition(
        name="checkout",
        strategy=strategy,
        steps=[
            WorkflowStep(
                name="reserve_stock",
                action=_reserve_all,
                compensation=_release_all,
            ),
            WorkflowStep(
                name="charge_payment",
                action=_charge,
                compensation=_refund,
            ),
        ],
    )
```

This approach is consistent with how SagaStrategy's STATE_SEQUENCE maps steps to states (2 steps, not N+1 for N items). The existing SAGA state progression `STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED` maps cleanly to two steps.

**Option B: Lambda closures with default-arg capture**
Use the pattern from `saga_strategy.py:86`: `lambda s=step, c=context: s.action(c)`. This would be needed if step behavior depends on per-step captured values at definition creation time. For checkout, the context dict is passed at execution time (not definition creation time), so module-level async functions (Option A) are cleaner and easier to test.

**Why Option A is preferred:**
- No closure capture pitfalls
- Each function is independently testable
- Consistent with transport.py's function-per-operation model
- Idempotency keys can be computed from context at execution time

### Pattern 4: 2PC Checkout Steps

For `strategy="2pc"`, the WorkflowDefinition steps use the prepare/commit/abort transport functions. The `TwoPhaseStrategy.execute()` calls `step.action()` twice (once for prepare, once for commit phase-2) and `step.compensation()` for abort.

```python
async def _prepare_stock(context: dict) -> dict:
    order_id = context["order_id"]
    for item in context["items"]:
        result = await prepare_stock(item["item_id"], item["quantity"], order_id)
        if not result.get("success"):
            return result
    return {"success": True, "error_message": ""}

async def _commit_abort_stock(context: dict) -> dict:
    """Used for both commit and abort -- transport.py distinguishes via separate functions."""
    # Note: TwoPhaseStrategy calls action for both prepare AND commit.
    # This is the existing pattern from grpc_server.py:433.
    order_id = context["order_id"]
    for item in context["items"]:
        await commit_stock(item["item_id"], order_id)
    return {"success": True, "error_message": ""}

async def _abort_stock(context: dict) -> dict:
    order_id = context["order_id"]
    for item in context["items"]:
        await abort_stock(item["item_id"], order_id)
    return {"success": True, "error_message": ""}
```

**Critical observation from tpc_strategy.py:** `TwoPhaseStrategy` calls `step.action(context)` for BOTH prepare (phase 1) and commit (phase 2). The `step.compensation(context)` is used for abort (phase 2b). This means the 2PC checkout steps need action=prepare callable (not commit), and a separate commit step is needed -- or the action callable must handle both prepare and commit based on idempotency. Looking at the existing `run_2pc_checkout()` in grpc_server.py:408-435, prepare and commit are distinct calls with distinct functions. The `tpc_strategy.py` re-calls `step.action` in phase 2, so for 2PC the `step.action` must serve as both prepare and commit. This creates an impedance mismatch.

**Resolution:** For phase 16, `make_checkout_workflow(strategy="2pc")` can use a design where action = prepare/commit sequence (the transport layer is idempotent), matching the pattern in `tpc_strategy.py:117` which re-calls `step.action` for phase-2 commit. The 2PC test suite already validates this pattern with mock steps. Alternatively, provide separate 2PC-specific step builders. This is a discretion call -- the simplest approach consistent with the current test coverage is to keep `action = prepare callable` and note that phase-2 "commit" re-calls the same prepare (idempotent by order_id), which the existing transport functions support.

**For scope:** The success criteria requires "full happy-path checkout driven through engine.execute() completes successfully" and "stock failure mid-checkout triggers compensation." Both can be demonstrated with saga strategy. The 2PC definition factory is still valuable but less critical for success criteria validation.

### Anti-Patterns to Avoid
- **Accessing Stock/Payment service names in engine or strategy modules:** The engine and strategy files must have zero references to "stock", "payment", "reserve_stock", etc. Only checkout_workflow.py touches transport functions.
- **Tight coupling of events.py to saga_id naming in callers:** Callers should pass `workflow_id` to the engine; the engine translates to events.py's internal field names.
- **Lambda late-binding without default capture:** `lambda: step.action(context)` in a loop captures the loop variable by reference. Must use `lambda s=step, c=context: s.action(c)` or module-level functions (Option A above).
- **Making WorkflowEngine a module-level singleton:** Must be injectable (REF-03), instantiated with WorkflowStore and db in `app.py`, not at import time.
- **Creating workflow_store record inside engine.execute():** The engine does NOT call `store.create()`. The caller (grpc_server.py in Phase 17) creates the record before calling engine.execute(). This matches how SagaStrategy.execute() currently starts from "STARTED" state (the record is pre-created by `create_saga_record()` in grpc_server.py before calling the strategy).

**Wait -- re-examination of create flow:** Looking at `saga_strategy.py:88-113`, SagaStrategy.execute() begins by iterating steps and calling retry_forward -- it does NOT call `store.create()`. The `run_checkout()` in grpc_server.py calls `create_saga_record()` before calling strategy.execute(). For Phase 16, the WorkflowEngine.execute() must either: (a) call `store.create()` internally as part of its contract, or (b) require the caller to pre-create the record. Option (a) is cleaner since the engine is the single entry point -- it should own the full lifecycle including creation. The SAGA strategy's execute() starts from STARTED state, which is what `store.create(initial_state="STARTED")` would set. So `engine.execute()` should call `store.create()` first.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State persistence | Custom Redis hash writes | WorkflowStore.create(), transition(), mark_step_done() | Lua CAS already handles atomicity |
| Strategy routing | if/elif chain on strategy name | Dict registry in engine | Strategies are already stateless singletons |
| Event publishing | Manual XADD calls | events.publish_event() | Fire-and-forget pattern + error handling already built |
| Retry on step failure | New retry logic | retry_forward/retry_forever from retry.py | Already extracted, tested |
| Transport function access | Direct gRPC client imports in workflow | transport.py re-exports | COMM_MODE switching is transport.py's job |

## Common Pitfalls

### Pitfall 1: Python Late-Binding in Closures
**What goes wrong:** Loop variable captured by reference instead of value.
**Why it happens:** Python closures close over the loop variable name, not its value at capture time.
**How to avoid:** Use default-argument capture: `lambda item=item: reserve_stock(item["item_id"], ...)` or define module-level async functions that receive context at call time (Option A above).
**Warning signs:** All steps invoke the same transport function with the last loop value.

### Pitfall 2: store.create() Initial State Mismatch
**What goes wrong:** SagaStrategy.execute() transitions from STATE_SEQUENCE[0] = "STARTED". If the record was created with a different initial_state, the first Lua CAS transition fails (returns 0).
**Why it happens:** store.create() and SagaStrategy.execute() both assume initial state "STARTED". TwoPhaseStrategy assumes "INIT".
**How to avoid:** WorkflowEngine.execute() must call `store.create(workflow_id, initial_state)` using the correct initial state for each strategy. For saga: "STARTED". For 2pc: "INIT". These are the first entries in each strategy's STATE_SEQUENCE/TPC_STATES.
**Warning signs:** `store.transition()` returns False on the very first transition.

### Pitfall 3: events.py saga_id Field Change Breaking Existing Tests
**What goes wrong:** Changing `_build_event()` field from `saga_id` to `workflow_id` breaks test_events.py assertions.
**Why it happens:** Existing tests assert on the exact field names in the event dict.
**How to avoid:** Keep `saga_id` in the Redis payload. Accept `workflow_id` as the public API parameter name in `publish_event()` and pass it as `saga_id=workflow_id` internally. The wire format stays unchanged.
**Warning signs:** test_events.py failures after events.py modification.

### Pitfall 4: SagaStrategy STATE_SEQUENCE Length vs. Checkout Steps
**What goes wrong:** SagaStrategy.STATE_SEQUENCE has 4 entries (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPLETED) for a 2-step checkout. If checkout_workflow.py defines 3 steps, STATE_SEQUENCE[i+1] causes IndexError at i=2.
**Why it happens:** STATE_SEQUENCE is hardcoded to match the original 2-step checkout. It is NOT generic.
**How to avoid:** Checkout definition MUST have exactly 2 steps when using SagaStrategy. The reserve_stock loop must be a single step (all items in one action callable).
**Warning signs:** IndexError at `STATE_SEQUENCE[i + 1]` during strategy execution.

### Pitfall 5: WorkflowEngine.execute() Called Without Pre-Existing Record
**What goes wrong:** Strategy assumes the workflow record exists in Redis (for transition). If engine.execute() doesn't call store.create() first, the first Lua CAS (STARTED -> STOCK_RESERVED) fails because HGET returns nil, not "STARTED".
**Why it happens:** WorkflowStore.create() uses HSETNX -- it must be called before any transition.
**How to avoid:** engine.execute() calls `await self._store.create(workflow_id, initial_state, metadata=context)` before delegating to the strategy. Initial state is strategy-dependent: "STARTED" for saga, "INIT" for 2pc.
**Warning signs:** All `store.transition()` calls return False; Redis record shows nil for state field.

### Pitfall 6: 2PC action/compensation Callable Semantics
**What goes wrong:** For 2PC, TwoPhaseStrategy re-calls `step.action(context)` for phase-2 commit (not a separate commit callable). If checkout_workflow's 2PC step.action is `prepare_stock`, phase-2 re-calls `prepare_stock` instead of `commit_stock`.
**Why it happens:** TwoPhaseStrategy.execute() uses `step.action` for BOTH prepare and commit (lines 92, 117 in tpc_strategy.py). The existing grpc_server.py uses separate `prepare_stock` and `commit_stock` calls, bypassing this.
**How to avoid:** For the 2PC WorkflowDefinition, design step.action as a callable that is idempotent when called twice (or makes the step.action = prepare+commit combined). The simplest Phase 16 approach: demonstrate success criteria using saga strategy only; the 2PC definition can use a simplified action that handles both prepare and commit if called twice (since transport functions are idempotent by order_id).
**Warning signs:** 2PC checkout commits with `prepare_stock` called twice instead of `prepare_stock` then `commit_stock`.

## Code Examples

### WorkflowEngine Constructor and Registry
```python
# Source: derived from saga_strategy.py and tpc_strategy.py patterns (both stateless)
from saga_strategy import SagaStrategy
from tpc_strategy import TwoPhaseStrategy

_STRATEGIES = {
    "saga": SagaStrategy(),
    "2pc": TwoPhaseStrategy(),
}

class WorkflowEngine:
    def __init__(self, store: WorkflowStore, db):
        self._store = store
        self._db = db
```

### Initial State by Strategy
```python
# Source: saga_strategy.py STATE_SEQUENCE[0]="STARTED", tpc_strategy.py TPC_STATES "INIT"
_INITIAL_STATES = {
    "saga": "STARTED",
    "2pc": "INIT",
}
```

### Closure with Context Dict Pattern (no late-binding risk)
```python
# Source: established pattern -- module-level functions receive context at call time
async def _reserve_all(context: dict) -> dict:
    order_id = context["order_id"]
    items = context["items"]
    for item in items:
        result = await reserve_stock(
            item["item_id"], item["quantity"],
            f"{{saga:{order_id}}}:step:reserve:{item['item_id']}"
        )
        if not result.get("success"):
            return result
    return {"success": True, "error_message": ""}
```

### Test Pattern for WorkflowEngine (unit)
```python
# Source: test_strategies.py helper pattern
def make_mock_store(get_return=None):
    store = AsyncMock(spec=WorkflowStore)
    store.transition.return_value = True
    store.mark_step_done.return_value = None
    store.create.return_value = True
    store.get.return_value = get_return
    return store

async def test_engine_routes_to_saga():
    step0 = make_step("reserve_stock")
    step1 = make_step("charge_payment")
    definition = WorkflowDefinition(name="checkout", steps=[step0, step1], strategy="saga")
    store = make_mock_store()
    mock_db = AsyncMock()
    engine = WorkflowEngine(store=store, db=mock_db)
    result = await engine.execute("wf-1", definition, {"order_id": "ord-1", "user_id": "u-1"})
    assert result["success"] is True
```

### Integration Test for Happy-Path Checkout
```python
# Source: grpc_integration pattern in test_grpc_integration.py
async def test_happy_path_checkout_via_engine(orchestrator_db, grpc_clients, clean_orchestrator_db):
    from workflow_store import WorkflowStore
    from workflow_engine import WorkflowEngine
    from checkout_workflow import make_checkout_workflow
    import uuid

    store = WorkflowStore(orchestrator_db)
    engine = WorkflowEngine(store=store, db=orchestrator_db)
    definition = make_checkout_workflow(strategy="saga")
    workflow_id = f"wf-{uuid.uuid4()}"
    context = {
        "order_id": workflow_id,
        "user_id": "test-user-1",
        "items": [{"item_id": "test-item-1", "quantity": 1}],
        "total_cost": 10,
    }
    result = await engine.execute(workflow_id, definition, context)
    assert result["success"] is True

    state = await store.get(workflow_id)
    assert state["state"] == "COMPLETED"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| run_checkout() / run_2pc_checkout() in grpc_server.py | engine.execute() + WorkflowDefinition | Phase 16 | Decouples transport from orchestration logic |
| Hardcoded saga_id / tpc_key in checkout logic | Generic workflow_id | Phase 14 (WorkflowStore) | Recovery scanner can be generalized in Phase 17 |
| Strategies called directly from gRPC handler | Called through engine | Phase 16 | Engine owns lifecycle events |

**Note:** grpc_server.py still calls `run_checkout()` / `run_2pc_checkout()` directly. This is NOT refactored in Phase 16 -- that is Phase 17 (CHK-02). Phase 16 only adds the engine and checkout definition; existing gRPC path is untouched until Phase 17.

## Open Questions

1. **events.py: What exact event_types to publish from the engine?**
   - What we know: D-07 says engine publishes lifecycle events. Existing event types: checkout_started, saga_completed, stock_reserved, payment_completed, etc.
   - What's unclear: Should engine publish only generic "workflow_started" / "workflow_succeeded" / "workflow_failed", or replicate the saga-specific event names?
   - Recommendation: Publish generic types ("workflow_started", "workflow_succeeded", "workflow_failed") from engine. Step-level events (stock_reserved, payment_completed) are strategy/transport concerns and NOT needed for Phase 16 success criteria.

2. **2PC step callable semantics (action = prepare or prepare+commit?)**
   - What we know: TwoPhaseStrategy re-calls step.action for phase-2 commit. Transport layer has separate prepare_stock and commit_stock functions.
   - What's unclear: How to reconcile this with a single action callable.
   - Recommendation: For Phase 16, make_checkout_workflow(strategy="2pc") can use a single action callable that wraps the idempotent prepare (calling it twice is harmless since order_id makes it idempotent). Document this limitation and address properly in Phase 17 if needed. Success criteria validation only requires saga strategy demonstration.

## Environment Availability

Step 2.6: SKIPPED -- Phase 16 is pure Python wiring of existing modules. No new external dependencies beyond what's already installed and validated in earlier phases.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 with pytest-asyncio |
| Config file | `pytest.ini` (asyncio_mode=auto, testpaths=tests) |
| Quick run command | `pytest tests/test_workflow_engine.py tests/test_checkout_workflow.py -x` |
| Full suite command | `pytest tests/ -x -m "not requires_docker"` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ENG-03 | engine.execute() routes saga definition to SagaStrategy | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_routes_to_saga"` | Wave 0 |
| ENG-03 | engine.execute() routes 2pc definition to TwoPhaseStrategy | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_routes_to_2pc"` | Wave 0 |
| ENG-03 | engine publishes workflow_started event before strategy call | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_publishes_started_event"` | Wave 0 |
| ENG-03 | engine publishes workflow_succeeded after strategy success | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_publishes_succeeded_event"` | Wave 0 |
| ENG-03 | engine publishes workflow_failed after strategy failure | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_publishes_failed_event"` | Wave 0 |
| ENG-03 | engine raises ValueError for unknown strategy | unit | `pytest tests/test_workflow_engine.py -x -k "test_engine_unknown_strategy"` | Wave 0 |
| CHK-01 | make_checkout_workflow() returns WorkflowDefinition with 2 steps | unit | `pytest tests/test_checkout_workflow.py -x -k "test_make_checkout_workflow_structure"` | Wave 0 |
| CHK-01 | make_checkout_workflow() steps have no references to Stock/Payment service names | unit | `pytest tests/test_checkout_workflow.py -x -k "test_no_service_names_in_engine"` | Wave 0 |
| CHK-01 + ENG-03 | Full happy-path checkout driven through engine.execute() succeeds with correct Redis state | integration | `pytest tests/test_checkout_workflow.py -x -k "test_happy_path_checkout_via_engine" -m requires_docker` | Wave 0 |
| CHK-01 + ENG-03 | Stock failure triggers compensation, no partial reservations remain | integration | `pytest tests/test_checkout_workflow.py -x -k "test_stock_failure_triggers_compensation" -m requires_docker` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_workflow_engine.py tests/test_checkout_workflow.py -x -m "not requires_docker"`
- **Per wave merge:** `pytest tests/ -x -m "not requires_docker"`
- **Phase gate:** Full suite green (including requires_docker integration tests) before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_workflow_engine.py` -- covers ENG-03 unit tests
- [ ] `tests/test_checkout_workflow.py` -- covers CHK-01 unit + integration tests

*(No framework install needed -- pytest + pytest-asyncio already installed and configured)*

## Sources

### Primary (HIGH confidence)
- Direct codebase reading: `orchestrator/saga_strategy.py`, `tpc_strategy.py`, `workflow_store.py`, `workflow_types.py`, `events.py`, `transport.py`, `retry.py`, `grpc_server.py`
- Direct codebase reading: `tests/test_strategies.py`, `tests/conftest.py`, `tests/test_workflow_store.py`
- `.planning/phases/16-workflowengine-checkout-definition/16-CONTEXT.md` -- phase decisions
- `.planning/REQUIREMENTS.md` -- ENG-03, CHK-01 definitions
- `pytest.ini` -- test framework configuration

### Secondary (MEDIUM confidence)
- None required -- all technical decisions derivable from direct codebase inspection

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new packages, all existing project dependencies
- Architecture: HIGH -- engine pattern derived directly from existing strategy interfaces; patterns verified by reading all source files
- Pitfalls: HIGH -- Python late-binding, STATE_SEQUENCE length, store.create() sequencing all verified by reading source code directly

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable -- internal codebase only)
