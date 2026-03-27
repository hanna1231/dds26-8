# Phase 17: Wiring - Research

**Researched:** 2026-03-27
**Domain:** Python async gRPC server wiring, startup recovery generalization, test compatibility
**Confidence:** HIGH

## Summary

Phase 17 wires the already-complete WorkflowEngine into the running system. Three files need changes: `grpc_server.py`, `recovery.py`, and `consumers.py`. The engine, strategies, store, and checkout definition are all working and tested from Phases 14-16. The work in this phase is mostly mechanical — replace three call sites — but several tests directly import and patch `run_checkout` / `run_2pc_checkout` from `grpc_server`, making those tests the most delicate surface.

The 37 "integration tests" referred to in the phase goal are the combined count from `test_saga.py`, `test_fault_tolerance.py`, `test_2pc_coordinator.py`, `test_grpc_integration.py`, and `test_tpc.py`. Many of them directly call or patch functions in `grpc_server.py`, `recovery.py`, and `saga.py`. After the wiring change, some tests will need updating to patch `engine.execute()` rather than `run_checkout()` — this is the primary risk.

The recovery scanner (`recovery.py`) currently uses `{saga:*}` and `{tpc:*}` key prefixes but the WorkflowStore uses `{workflow:*}`. This is a fundamental scope question: does recovery still scan the old prefix keys (because existing saga.py/tpc.py records persist), or does it switch to `{workflow:*}` keys? The answer is critical to whether the recovery tests break. Given that Phase 18 (cleanup) deletes saga.py and tpc.py, the safest Phase 17 path is to keep the recovery scanner scanning both prefixes, or to ensure grpc_server.py continues writing to `{workflow:*}` keys (which the engine already does) and recovery.py reads those. Currently recovery.py uses `get_saga()` / `get_tpc()` helpers that read `{saga:*}` / `{tpc:*}` keys. After wiring, the engine writes to `{workflow:*}` keys. Recovery must be updated to scan `{workflow:*}` keys.

**Primary recommendation:** Wire `grpc_server.py` to call `engine.execute()` by injecting `WorkflowEngine` into `OrchestratorServiceServicer` via constructor. Update `recovery.py` to scan `{workflow:*}` keys and call `engine.resume()` (a new method on `WorkflowEngine`). Update `consumers.py` to call engine compensation via engine API rather than importing `run_compensation` from `grpc_server`. Update the two `test_pattern_toggle_*` tests to patch `engine.execute()` rather than `run_checkout` / `run_2pc_checkout`.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CHK-02 | grpc_server.py refactored to receive WorkflowEngine and call engine.execute() only | WorkflowEngine.execute(workflow_id, definition, context) exists in workflow_engine.py. OrchestratorServiceServicer receives db in constructor — same pattern extends to receive engine. TRANSACTION_PATTERN env var selects strategy for make_checkout_workflow(). |
| CHK-03 | Recovery scanner generalized to read workflow state and resume via engine API | recovery.py currently scans {saga:*} and {tpc:*} keys. After wiring, engine writes to {workflow:*} keys. Recovery must scan {workflow:*} and call engine.resume(). engine.resume() does not yet exist — it must be added to WorkflowEngine. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python asyncio | stdlib | Async coordination | Already used throughout |
| redis.asyncio | installed | WorkflowStore backing, recovery scan | Existing project dependency |

No new packages required. Phase 17 is pure wiring of existing modules.

**Version verification:** N/A — no new package installs.

## Architecture Patterns

### Recommended Project Structure (changes only)

```
orchestrator/
├── grpc_server.py     MODIFY: inject WorkflowEngine, replace run_checkout/run_2pc_checkout
├── recovery.py        MODIFY: scan {workflow:*}, call engine.resume()
├── consumers.py       MODIFY: replace run_compensation import with engine API call
├── workflow_engine.py MODIFY: add resume() method for recovery path
└── app.py             MODIFY: construct WorkflowEngine + WorkflowStore, inject into servicer

tests/
├── conftest.py        MODIFY: inject engine into OrchestratorServiceServicer
├── test_2pc_coordinator.py  MODIFY: rewrite test_pattern_toggle_* to patch engine.execute()
├── test_fault_tolerance.py  REVIEW: test_run_checkout_compensates_on_circuit_breaker calls run_checkout directly -- must stay or be updated
└── test_saga.py       REVIEW: test_compensation_retries_until_success patches grpc_server.release_stock -- check if this still works after wiring
```

### Pattern 1: WorkflowEngine Injection into OrchestratorServiceServicer

