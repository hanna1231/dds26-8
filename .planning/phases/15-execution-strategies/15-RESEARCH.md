# Phase 15: Execution Strategies - Research

**Researched:** 2026-03-27
**Domain:** Python async workflow strategy pattern — SAGA and 2PC execution over WorkflowStore
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Both strategies expose `async execute(workflow_id, definition, context, store)` where `store` is a `WorkflowStore` instance, `definition` is a `WorkflowDefinition`, and `context` is a dict of domain metadata.
- **D-02:** `SagaStrategy` additionally exposes a public `async compensate(workflow_id, definition, context, store)` method for the recovery scanner (Phase 17).
- **D-03:** `TwoPhaseStrategy` does NOT have a separate compensate — abort is integral to the execute flow.
- **D-04:** Extract `retry_forward()` and `retry_forever()` from `grpc_server.py` into `orchestrator/retry.py`. No configuration surface — existing defaults are proven in production.
- **D-05:** Each strategy module defines its own state constants and `VALID_TRANSITIONS` dict. Reuse exact state values from existing code:
  - SAGA: `STARTED`, `STOCK_RESERVED`, `PAYMENT_CHARGED`, `COMPLETED`, `COMPENSATING`, `FAILED`
  - 2PC: `INIT`, `PREPARING`, `COMMITTING`, `ABORTING`, `COMMITTED`, `ABORTED`
- **D-06:** Strategies validate transitions before calling `store.transition()`. Invalid transitions raise `ValueError`.
- **D-07:** Strategies do NOT publish events. They return `{"success": bool, "error_message": str}`. Event publishing deferred to WorkflowEngine (Phase 16).

### Claude's Discretion

- Exact method signatures beyond what is specified in D-01/D-02/D-03
- How SagaStrategy tracks which steps completed for partial compensation (internal list vs reading store)
- Whether strategies are stateless classes or carry constructor params
- Test structure (unit tests with mock callables vs integration tests with Redis)
- How `context` dict is threaded through step callables

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STR-01 | SAGA strategy executor with forward step execution and bounded retry | Extraction of `run_checkout()` forward loop from `grpc_server.py:269-330`; `retry_forward()` extracted to `retry.py` |
| STR-02 | SAGA compensation with reverse-order step undoing and infinite retry | Extraction of `run_compensation()` from `grpc_server.py:119-176`; `retry_forever()` extracted to `retry.py`; WorkflowDefinition.steps reversed |
| STR-03 | 2PC strategy executor with concurrent prepare, WAL decision write, and phase-2 commit/abort | Extraction of `run_2pc_checkout()` from `grpc_server.py:351-453`; `asyncio.gather` concurrent prepare; WAL pattern preserved |
| STR-04 | Both strategies callable from the same WorkflowDefinition (strategy field selects execution path) | WorkflowDefinition.strategy field already exists; strategies accept identical `execute(workflow_id, definition, context, store)` signature |
</phase_requirements>

---

## Summary

Phase 15 extracts the execution logic already proven in `orchestrator/grpc_server.py` into two isolated strategy classes (`SagaStrategy` and `TwoPhaseStrategy`) and a shared retry module. The source code is concrete: `run_checkout()` (lines 183–344), `run_compensation()` (lines 119–176), `run_2pc_checkout()` (lines 351–453), and the retry utilities (lines 46–112) are the direct extraction targets. Nothing needs to be invented — everything is a translation from hardcoded domain objects to generic `WorkflowDefinition`/`WorkflowStep` callables.

The central generalisation move is replacing hardcoded gRPC calls (e.g. `reserve_stock(...)`) with calls to `step.action(context)` and replacing hardcoded flag checks (e.g. `current.get("stock_reserved") == "1"`) with `store.get()` step-index flag checks (`step_0_done == "1"`). State constants and valid-transition dicts are copied verbatim from `saga.py` and `tpc.py` into the new strategy modules.

Test strategy follows the established project pattern: unit tests use pure mock callables (no Redis, no gRPC); the test file lives in `tests/test_strategies.py`. The existing `pytest.ini` (`asyncio_mode = auto`) means no `@pytest.mark.asyncio` decorators are needed.