**What:** `OrchestratorServiceServicer.__init__` currently receives only `db`. Extend it to also receive a `WorkflowEngine` instance. `StartCheckout` builds context and calls `engine.execute(order_id, make_checkout_workflow(TRANSACTION_PATTERN), context)`.

**Key design decision:** `workflow_id` in the engine maps to `order_id`. The engine writes to `{workflow:<workflow_id>}` keys, so the workflow record key becomes `{workflow:<order_id>}`.

**When to use:** Always — this is the CHK-02 wiring.

**Example:**
```python
# grpc_server.py (stripped to essentials)
from workflow_engine import WorkflowEngine
from checkout_workflow import make_checkout_workflow

TRANSACTION_PATTERN = os.environ.get("TRANSACTION_PATTERN", "saga")

class OrchestratorServiceServicer(OrchestratorServiceServicerBase):
    def __init__(self, db, engine: WorkflowEngine):
        self.db = db
        self.engine = engine

    async def StartCheckout(self, request, context):
        items = [{"item_id": item.item_id, "quantity": item.quantity}
                 for item in request.items]
        workflow_context = {
            "order_id": request.order_id,
            "user_id": request.user_id,
            "items": items,
            "total_cost": request.total_cost,
        }
        definition = make_checkout_workflow(TRANSACTION_PATTERN)
        result = await self.engine.execute(request.order_id, definition, workflow_context)
        return CheckoutResponse(
            success=result["success"],
            error_message=result["error_message"],
        )
```

**Critical:** `run_checkout()`, `run_2pc_checkout()`, and `run_compensation()` become dead code. They must NOT be deleted in Phase 17 if any existing test directly calls them (they are deleted in Phase 18 per REF-01). However, the test that directly calls `run_checkout()` and patches `grpc_server.release_stock` (`test_compensation_retries_until_success` and `test_run_checkout_compensates_on_circuit_breaker`) will still pass after Phase 17 ONLY if those functions remain in `grpc_server.py`.

### Pattern 2: WorkflowEngine.resume() for Recovery

**What:** Add a `resume()` method to `WorkflowEngine` that reads workflow state from the store and drives it to a terminal state using the appropriate strategy.

**Why `resume()` and not calling strategy directly:** Recovery needs to route based on strategy field stored in the record. The engine already knows the strategy registry; adding `resume()` keeps recovery scanner ignorant of SAGA vs 2PC internals (CHK-03 requirement).

**Key design issue:** `WorkflowStore.get()` returns the raw record dict. The recovery scanner must pass a `WorkflowDefinition` (with the checkout steps) to `engine.resume()` so the strategy can execute/compensate the actual transport operations. This means `engine.resume()` needs access to a `WorkflowDefinition` factory.

**Options for passing the WorkflowDefinition to resume():**
- **Option A (recommended):** `engine.resume(workflow_id, definition, context)` — caller (recovery.py) reads the stored record, reconstructs context from fields (order_id, user_id, items_json, total_cost), calls `make_checkout_workflow(strategy)` for definition, then calls `engine.resume()`.
- **Option B:** Engine holds a registered definition factory per workflow name. Too complex for phase scope.

**Resume implementation for SAGA strategy:**
```python
# workflow_engine.py — new method
async def resume(self, workflow_id: str, definition: WorkflowDefinition, context: dict) -> dict:
    """Resume a partially-completed workflow from its current state.

    Reads current state from store. Routes to strategy's resume/compensate path.
    For SAGA: if state is in forward states, re-runs from current position.
              if state is COMPENSATING, runs compensate().
    For 2PC:  if state is COMMITTING, re-sends commits.
              if state is ABORTING/INIT/PREPARING, re-sends aborts.
    """
    strategy = _STRATEGIES.get(definition.strategy)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {definition.strategy!r}")

    record = await self._store.get(workflow_id)
    if record is None:
        return {"success": False, "error_message": "workflow not found"}

    state = record.get("state", "")
    # Delegate to strategy-level resume logic
    return await strategy.resume(workflow_id, definition, context, self._store, state)
```

**SagaStrategy.resume() bridge:** SagaStrategy already has `compensate(workflow_id, definition, context, store, completed_indices=None)` for the recovery path (reads step_N_done flags). Need a `resume()` method that:
- If state == "COMPENSATING": calls `self.compensate(workflow_id, definition, context, store, completed_indices=None)` (reads step_N_done flags).
- If state in ("STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED"): re-runs from the current step in STATE_SEQUENCE.