**Primary recommendation:** Extract, generalise, and test — do not redesign. All algorithmic decisions are already made in the existing code; the task is faithful translation.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python asyncio | stdlib | Async execution, `asyncio.gather` for concurrent 2PC prepare | Already used project-wide |
| redis.asyncio | project dep | WorkflowStore backend; strategies access only via WorkflowStore interface | Existing dep in orchestrator/requirements.txt |
| circuitbreaker | project dep | `CircuitBreakerError` propagation bypass in `retry_forward` | Already used in grpc_server.py:96,104 |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest + pytest-asyncio | 9.0.2 / 1.3.0 | Test runner, async test support | All strategy tests |
| unittest.mock (stdlib) | stdlib | `AsyncMock` for step callable mocks in unit tests | Pure unit tests without Redis |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Pure unit tests (mock callables) | Integration tests with real Redis | Unit tests run without infrastructure, cover strategy logic cleanly; integration tests verify store interaction but require Redis to be up. Both can coexist. |
| Stateless strategy classes | Module-level functions | Classes are slightly more idiomatic for the injectable pattern (Phase 16 REF-03); functions would work but classes align with existing WorkflowStore class design |

**Installation:** No new packages required. All dependencies already present in `orchestrator/requirements.txt`.

---

## Architecture Patterns

### Recommended Project Structure

```
orchestrator/
├── retry.py              # extracted retry_forward(), retry_forever()
├── saga_strategy.py      # SagaStrategy class + SAGA state constants
├── tpc_strategy.py       # TwoPhaseStrategy class + TPC state constants
├── workflow_types.py     # (Phase 14 — already exists)
└── workflow_store.py     # (Phase 14 — already exists)

tests/
└── test_strategies.py    # unit tests for both strategies + retry module
```

### Pattern 1: Strategy Class with Injected Store

**What:** Stateless class with no constructor parameters. All dependencies (store, definition, context) passed per-call via `execute()`.

**When to use:** Every call to `execute()` or `compensate()`. Aligns with D-01, REF-03 (injectable).

**Example:**
```python
# orchestrator/saga_strategy.py
class SagaStrategy:
    async def execute(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
    ) -> dict:
        ...

    async def compensate(
        self,
        workflow_id: str,
        definition: WorkflowDefinition,
        context: dict,
        store: WorkflowStore,
    ) -> dict:
        ...
```

### Pattern 2: Forward Execution with Partial-Completion Tracking

**What:** As SAGA forward steps succeed, record completed step indices in a local list. On failure, pass the list to compensate so only actually-completed steps are undone.

**When to use:** Within `SagaStrategy.execute()`. This avoids a store round-trip to discover which steps ran, while staying correct in the non-crash path.

**Example:**
```python
completed_step_indices: list[int] = []
for i, step in enumerate(definition.steps):
    result = await retry_forward(lambda s=step: s.action(context))
    if not result.get("success"):
        # transition to COMPENSATING, then compensate only completed_step_indices
        await self.compensate(workflow_id, definition, context, store,
                              completed_indices=completed_step_indices)
        return {"success": False, "error_message": result.get("error_message", "")}
    await store.mark_step_done(workflow_id, i)
    completed_step_indices.append(i)
```

Note: `compensate()` is also callable standalone by the recovery scanner (D-02), so it must also handle the case where completed steps are determined by re-reading `store.get()` flags when `completed_indices` is not provided.

### Pattern 3: WAL Decision Before Phase-2 (2PC)

**What:** Write COMMITTING (or ABORTING) state to WorkflowStore BEFORE sending phase-2 messages. This is the write-ahead log (WAL) guarantee already in `run_2pc_checkout()`.

**When to use:** `TwoPhaseStrategy.execute()` after collecting all PREPARE votes.

**Example:**
```python
# Source: grpc_server.py:426-438
if all_yes:
    await store.transition(workflow_id, "PREPARING", "COMMITTING")  # WAL
    commit_futures = [step.action(context) for step in definition.steps]
    await asyncio.gather(*commit_futures, return_exceptions=True)
    await store.transition(workflow_id, "COMMITTING", "COMMITTED")
    return {"success": True, "error_message": ""}
else:
    await store.transition(workflow_id, "PREPARING", "ABORTING")    # WAL
    abort_futures = [step.compensation(context) for step in definition.steps]
    await asyncio.gather(*abort_futures, return_exceptions=True)
    await store.transition(workflow_id, "ABORTING", "ABORTED")
    return {"success": False, "error_message": first_error}
```