This is analogous to the existing `resume_saga()` in `recovery.py` but expressed through the strategy class.

**TwoPhaseStrategy:** No `compensate()` method exists (2PC abort is integral to `execute()`). For recovery, a `resume()` method needs to:
- If state == "COMMITTING": re-send commits, transition to COMMITTED.
- If state in ("INIT", "PREPARING", "ABORTING"): re-send aborts, transition to ABORTED.

This mirrors the existing `resume_tpc()` in `recovery.py`.

### Pattern 3: consumers.py compensation consumer update

**What:** `_handle_compensation_message()` currently imports `run_compensation` from `grpc_server`. After wiring, this should call the engine's compensation path instead.

**Options:**
- **Option A (recommended for simplicity):** Keep importing `run_compensation` from `grpc_server`. Since `run_compensation` is NOT deleted in Phase 17 (only in Phase 18/REF-01), this continues to work. No change to `consumers.py` is strictly necessary for Phase 17.
- **Option B:** Replace with `engine.resume()` call for the COMPENSATING state. Cleaner but requires passing a definition and context, which the consumer must reconstruct from the saga record.

**For Phase 17 scope:** Option A (no change to `consumers.py`) is safe and keeps the change surface minimal. Phase 18 cleanup will remove `run_compensation` along with the whole `saga.py` module. Document this as a known debt.

**However:** If the goal is "consumers.py are updated to call engine APIs" (phase description says all three files are updated), then Option B must be implemented.

**Recommendation:** Implement Option B — have the compensation consumer reconstruct context from the SAGA record and call `engine.resume()`. This satisfies the phase description and is not much more complex than Option A.

```python
# consumers.py — updated _handle_compensation_message
async def _handle_compensation_message(db, group, msg_id, fields) -> None:
    event_type = fields.get(b"event_type", b"").decode()
    if event_type != "compensation_triggered":
        await db.xack(STREAM_NAME, group, msg_id)
        return
    # ... delivery count check unchanged ...
    try:
        order_id = fields.get(b"order_id", b"").decode()
        if order_id:
            from workflow_engine import _get_engine  # module-level engine reference
            # OR: pass engine as parameter to the consumer coroutine
            from workflow_store import WorkflowStore
            from checkout_workflow import make_checkout_workflow
            import json as _json
            store = WorkflowStore(db)
            record = await store.get(order_id)
            if record and record.get("state") == "COMPENSATING":
                strategy = record.get("strategy", "saga")
                items_json = record.get("items", "[]")
                context = {
                    "order_id": order_id,
                    "user_id": record.get("user_id", ""),
                    "items": _json.loads(items_json),
                    "total_cost": int(record.get("total_cost", "0")),
                }
                definition = make_checkout_workflow(strategy)
                engine = _get_engine()
                await engine.resume(order_id, definition, context)
        await db.xack(STREAM_NAME, group, msg_id)
    except Exception as exc:
        logging.warning("Compensation for %s failed: %s (will retry)", msg_id, exc)
```

**Engineering note:** The cleanest approach is to pass the engine instance to the consumer coroutine (background task) via `app.py`. Currently `app.py` calls `app.add_background_task(compensation_consumer, db)`. Change this to pass `engine` as well: `app.add_background_task(compensation_consumer, db, engine)`.

### Pattern 4: app.py construction order

**What:** `app.py` must construct `WorkflowStore` and `WorkflowEngine` in `startup()` after Redis is connected, then pass the engine to `OrchestratorServiceServicer` and `compensation_consumer`.

```python
# app.py startup() additions
from workflow_store import WorkflowStore
from workflow_engine import WorkflowEngine

async def startup():
    global db
    # ... existing Redis init ...
    store = WorkflowStore(db)
    engine = WorkflowEngine(store=store, db=db)
    # ... existing recovery calls ...
    app.add_background_task(serve_grpc, db, engine)   # pass engine
    app.add_background_task(compensation_consumer, db, engine)  # pass engine
```

```python
# grpc_server.py serve_grpc update
async def serve_grpc(db, engine) -> None:
    global _grpc_server
    _grpc_server = grpc.aio.server()
    add_OrchestratorServiceServicer_to_server(
        OrchestratorServiceServicer(db, engine), _grpc_server
    )
    _grpc_server.add_insecure_port("[::]:50053")
    await _grpc_server.start()
    await _grpc_server.wait_for_termination()
```

### Pattern 5: conftest.py needs engine injection

**What:** `conftest.py` creates `OrchestratorServiceServicer(orchestrator_db)` directly. After the constructor changes to `OrchestratorServiceServicer(db, engine)`, `conftest.py` must also construct a `WorkflowEngine`. This affects ALL tests using `orchestrator_stub` (the session-scoped server).

```python
# conftest.py orchestrator_grpc_server fixture update
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def orchestrator_grpc_server(orchestrator_db, grpc_clients):
    from workflow_store import WorkflowStore
    from workflow_engine import WorkflowEngine
    store = WorkflowStore(orchestrator_db)
    engine = WorkflowEngine(store=store, db=orchestrator_db)

    server = grpc.aio.server()
    add_OrchestratorServiceServicer_to_server(
        OrchestratorServiceServicer(orchestrator_db, engine), server
    )
    server.add_insecure_port("[::]:50053")
    await server.start()
    yield server
    await server.stop(grace=0)
```

**This is mandatory** for tests that use `orchestrator_stub` (the gRPC end-to-end tests in `test_saga.py`): `test_checkout_happy_path`, `test_checkout_insufficient_stock_compensates`, `test_checkout_insufficient_credit_compensates`, `test_checkout_duplicate_returns_original`, `test_compensation_retries_until_success`, `test_idempotency_keys_prevent_duplicate_side_effects`.

### Pattern 6: Test compatibility — the critical audit

The following tests directly reference `run_checkout`, `run_2pc_checkout`, or `run_compensation` from `grpc_server`. They must be reviewed case by case:

| Test | What it does | Phase 17 impact |
|------|-------------|----------------|
| `test_run_checkout_compensates_on_circuit_breaker` (test_fault_tolerance.py:168) | Calls `run_checkout(orchestrator_db, ...)` directly | `run_checkout` still exists in Phase 17 (deleted in Phase 18). Test passes unchanged. |
| `test_compensation_retries_until_success` (test_saga.py:347) | Patches `grpc_server.release_stock` and calls `orchestrator_stub.StartCheckout` | After wiring, `StartCheckout` calls `engine.execute()` → `SagaStrategy` → `checkout_workflow._release_all` → `transport.release_stock`. The patch `grpc_server.release_stock` will NOT intercept the compensation call because the wired path calls `checkout_workflow._release_all` which imports `release_stock` from `transport`, not from `grpc_server`. This test WILL FAIL unless the patch is updated to `patch("checkout_workflow.release_stock", ...)`. |
| `test_pattern_toggle_saga` (test_2pc_coordinator.py:404) | Patches `grpc_server.run_checkout` | After wiring, `StartCheckout` calls `engine.execute()` instead. `run_checkout` is never called. Test WILL FAIL — must be rewritten to patch `engine.execute()`. |
| `test_pattern_toggle_2pc` (test_2pc_coordinator.py:437) | Patches `grpc_server.run_2pc_checkout` | Same — must be rewritten to patch `engine.execute()` or verify via `TRANSACTION_PATTERN` effect on definition strategy. |
| `test_2pc_*` (test_2pc_coordinator.py, all except toggle) | Call `run_2pc_checkout(tpc_db, ...)` directly | `run_2pc_checkout` still exists in Phase 17. Tests pass unchanged. |

**The 37-test target:** The phase goal says "all 37 existing integration tests pass." Currently there are 130 tests total. The "37" likely refers to the integration-relevant subset (those using real Redis and gRPC, not pure unit tests). The safest interpretation is that the FULL 130-test suite must pass.

**Total remediation work:**
1. `test_compensation_retries_until_success`: Change `patch("grpc_server.release_stock", ...)` to `patch("checkout_workflow.release_stock", ...)` and potentially `patch("checkout_workflow.refund_payment", ...)`. Also update the `release_stock` call in `run_compensation` that is now being bypassed.
2. `test_pattern_toggle_saga`: Rewrite to verify SAGA strategy is used by patching `engine.execute` or by observing the `_STRATEGIES` dict.
3. `test_pattern_toggle_2pc`: Same as above.

### Anti-Patterns to Avoid