### Pattern 4: Lambda Closure Default-Arg Capture

**What:** When constructing callables inside loops, use `lambda s=step:` not `lambda: step` to capture the loop variable by value, avoiding Python's late-binding closure bug.

**When to use:** Any `asyncio.gather` future construction inside a for-loop. Already present in `grpc_server.py:277`.

**Example:**
```python
# CORRECT — captures step at loop iteration time
futures = [lambda s=step: s.action(context) for step in definition.steps]

# WRONG — all lambdas capture the final value of `step`
futures = [lambda: step.action(context) for step in definition.steps]
```

Note: `asyncio.gather` receives coroutines, not lambdas. The correct pattern for gather is to call the step action directly to produce a coroutine:
```python
futures = [step.action(context) for step in definition.steps]
results = await asyncio.gather(*futures, return_exceptions=True)
```
If `step.action` is a coroutine function, calling it in the list comprehension captures `step` correctly by loop value because the call happens at comprehension time. This is safe. The lambda anti-pattern only matters when the call is deferred (e.g., wrapped in `retry_forever`).

### Anti-Patterns to Avoid

- **Hardcoded field names in strategies:** Never check `current.get("stock_reserved") == "1"`. Use `store.get()` and check `step_N_done` flags via index. Strategies must not know about domain field names.
- **Event publishing in strategies:** Strategies return `{"success": bool, "error_message": str}` only (D-07). Publishing is Phase 16's responsibility.
- **Importing grpc_server into strategies:** Creates circular dependency and defeats generality. Strategies only import from `retry.py`, `workflow_types.py`, `workflow_store.py`.
- **Sharing state between concurrent compensate calls:** `compensate()` re-reads store flags on entry; it must be safe to call from recovery scanner concurrent to a retry.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exponential backoff with jitter | Custom sleep loop | `retry_forward()` / `retry_forever()` from extracted `retry.py` | Production-proven, handles CircuitBreakerError bypass, tested |
| Concurrent async fan-out | Manual task tracking | `asyncio.gather(*futures, return_exceptions=True)` | stdlib, handles exceptions cleanly, same pattern as existing 2PC code |
| Idempotent step tracking | Custom flag scheme | `store.mark_step_done(workflow_id, i)` + `store.get()` | Already defined in WorkflowStore (ENG-05), avoids double-execution on recovery |
| Atomic state transition | Redis MULTI/EXEC | `store.transition()` via Lua CAS | Lua CAS is already extracted and tested in Phase 14 |

**Key insight:** All algorithmic complexity already exists and is tested in grpc_server.py/saga.py/tpc.py. Phase 15 is a translation exercise, not an invention exercise.

---

## Common Pitfalls

### Pitfall 1: Late-Binding Closure Bug in retry_forever Lambdas

**What goes wrong:** A compensation loop passes `lambda: step.compensation(context)` to `retry_forever`. If the loop variable `step` is captured by reference, all retries operate on the last step in the loop.

**Why it happens:** Python closures capture variables by reference. Loop variables are mutable.

**How to avoid:** Use default argument capture `lambda s=step, c=context: s.compensation(c)` — OR call the coroutine directly and `await` without retry indirection where possible. The existing `grpc_server.py:166` uses `lambda iid=item_id, qty=quantity: ...` as the reference pattern.

**Warning signs:** Compensation always operates on the last item; test with multi-step definitions.

### Pitfall 2: Stale Flags in Compensation Recovery Path

**What goes wrong:** `compensate()` is called standalone by Phase 17 recovery scanner. If it uses a local `completed_step_indices` list from the current call, it has no list — the original execute call is gone. It must re-read the store.

**Why it happens:** The in-memory list from `execute()` is lost after crash.