- **Injecting engine as a module-level global:** WorkflowEngine must be injectable (REF-03). Module-level globals in grpc_server.py are the existing pattern for `TRANSACTION_PATTERN` but not for `WorkflowEngine` — use constructor injection.
- **Changing WorkflowStore key prefix to {saga:*}:** The store uses `{workflow:*}`. Do NOT rename it to match old keys. Recovery scanner must migrate to scan `{workflow:*}` keys.
- **Keeping recovery.py scanning {saga:*} while engine writes to {workflow:*}:** After wiring, new checkouts create `{workflow:<order_id>}` records. The old `recover_incomplete_sagas()` scanning `{saga:*}` will miss them. For Phase 17, `recover_incomplete_sagas()` and `recover_incomplete_tpc()` must be generalized to scan `{workflow:*}` — or kept as-is with a NEW `recover_incomplete_workflows()` added alongside.
- **Breaking the `conftest.py` session-scoped server without updating it:** All integration tests will fail with a TypeError if the constructor signature changes and conftest is not updated.
- **Deleting run_checkout / run_2pc_checkout in Phase 17:** Phase 18 (REF-01) does this. Deleting them in Phase 17 would break `test_run_checkout_compensates_on_circuit_breaker` and the six `test_2pc_*` tests that call them directly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Strategy selection for recovery | if/elif on TRANSACTION_PATTERN | Read strategy field from WorkflowStore record | Engine already stores strategy-aware state; record contains the original strategy |
| Context reconstruction in recovery | New parsing code | Read existing fields from WorkflowStore.get() | Store already persists metadata dict via create(metadata=context) |
| New retry logic for recovery | Custom loop | SagaStrategy.compensate() already handles infinite retry | Already tested and extracted |
| New WAL recovery for 2PC | Custom COMMITTING/ABORTING check | TwoPhaseStrategy.resume() or equivalent pattern from resume_tpc() | Patterns already established in recovery.py |

## Common Pitfalls

### Pitfall 1: Recovery scanner key prefix mismatch
**What goes wrong:** `recover_incomplete_sagas()` scans `{saga:*}` — but after wiring, new checkouts write to `{workflow:*}` keys. Recovery never finds new workflows.
**Why it happens:** WorkflowStore uses `{workflow:<id>}` prefix (D-01 from Phase 14 research). recovery.py was written before the engine existed.
**How to avoid:** Replace `recover_incomplete_sagas()` and `recover_incomplete_tpc()` with a new `recover_incomplete_workflows()` that scans `{workflow:*}` and reads the `strategy` field to determine SAGA vs 2PC recovery path. Keep old scanners for Phase 17 only if backward compatibility with pre-engine records is needed (it is not — this is a fresh deployment).
**Warning signs:** Kill-test shows consistency violations because crashed mid-workflow records are never found by recovery.

### Pitfall 2: conftest.py constructor mismatch breaks all integration tests
**What goes wrong:** `OrchestratorServiceServicer(orchestrator_db)` fails with `TypeError: __init__() missing 1 required positional argument: 'engine'`.
**Why it happens:** conftest.py constructs the servicer directly and must be updated alongside grpc_server.py.
**How to avoid:** Update `orchestrator_grpc_server` fixture in conftest.py to construct WorkflowStore and WorkflowEngine before constructing the servicer.
**Warning signs:** ALL tests using `orchestrator_stub` fail with TypeError at session startup.

### Pitfall 3: test_compensation_retries_until_success patches wrong module
**What goes wrong:** Test patches `grpc_server.release_stock` but after wiring, the compensation path is `SagaStrategy.compensate()` → `checkout_workflow._release_all()` → `transport.release_stock`. The patch does not intercept the actual call.
**Why it happens:** Before wiring, `run_compensation()` in grpc_server.py calls `release_stock` imported at the top of grpc_server.py. After wiring, compensation goes through checkout_workflow.py which imports from transport.py.
**How to avoid:** Update the patch target to `patch("checkout_workflow.release_stock", side_effect=flaky_release_stock)` and verify `checkout_workflow.release_stock` is the actual name intercepted by the wired path.
**Warning signs:** `call_count` assertion fails (`assert call_count == 3`) because the mock is never called.

### Pitfall 4: Exactly-once / duplicate detection regression
**What goes wrong:** The old `run_checkout()` checked for existing `{saga:*}` records before executing. After wiring, `WorkflowEngine.execute()` calls `store.create()` which uses HSETNX on `{workflow:*}`. If two concurrent requests arrive, the second one gets `store.create()` returning `False` — but there is NO early-return on `False` in `WorkflowEngine.execute()` currently.
**Why it happens:** `WorkflowEngine.execute()` (Phase 16) calls `store.create()` and ignores the return value. It proceeds to publish events and call `strategy.execute()` regardless. The strategy then calls `store.transition(workflow_id, "STARTED", "STOCK_RESERVED")` which returns False (because the first request already advanced the state), but SagaStrategy does not handle False from `store.transition()`.
**How to avoid:** `WorkflowEngine.execute()` MUST check the return value of `store.create()`. If `False`, read the existing record state and return the appropriate result (COMPLETED → success, FAILED → failure, otherwise "already in progress").
**Warning signs:** `test_checkout_duplicate_returns_original` fails — second call returns "already in progress" OR double-executes.

### Pitfall 5: WorkflowEngine.resume() needs strategy field in stored record
**What goes wrong:** `engine.resume()` reads the workflow record to determine which strategy to use. But `WorkflowStore.create(metadata=context)` stores context fields — including all keys from the context dict. The context dict passed to `engine.execute()` is `{order_id, user_id, items, total_cost}` — it does NOT include a `strategy` field.
**Why it happens:** The strategy selection happens in `engine.execute()` via `definition.strategy`, but this information is not persisted to the store.
**How to avoid:** When calling `store.create()` in `engine.execute()`, add `strategy` to the metadata: `metadata={**context, "strategy": definition.strategy}`. Then recovery can read `record["strategy"]` to reconstruct the correct definition.
**Warning signs:** `engine.resume()` raises ValueError for unknown strategy, or always defaults to SAGA for all recovered 2PC records.

### Pitfall 6: items_json encoding in WorkflowStore vs saga.py
**What goes wrong:** `saga.py`'s `create_saga_record()` stores items as `items_json`. The WorkflowStore stores context metadata with `items` key (from `context["items"]` — a list, serialized by `json.dumps(v)` in `store.create()`). Recovery must use the same field name when reading back.
**Why it happens:** `WorkflowStore.create()` at line 87: `fields[k] = v if isinstance(v, str) else json.dumps(v)`. So `context["items"]` (a list) becomes `json.dumps(items)` stored under key `"items"`. Recovery must read `record["items"]` and `json.loads()` it.
**Warning signs:** Recovery reconstructed context has `items=None` or wrong items list.

## Code Examples

### Exactly-Once Check in WorkflowEngine.execute()
```python
# workflow_engine.py — execute() with duplicate detection
async def execute(self, workflow_id: str, definition: WorkflowDefinition, context: dict) -> dict:
    strategy = _STRATEGIES.get(definition.strategy)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {definition.strategy!r}")

    initial_state = _INITIAL_STATES[definition.strategy]
    # Persist strategy field for recovery (Pitfall 5)
    created = await self._store.create(
        workflow_id, initial_state,
        metadata={**context, "strategy": definition.strategy}
    )
    if not created:
        # Duplicate: read stored result and return
        existing = await self._store.get(workflow_id)
        if existing is None:
            return {"success": False, "error_message": "internal error"}
        state = existing.get("state", "")
        if state in ("COMPLETED", "COMMITTED"):
            return {"success": True, "error_message": ""}
        if state in ("FAILED", "ABORTED"):
            return {"success": False, "error_message": existing.get("error_message", "")}
        return {"success": False, "error_message": "checkout already in progress"}

    await publish_event(self._db, "workflow_started", workflow_id,
                        context.get("order_id", ""), context.get("user_id", ""))

    result = await strategy.execute(workflow_id, definition, context, self._store)

    event_type = "workflow_succeeded" if result.get("success") else "workflow_failed"
    await publish_event(self._db, event_type, workflow_id,
                        context.get("order_id", ""), context.get("user_id", ""))

    return result
```

### Recovery Scanner for {workflow:*} keys
```python
# recovery.py — new recover_incomplete_workflows()
import json

WORKFLOW_NON_TERMINAL = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING",
                          "INIT", "PREPARING", "COMMITTING", "ABORTING"}

async def recover_incomplete_workflows(db, engine) -> None:
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="{workflow:*", count=100):
        try:
            raw = await db.hgetall(key)
        except Exception:
            continue
        if not raw:
            continue
        record = {k.decode(): v.decode() for k, v in raw.items()}
        state = record.get("state", "")

        if state not in WORKFLOW_NON_TERMINAL:
            continue

        updated_at = int(record.get("updated_at", "0"))
        if (now - updated_at) < STALENESS_THRESHOLD_SECONDS:
            skipped += 1
            continue

        workflow_id = record.get("workflow_id", "")
        strategy = record.get("strategy", "saga")
        context = {
            "order_id": record.get("order_id", workflow_id),
            "user_id": record.get("user_id", ""),
            "items": json.loads(record.get("items", "[]")),
            "total_cost": int(record.get("total_cost", "0")),
        }
        from checkout_workflow import make_checkout_workflow
        definition = make_checkout_workflow(strategy)
        await engine.resume(workflow_id, definition, context)
        recovered += 1

    logging.info("Workflow recovery complete: %d recovered, %d skipped", recovered, skipped)
```