**How to avoid:** Design `compensate()` to optionally accept `completed_indices`. When called without it (recovery path), read `store.get()` and check `step_N_done` flags to determine which steps ran. The existing `grpc_server.py:137` shows the re-read pattern: "Re-read current flags to avoid stale data".

**Warning signs:** Recovery scanner triggers compensation, but no actual compensation actions run.

### Pitfall 3: Transition Validation Before Store.transition()

**What goes wrong:** Strategy calls `store.transition()` with an invalid from/to pair. WorkflowStore is state-agnostic (Phase 14 D-04) — it does NOT validate transitions. The invalid transition silently returns False.

**Why it happens:** Store's state-agnosticism is by design; the strategy must be the guard.

**How to avoid:** Each strategy module defines `VALID_TRANSITIONS` (copied verbatim from `saga.py` and `tpc.py`). Validate before calling `store.transition()` and raise `ValueError` on invalid transitions (D-06).

**Warning signs:** State machine moves to unexpected state without error; silent False from `store.transition()` ignored.

### Pitfall 4: asyncio.gather Return Value Type Checking

**What goes wrong:** `asyncio.gather(*futures, return_exceptions=True)` returns exceptions as objects in the result list, not raised. Treating all results as dicts without checking `isinstance(r, Exception)` causes `AttributeError` on `.get("success")`.

**Why it happens:** `return_exceptions=True` is the correct pattern for non-aborting fan-out, but it changes return types.

**How to avoid:** Iterate results with: `if isinstance(r, Exception): all_yes = False` before checking `r.get("success")`. Already done in `grpc_server.py:417-424`.

**Warning signs:** `AttributeError: 'TimeoutError' object has no attribute 'get'` in 2PC prepare phase.

### Pitfall 5: Creating Workflow Record in Strategy vs Caller

**What goes wrong:** The existing `run_checkout()` calls `create_saga_record()` internally. Strategies must NOT create records — the WorkflowEngine (Phase 16) calls `store.create()` before calling `strategy.execute()`. Duplicating creation in the strategy breaks exactly-once semantics.

**Why it happens:** Temptation to port the full `run_checkout()` function body verbatim.

**How to avoid:** Strategy `execute()` starts from an already-created record in initial state. It begins with `store.transition(workflow_id, initial_state, next_state)`. No `store.create()` call inside strategies.

**Warning signs:** Tests fail with duplicate workflow creation; Phase 16 engine creates a record that strategies immediately overwrite.

---

## Code Examples

Verified patterns from existing codebase:

### retry_forward extraction target (grpc_server.py:77-112)
```python
# Source: orchestrator/grpc_server.py lines 77-112
async def retry_forward(fn, max_attempts: int = 3, base: float = 0.5, cap: float = 30.0) -> dict:
    from circuitbreaker import CircuitBreakerError
    last_result = {"success": False, "error_message": "max retries exceeded"}
    for attempt in range(max_attempts):
        try:
            result = await fn()
            if result.get("success"):
                return result
            last_result = result
        except CircuitBreakerError:
            raise  # breaker open -- propagate immediately, never retry
        except Exception as exc:
            last_result = {"success": False, "error_message": str(exc)}
        if attempt < max_attempts - 1:
            delay = min(cap, base * (2 ** attempt))
            jitter = random.uniform(0, delay)
            await asyncio.sleep(jitter)
    return last_result
```

### retry_forever extraction target (grpc_server.py:46-70)
```python
# Source: orchestrator/grpc_server.py lines 46-70
async def retry_forever(fn, base: float = 0.5, cap: float = 30.0) -> dict:
    attempt = 0
    while True:
        try:
            result = await fn()
            if result.get("success"):
                return result
        except Exception as exc:
            logging.warning("compensation retry attempt %d failed: %s", attempt, exc)
        delay = min(cap, base * (2 ** attempt))
        await asyncio.sleep(delay)
        attempt += 1
```

### SAGA states to copy into saga_strategy.py (saga.py:14-28)
```python
# Source: orchestrator/saga.py lines 14-28
SAGA_STATES = {
    "STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED",
    "COMPLETED", "COMPENSATING", "FAILED",
}
VALID_TRANSITIONS: dict[str, set[str]] = {
    "STARTED": {"STOCK_RESERVED", "COMPENSATING"},
    "STOCK_RESERVED": {"PAYMENT_CHARGED", "COMPENSATING"},
    "PAYMENT_CHARGED": {"COMPLETED", "COMPENSATING"},
    "COMPENSATING": {"FAILED"},
}
```

### TPC states to copy into tpc_strategy.py (tpc.py:15-29)
```python
# Source: orchestrator/tpc.py lines 15-29
TPC_STATES = {
    "INIT", "PREPARING", "COMMITTING", "ABORTING", "COMMITTED", "ABORTED",
}
TPC_VALID_TRANSITIONS: dict[str, set[str]] = {
    "INIT": {"PREPARING"},
    "PREPARING": {"COMMITTING", "ABORTING"},
    "COMMITTING": {"COMMITTED"},
    "ABORTING": {"ABORTED"},
}
```

### asyncio.gather with return_exceptions (grpc_server.py:411-424)
```python
# Source: orchestrator/grpc_server.py lines 405-424
results = await asyncio.gather(*futures, return_exceptions=True)
all_yes = True
first_error = ""
for r in results:
    if isinstance(r, Exception):
        all_yes = False
        if not first_error:
            first_error = str(r)
    elif not r.get("success"):
        all_yes = False
        if not first_error:
            first_error = r.get("error_message", "prepare failed")
```