### test_pattern_toggle rewrite (avoiding patch of deleted functions)
```python
# tests/test_2pc_coordinator.py — rewritten toggle tests
async def test_pattern_toggle_saga(tpc_db, clean_tpc_db):
    """TRANSACTION_PATTERN=saga -> StartCheckout uses saga strategy."""
    from grpc_server import OrchestratorServiceServicer
    from workflow_store import WorkflowStore
    from workflow_engine import WorkflowEngine

    store = WorkflowStore(tpc_db)
    engine = WorkflowEngine(store=store, db=tpc_db)

    with patch("grpc_server.TRANSACTION_PATTERN", "saga"), \
         patch.object(engine, "execute", new_callable=AsyncMock,
                      return_value={"success": True, "error_message": ""}) as mock_exec:

        servicer = OrchestratorServiceServicer(tpc_db, engine)
        # ... mock request ...
        result = await servicer.StartCheckout(MockRequest(), None)

    call_args = mock_exec.call_args
    # The definition passed to engine.execute() should have strategy="saga"
    definition = call_args[0][1]  # positional arg 1 = definition
    assert definition.strategy == "saga"
```

### WorkflowEngine.resume() skeleton
```python
# workflow_engine.py addition
async def resume(self, workflow_id: str, definition: WorkflowDefinition, context: dict) -> dict:
    """Resume an incomplete workflow from its persisted state.

    Reads current state from store and delegates to strategy.resume().
    Called by recovery scanner on startup.
    """
    strategy = _STRATEGIES.get(definition.strategy)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {definition.strategy!r}")

    record = await self._store.get(workflow_id)
    if record is None:
        return {"success": False, "error_message": "workflow not found"}

    state = record.get("state", "")
    return await strategy.resume(workflow_id, definition, context, self._store, state)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| OrchestratorServiceServicer receives only `db` | Receives `db` + `WorkflowEngine` | Phase 17 | Engine is now injectable per REF-03 |
| run_checkout() / run_2pc_checkout() call sites | engine.execute() single entry point | Phase 17 | All checkout coordination through engine |
| Recovery scans {saga:*} and {tpc:*} prefixes | Recovery scans {workflow:*} prefix | Phase 17 | Single key namespace for all workflow state |
| run_compensation() in grpc_server.py called from consumers.py | engine.resume() in COMPENSATING state | Phase 17 | Consumers decoupled from grpc_server.py internals |

**Note:** `saga.py`, `tpc.py`, `run_checkout()`, `run_2pc_checkout()`, `run_compensation()` are NOT deleted in Phase 17. They remain as dead code. Phase 18 (REF-01) removes them after validation is complete.

## Open Questions

1. **Does WorkflowEngine.execute() need exactly-once for FAILED/ABORTED retry?**
   - What we know: Old `run_checkout()` deleted the `{saga:*}` record on FAILED state, allowing retry. Old `run_2pc_checkout()` deleted the `{tpc:*}` record on ABORTED state.
   - What's unclear: Does `engine.execute()` need to handle the "retry after terminal failure" case? Or do callers always use a new `order_id`?
   - Recommendation: The test `test_checkout_duplicate_returns_original` only tests duplicate detection for the in-flight case (not retry after FAILED). For Phase 17, implement the COMPLETED/in-progress duplicate check as the minimum. If `test_checkout_duplicate_returns_original` fails, investigate whether the old FAILED-retry behavior is tested and implement accordingly.

2. **What is the exact set of "37 integration tests" referred to in the phase goal?**
   - What we know: 130 tests total; 37 was likely the count before Phases 14-16 added new unit tests.
   - What's unclear: Whether "37 integration tests pass" means the integration-only subset or the full suite.
   - Recommendation: Target 130/130 passing. The phase success criteria explicitly includes kill-test (0 violations) which is a Docker-based requirement, not a unit test. Focus on making all 130 unit/integration tests pass.

3. **Can SagaStrategy and TwoPhaseStrategy be extended with resume() without breaking existing tests?**
   - What we know: Both strategies are tested in `test_strategies.py` with mock stores. Adding `resume()` is additive.
   - What's unclear: Whether `resume()` lives on the strategy class or only on the engine.
   - Recommendation: Put `resume()` on the engine, not the strategy. The engine already knows state sequences and can delegate to the appropriate strategy method. SagaStrategy already has `compensate(completed_indices=None)` for recovery; the engine.resume() can call `strategy.execute()` from the correct step or `strategy.compensate()` depending on current state.

## Environment Availability

Step 2.6: SKIPPED — Phase 17 is pure Python wiring of existing modules. No new external dependencies.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 with pytest-asyncio |
| Config file | `pytest.ini` (asyncio_mode=auto, testpaths=tests) |
| Quick run command | `python3 -m pytest tests/ -x -m "not requires_docker"` |
| Full suite command | `python3 -m pytest tests/ -x -m "not requires_docker"` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CHK-02 | OrchestratorServiceServicer.StartCheckout calls engine.execute() only | unit | `python3 -m pytest tests/test_2pc_coordinator.py -x -k "toggle"` | Exists (needs rewrite) |
| CHK-02 | Happy path checkout via engine wiring produces COMPLETED state | integration | `python3 -m pytest tests/test_saga.py -x -k "happy_path"` | Exists |
| CHK-02 | Stock failure triggers compensation via engine wiring | integration | `python3 -m pytest tests/test_saga.py -x -k "insufficient_stock"` | Exists |
| CHK-02 | Payment failure triggers compensation via engine wiring | integration | `python3 -m pytest tests/test_saga.py -x -k "insufficient_credit"` | Exists |
| CHK-02 | Duplicate checkout returns stored result | integration | `python3 -m pytest tests/test_saga.py -x -k "duplicate"` | Exists |
| CHK-03 | Recovery scanner finds {workflow:*} keys in non-terminal states | unit | `python3 -m pytest tests/test_fault_tolerance.py -x -k "recovery"` | Exists (needs update) |
| CHK-03 | Recovery drives COMPENSATING workflow to FAILED | integration | `python3 -m pytest tests/test_fault_tolerance.py -x -k "recovery_compensating"` | Exists (needs update) |
| CHK-03 | Recovery drives STARTED workflow to COMPLETED or FAILED | integration | `python3 -m pytest tests/test_fault_tolerance.py -x -k "recovery_started"` | Exists (needs update) |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/ -x -m "not requires_docker"`
- **Per wave merge:** `python3 -m pytest tests/ -x -m "not requires_docker"`
- **Phase gate:** Full suite (130 tests) green + kill-test 0 violations before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_fault_tolerance.py` — recovery tests (`test_recovery_resolves_stale_started_saga`, `test_recovery_resolves_stale_compensating_saga`, `test_no_sagas_stranded_after_recovery`) will need updating to seed `{workflow:*}` keys instead of `{saga:*}` keys and to pass engine to recovery function
- [ ] `tests/test_2pc_coordinator.py::test_pattern_toggle_saga` — must be rewritten (patches deleted function)
- [ ] `tests/test_2pc_coordinator.py::test_pattern_toggle_2pc` — must be rewritten (patches deleted function)
- [ ] `tests/test_saga.py::test_compensation_retries_until_success` — patch target must change from `grpc_server.release_stock` to `checkout_workflow.release_stock`

*(4 test files need targeted fixes; no new test files needed — all behaviors are covered by existing tests after update)*

## Sources

### Primary (HIGH confidence)
- Direct codebase reading: `orchestrator/grpc_server.py`, `orchestrator/recovery.py`, `orchestrator/consumers.py`, `orchestrator/app.py`
- Direct codebase reading: `orchestrator/workflow_engine.py`, `orchestrator/workflow_store.py`, `orchestrator/checkout_workflow.py`, `orchestrator/saga_strategy.py`, `orchestrator/tpc_strategy.py`
- Direct codebase reading: `tests/conftest.py`, `tests/test_2pc_coordinator.py`, `tests/test_saga.py`, `tests/test_fault_tolerance.py`
- `.planning/REQUIREMENTS.md` — CHK-02, CHK-03 definitions
- `.planning/phases/16-workflowengine-checkout-definition/16-VERIFICATION.md` — Phase 16 complete status and open questions
- `pytest.ini` — test framework configuration

### Secondary (MEDIUM confidence)
- None required — all technical decisions derivable from direct codebase inspection

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages, all existing project dependencies
- Architecture: HIGH — engine pattern derived directly from existing module interfaces; all call sites verified by reading source code
- Pitfalls: HIGH — test compatibility analysis derived from reading all test files and tracing the execution paths that change after wiring; pitfall 4 (exactly-once) is the highest-risk finding

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable — internal codebase only)