### Unit test pattern with AsyncMock (established project style)
```python
# Uses pytest-asyncio 1.3.0 with asyncio_mode=auto (no @pytest.mark.asyncio needed)
from unittest.mock import AsyncMock
from workflow_types import WorkflowStep, WorkflowDefinition
from workflow_store import WorkflowStore

async def test_saga_execute_success():
    action = AsyncMock(return_value={"success": True, "error_message": ""})
    compensation = AsyncMock(return_value={"success": True, "error_message": ""})
    step = WorkflowStep(name="step1", action=action, compensation=compensation)
    definition = WorkflowDefinition(name="test", steps=[step], strategy="saga")
    # Use real Redis via orchestrator_db fixture, or mock WorkflowStore
    ...
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded SAGA/2PC functions in grpc_server.py | Generic strategy classes operating on WorkflowDefinition | Phase 15 | Strategies testable in isolation, reusable for any workflow shape |
| domain-specific flag fields (stock_reserved, payment_charged) | Generic step_N_done flags via WorkflowStore.mark_step_done() | Phase 14 | Compensation can run without knowing domain field names |
| Retry logic duplicated in grpc_server.py | Shared retry.py module | Phase 15 | Single source of truth; DRY for both strategies |

---

## Open Questions

1. **How should context dict be passed to step callables?**
   - What we know: `context` is a dict (`{"order_id": ..., "user_id": ..., ...}`). Step callables in WorkflowStep have signature `Callable[..., Awaitable[Any]]`.
   - What's unclear: Should strategies call `step.action(context)` (positional), `step.action(**context)` (unpacked), or leave it to the callable to accept `**kwargs`?
   - Recommendation: Call as `step.action(context)` — pass the whole dict as a single positional argument. The callable signature is the checkout definition's responsibility (Phase 16 CHK-01). This is the simplest and most testable form.

2. **WorkflowStore initial state for SAGA vs 2PC**
   - What we know: SAGA starts at `STARTED`; 2PC starts at `INIT`. These are defined in the respective strategy modules.
   - What's unclear: Who creates the record with the correct initial state — the strategy or the engine?
   - Recommendation: Per Pitfall 5, the WorkflowEngine (Phase 16) calls `store.create()` with the initial state determined by `definition.strategy`. Strategies receive an already-created record. This is consistent with D-01 (strategy receives `store` but does not own its lifecycle).

---

## Environment Availability

Step 2.6: SKIPPED — phase is pure code extraction and reorganisation. No new external dependencies introduced. Redis and gRPC infrastructure already available (verified by passing Phase 14 tests).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pytest.ini` (asyncio_mode = auto, asyncio_default_fixture_loop_scope = session) |
| Quick run command | `pytest tests/test_strategies.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STR-01 | SagaStrategy.execute() runs steps in order with bounded retry; step failure halts forward progress | unit | `pytest tests/test_strategies.py::test_saga_execute_success -x` | Wave 0 |
| STR-01 | SagaStrategy.execute() triggers compensation when a step fails after max retries | unit | `pytest tests/test_strategies.py::test_saga_execute_step_failure_triggers_compensation -x` | Wave 0 |
| STR-01 | retry_forward() returns last failure after max_attempts exhausted | unit | `pytest tests/test_strategies.py::test_retry_forward_exhausted -x` | Wave 0 |
| STR-01 | retry_forward() propagates CircuitBreakerError immediately | unit | `pytest tests/test_strategies.py::test_retry_forward_circuit_breaker -x` | Wave 0 |
| STR-02 | SagaStrategy.compensate() calls compensation callables in reverse step order | unit | `pytest tests/test_strategies.py::test_saga_compensate_reverse_order -x` | Wave 0 |
| STR-02 | SagaStrategy.compensate() uses infinite retry for each compensation step | unit | `pytest tests/test_strategies.py::test_saga_compensate_retries_forever -x` | Wave 0 |
| STR-02 | SagaStrategy.compensate() skips steps with no step_N_done flag | unit | `pytest tests/test_strategies.py::test_saga_compensate_partial -x` | Wave 0 |
| STR-03 | TwoPhaseStrategy.execute() sends prepare concurrently via asyncio.gather | unit | `pytest tests/test_strategies.py::test_tpc_execute_concurrent_prepare -x` | Wave 0 |
| STR-03 | TwoPhaseStrategy.execute() writes COMMITTING before phase-2 on all-yes | unit | `pytest tests/test_strategies.py::test_tpc_execute_wal_commit -x` | Wave 0 |
| STR-03 | TwoPhaseStrategy.execute() writes ABORTING before phase-2 on any-no | unit | `pytest tests/test_strategies.py::test_tpc_execute_wal_abort -x` | Wave 0 |
| STR-04 | SagaStrategy.execute() accepts a WorkflowDefinition with strategy="saga" | unit | `pytest tests/test_strategies.py::test_both_strategies_accept_same_definition -x` | Wave 0 |
| STR-04 | TwoPhaseStrategy.execute() accepts a WorkflowDefinition with strategy="2pc" | unit | `pytest tests/test_strategies.py::test_both_strategies_accept_same_definition -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_strategies.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_strategies.py` — covers STR-01, STR-02, STR-03, STR-04 (file does not yet exist)

*(All other test infrastructure already exists: `conftest.py`, `orchestrator_db` fixture, `clean_orchestrator_db` fixture. No new fixtures required for pure unit tests using AsyncMock.)*

---

## Sources

### Primary (HIGH confidence)

- `orchestrator/grpc_server.py` — complete source of `retry_forward`, `retry_forever`, `run_checkout`, `run_compensation`, `run_2pc_checkout` — extraction targets read directly
- `orchestrator/saga.py` — `SAGA_STATES`, `VALID_TRANSITIONS`, `TRANSITION_LUA` — read directly
- `orchestrator/tpc.py` — `TPC_STATES`, `TPC_VALID_TRANSITIONS`, `TRANSITION_LUA` — read directly
- `orchestrator/workflow_types.py` — `WorkflowStep`, `WorkflowDefinition` — read directly (Phase 14 output)
- `orchestrator/workflow_store.py` — `WorkflowStore` API — read directly (Phase 14 output)
- `tests/conftest.py` — fixture inventory and test infrastructure — read directly
- `tests/test_workflow_store.py` — existing test patterns for this codebase — read directly
- `pytest.ini` — asyncio configuration — read directly

### Secondary (MEDIUM confidence)

None needed — all findings are derived from first-party project source files.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all deps already in use in the project; no new packages
- Architecture: HIGH — strategy interface locked by CONTEXT.md decisions; patterns extracted verbatim from existing code
- Pitfalls: HIGH — all pitfalls identified from direct code reading; existing code comments call them out explicitly
- Test structure: HIGH — existing test files and conftest fully understood; same patterns apply

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable codebase, no moving ecosystem targets)
