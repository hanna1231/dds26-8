# Phase 18: Cleanup & Refactoring - Research

**Researched:** 2026-03-27
**Domain:** Python codebase cleanup — module deletion, structured logging, dependency injection audit, test migration
**Confidence:** HIGH

## Summary

Phase 18 is the final cleanup pass before demo and code review. All four requirements are straightforward deletion and logging changes, but each has hidden blast-radius: `saga.py` and `tpc.py` are still imported by `grpc_server.py`, `recovery.py`, `consumers.py`, and four test files. Deleting those modules without updating every import site will break the entire test suite and app startup.

The WorkflowEngine injectable dependency (REF-03) is structurally already satisfied: `app.py` creates `WorkflowStore`, passes it to `WorkflowEngine`, and passes the engine to `serve_grpc` and `compensation_consumer`. The remaining work is deleting the dead code paths (`run_checkout`, `run_2pc_checkout`, `recover_incomplete_sagas`, `recover_incomplete_tpc`) and removing the old-module imports that surround them.

Named step logging (REF-02) requires adding `logging.info` calls into `SagaStrategy.execute()` and `TwoPhaseStrategy.execute()` because those are the only code paths that now run steps — the engine-path replaces the old hand-written loops in `grpc_server.py`.

**Primary recommendation:** Execute in three ordered waves — (1) update tests that reference old modules and functions, (2) delete `saga.py`/`tpc.py` and strip dead code from `grpc_server.py`, `recovery.py`, `consumers.py`, and `app.py`, then (3) add step+workflow_id logging to the strategy classes and run the full test suite.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REF-01 | Delete `saga.py` and `tpc.py` after verifying no remaining import | Full import audit below — all 9 import sites catalogued with required changes |
| REF-02 | Named step execution logging with workflow_id on every execution log line | Strategies are the only execution path; add `logging.info` with `step.name` and `workflow_id` in `SagaStrategy.execute()` and `TwoPhaseStrategy.execute()` |
| REF-03 | WorkflowEngine as injectable dependency — no global engine singleton in any engine module | Already satisfied structurally; confirm `_STRATEGIES` module-level dict in `workflow_engine.py` is acceptable (stateless strategy singletons, not mutable global state) |
| REF-04 | General codebase cleanup for clarity, consistency, maintainability | Dead code removal, docstring updates, module-level comment cleanup |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.13.1 | Runtime | Project requirement |
| pytest | 9.0.2 | Test runner | Already in use |
| pytest-asyncio | 1.3.0 | Async test support | Already in use |

No new libraries are needed for this phase. All work is deletion, import updates, and `logging.info` calls.

**Installation:** No new dependencies.

---

## Architecture Patterns

### Pattern 1: Import Blast-Radius Audit Before Deletion

`saga.py` and `tpc.py` must not be deleted until every import site is updated. The full list of import sites (production code only — worktrees excluded):

**`saga.py` import sites:**
| File | Import | Action |
|------|--------|--------|
| `orchestrator/grpc_server.py` lines 25-30 | `from saga import create_saga_record, transition_state, get_saga, set_saga_error` | Remove — used only by `run_checkout` which is deleted |
| `orchestrator/recovery.py` line 31 | `from saga import transition_state, get_saga` | Remove — used only by `resume_saga` which handles old-path recovery |
| `orchestrator/consumers.py` line 130 | `from saga import get_saga` | Remove — inside `elif order_id:` fallback branch that uses `run_compensation` |
| `tests/test_saga.py` line 33 | `from saga import create_saga_record, transition_state, get_saga, VALID_TRANSITIONS` | This test file tests `saga.py` directly — must be deleted or repurposed |
| `tests/test_fault_tolerance.py` line 21 | `from saga import create_saga_record, get_saga, transition_state` | Needs rewrite to use `WorkflowStore` |
| `tests/test_2pc_coordinator.py` line 372 | `from saga import create_saga_record, get_saga` | One test that mixes saga + tpc records — needs rewrite |

**`tpc.py` import sites:**
| File | Import | Action |
|------|--------|--------|
| `orchestrator/grpc_server.py` line 31 | `from tpc import create_tpc_record, transition_tpc_state, get_tpc` | Remove — used only by `run_2pc_checkout` which is deleted |
| `orchestrator/recovery.py` line 161 | `from tpc import transition_tpc_state` | Remove — used only by `resume_tpc` in old recovery path |
| `tests/test_tpc.py` line 27 | `from tpc import create_tpc_record, transition_tpc_state, get_tpc, TPC_VALID_TRANSITIONS` | This test file tests `tpc.py` directly — must be deleted or repurposed |
| `tests/test_2pc_coordinator.py` lines 26, 174, 226 | `from tpc import create_tpc_record, transition_tpc_state, get_tpc` | Needs rewrite to use `WorkflowStore` + engine |

### Pattern 2: Dead Code Scope in grpc_server.py

`grpc_server.py` currently contains two large dead-code blocks alongside the live `OrchestratorServiceServicer`:

1. `run_checkout()` — lines 185–346, ~162 LOC. Only used by `test_fault_tolerance.py` and `test_events.py`. Phase 17 STATE.md decision: "Preserve run_checkout/run_2pc_checkout in grpc_server.py for backward-compatible tests (Phase 18 REF-01 will delete them)".
2. `run_2pc_checkout()` — lines 353–455, ~103 LOC. Only used by `test_2pc_coordinator.py`.
3. `run_compensation()` — lines 121–178, ~58 LOC. Used by `test_fault_tolerance.py` and as a fallback in `consumers.py`.
4. `retry_forever` / `retry_forward` — lines 48–114. Still used by `test_fault_tolerance.py` directly. Both are already extracted verbatim into `retry.py`. After tests are rewritten, these can be removed from `grpc_server.py`.

**Critical:** `grpc_server.py` line 25-31 still imports from `saga` and `tpc` at module top-level. Deleting `saga.py`/`tpc.py` before removing those imports will cause `ImportError` on any test that imports `grpc_server`.

### Pattern 3: Old Recovery Functions in recovery.py

`recovery.py` contains three recovery functions:
- `recover_incomplete_sagas(db)` — scans `{saga:*}` keys, calls `resume_saga()` which imports from `saga` and `grpc_server`. DELETE.
- `resume_saga(db, saga)` — imports `from saga import transition_state, get_saga`. DELETE.
- `recover_incomplete_tpc(db)` — scans `{tpc:*}` keys, calls `resume_tpc()` which imports `from tpc import transition_tpc_state`. DELETE.
- `resume_tpc(db, tpc)` — imports `from tpc import transition_tpc_state`. DELETE.
- `recover_incomplete_workflows(db, engine)` — the new engine-based scanner. KEEP.

`app.py` imports `recover_incomplete_sagas, recover_incomplete_tpc, recover_incomplete_workflows` — the first two need to be removed from the import and startup sequence.

### Pattern 4: Named Step Logging Implementation

REF-02 requires log lines like:
```
workflow_id=<id> step=reserve_stock executing
workflow_id=<id> step=reserve_stock completed
workflow_id=<id> step=charge_payment executing
```

The correct location is `SagaStrategy.execute()` (in `saga_strategy.py`) and `TwoPhaseStrategy.execute()` (in `tpc_strategy.py`), inside the step loop/gather. Currently neither strategy logs step names — `tpc_strategy.py` has a module-level `logger = logging.getLogger(__name__)` but `saga_strategy.py` has no logger at all.

Pattern to follow (matches `tpc_strategy.py` style):
```python
import logging
logger = logging.getLogger(__name__)

# In execute(), before calling step.action:
logger.info("workflow_id=%s step=%s executing", workflow_id, step.name)
# After step succeeds:
logger.info("workflow_id=%s step=%s completed", workflow_id, step.name)
# On step failure:
logger.warning("workflow_id=%s step=%s failed: %s", workflow_id, step.name, result.get("error_message"))
```

For `TwoPhaseStrategy.execute()` the steps run via `asyncio.gather` — log before the gather call (cannot log per-step mid-gather):
```python
for step in definition.steps:
    logger.info("workflow_id=%s step=%s preparing", workflow_id, step.name)
# After gather, log results per index
```

### Pattern 5: REF-03 Compliance Verification

The success criterion for REF-03 is: "no module-level engine singleton or global mutable state exists in any engine module."

Current state:
- `workflow_engine.py` has `_STRATEGIES = {"saga": SagaStrategy(), "2pc": TwoPhaseStrategy()}` at module level. This is a module-level dict of stateless strategy instances. The strategies are stateless (confirmed by `SagaStrategy` and `TwoPhaseStrategy` having no constructor params). This is not mutable global state — it is a static dispatch table. The planner must decide: is this acceptable per REF-03, or should `_STRATEGIES` be moved into `WorkflowEngine.__init__`?

The conservative reading: move `_STRATEGIES` and `_INITIAL_STATES` inside `WorkflowEngine.__init__` to eliminate any module-level state. This is a small change (2 lines moved) and eliminates ambiguity for the code review.

- `app.py` creates `engine` inside `startup()` and passes it down. No module-level engine. This is already correct.
- `grpc_server.py` has `_grpc_server: grpc.aio.Server = None` at module level — this is a server handle, not an engine instance, and is acceptable server lifecycle state.

### Pattern 6: consumers.py Fallback Branch Deletion

`consumers.py` lines 127-133:
```python
elif order_id:
    # Fallback: use old compensation path if no engine (backward compat)
    from grpc_server import run_compensation
    from saga import get_saga
    saga = await get_saga(db, order_id)
    if saga and saga.get("state") == "COMPENSATING":
        await run_compensation(db, saga)
```

This is the old-path fallback. Since `engine` is always passed from `app.py` (confirmed in `app.py` line 50: `app.add_background_task(compensation_consumer, db, engine)`), the `elif order_id:` branch is unreachable. Delete this branch. This removes the last use of `from saga import get_saga`.

### Pattern 7: Test Migration Strategy

Tests that currently test `saga.py` and `tpc.py` directly:

**`test_saga.py`** — tests `create_saga_record`, `transition_state`, `get_saga`, `VALID_TRANSITIONS`. These test the old module functions. After deletion of `saga.py`, these tests become invalid. Options:
1. Delete the test file entirely (cleanest for demo).
2. Rewrite to test `WorkflowStore` equivalents.

**`test_tpc.py`** — same situation for TPC records.

**`test_fault_tolerance.py`** — imports `from saga import create_saga_record, get_saga, transition_state` and `from grpc_server import retry_forward, run_checkout, run_compensation`. After deleting those, this test file needs substantial rewriting — the fault tolerance concepts (circuit breaker, compensation, recovery) are now tested via `WorkflowEngine` + strategies. The test for `run_checkout_compensates_on_circuit_breaker` needs to become an engine-level test.

**`test_2pc_coordinator.py`** — imports `from tpc import ...` and `from grpc_server import run_2pc_checkout`. After deletion, the TPC coordinator tests should exercise `TwoPhaseStrategy` via `WorkflowEngine`. The WAL tests (TPC-05) and recovery tests (TPC-06) are already partially covered by `test_strategies.py`.

**Recommendation:** Check `test_strategies.py` and `test_workflow_engine.py` for coverage overlap before deciding what to keep in the migrated versions.

### Anti-Patterns to Avoid

- **Partial deletion:** Deleting `saga.py` without removing all import sites first. The module-level imports in `grpc_server.py` will fail on import before any test even runs.
- **Deleting tests without verifying coverage:** `test_saga.py` and `test_fault_tolerance.py` cover SAGA-01 through SAGA-06, FAULT-01 through FAULT-04. Confirm these are covered by the engine-path tests before deleting.
- **Forgetting `recovery.py` NON_TERMINAL_STATES constant:** `NON_TERMINAL_STATES` and `WORKFLOW_NON_TERMINAL` are still referenced by `recover_incomplete_workflows()`. After deleting the old scanners, ensure `WORKFLOW_NON_TERMINAL` is retained and the import of the constant from the old functions is not accidentally removed.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Step name logging format | Custom log format or structured JSON | Python `logging.info` with `%s` format args | Consistent with existing logging style in codebase |
| Test migration | Rewriting all saga/tpc tests from scratch | Identify coverage in existing `test_strategies.py` and `test_workflow_engine.py` first | Avoid duplicating tests that already exist |

---

## Common Pitfalls

### Pitfall 1: Module-Level Import Order in grpc_server.py

**What goes wrong:** `grpc_server.py` lines 25-31 are module-level top imports of `saga` and `tpc`. If those modules are deleted but the import lines remain, any test that does `import grpc_server` or `from grpc_server import ...` will fail with `ModuleNotFoundError`, even if the test doesn't use the saga/tpc functions.

**Why it happens:** Python resolves all module-level imports at import time, not at call time.

**How to avoid:** Remove the `from saga import ...` and `from tpc import ...` lines from the top of `grpc_server.py` BEFORE deleting `saga.py` and `tpc.py`, or do it atomically in the same commit.

**Warning signs:** `ImportError: No module named 'saga'` on any test run.

### Pitfall 2: recovery.py Imports at Function Scope

**What goes wrong:** `recovery.py` uses deferred imports inside function bodies (`from saga import ...` inside `resume_saga`, `from tpc import ...` inside `resume_tpc`). These won't fail at module import time — they fail only when the function is called. After deletion of `saga.py` and `tpc.py`, if `recover_incomplete_sagas` or `resume_saga` are ever called (even by an old test), it will raise `ModuleNotFoundError` at runtime.

**How to avoid:** Delete `resume_saga`, `recover_incomplete_sagas`, `resume_tpc`, `recover_incomplete_tpc` functions entirely from `recovery.py` in the same wave as the module deletions.

### Pitfall 3: consumers.py Fallback Branch Still References saga

**What goes wrong:** `consumers.py` line 130 has `from saga import get_saga` inside a function body. If the outer `elif order_id:` branch is not deleted before `saga.py` is removed, any compensation event processed after deletion will crash.

**How to avoid:** Delete the `elif order_id:` fallback block from `consumers.py` in the same wave.

### Pitfall 4: test_2pc_coordinator.py Uses tpc Functions AND grpc_server.run_2pc_checkout

**What goes wrong:** `test_2pc_coordinator.py` tests both `from tpc import ...` (for direct record inspection) AND `from grpc_server import run_2pc_checkout` (for coordinator behavior). The TPC tests that check final state (e.g., `record["state"] == "COMMITTED"`) now need to read from `WorkflowStore` using `{workflow:<order_id>}` key instead of the old `{tpc:<order_id>}` key. The `get_tpc` calls need to become `store.get(order_id)` calls.

**Warning signs:** Tests pass but check stale `{tpc:*}` keys that are never written by the engine path.

### Pitfall 5: _STRATEGIES Module-Level Dict Ambiguity

**What goes wrong:** `workflow_engine.py` has `_STRATEGIES` as a module-level dict. If a code reviewer interprets REF-03 strictly (no module-level mutable state in engine modules), this could be flagged.

**How to avoid:** Move `_STRATEGIES` and `_INITIAL_STATES` into `WorkflowEngine.__init__` as `self._strategies` and `self._initial_states`. The planner should include this as part of REF-03 work.

### Pitfall 6: Logging Pattern in TwoPhaseStrategy (gather-concurrent steps)

**What goes wrong:** `TwoPhaseStrategy.execute()` runs all steps concurrently via `asyncio.gather`. Adding per-step logging inside the gather is not possible without restructuring. A naive implementation logs "preparing step X" before the gather for all steps, which is correct — but the "completed" log needs to happen after inspecting the results array, not inside the gather.

**How to avoid:** Log "preparing step X" before the gather (loop over `definition.steps`), then after the gather, log per-step result using the indexed `results` array.

---

## Code Examples

### REF-02: Step logging in SagaStrategy.execute()

```python
import logging
logger = logging.getLogger(__name__)

# In SagaStrategy.execute(), replace the step loop:
for i, step in enumerate(definition.steps):
    logger.info("workflow_id=%s step=%s executing", workflow_id, step.name)
    result = await retry_forward(lambda s=step, c=context: s.action(c))

    if not result.get("success"):
        logger.warning("workflow_id=%s step=%s failed: %s",
                       workflow_id, step.name, result.get("error_message", ""))
        # ... compensation path unchanged ...

    logger.info("workflow_id=%s step=%s completed", workflow_id, step.name)
    await store.mark_step_done(workflow_id, i)
    # ... state transition unchanged ...
```

### REF-02: Step logging in TwoPhaseStrategy.execute()

```python
# In TwoPhaseStrategy.execute(), before asyncio.gather:
for step in definition.steps:
    logger.info("workflow_id=%s step=%s preparing", workflow_id, step.name)

futures = [step.action(context) for step in definition.steps]
results = await asyncio.gather(*futures, return_exceptions=True)

# After gather, log per-step results:
for i, r in enumerate(results):
    step = definition.steps[i]
    if isinstance(r, Exception):
        logger.warning("workflow_id=%s step=%s prepare failed: %s",
                       workflow_id, step.name, r)
    elif not r.get("success"):
        logger.warning("workflow_id=%s step=%s prepare voted NO: %s",
                       workflow_id, step.name, r.get("error_message", ""))
    else:
        logger.info("workflow_id=%s step=%s prepare voted YES", workflow_id, step.name)
```

### REF-03: Move _STRATEGIES into WorkflowEngine.__init__

```python
class WorkflowEngine:
    def __init__(self, store: WorkflowStore, db):
        self._store = store
        self._db = db
        self._strategies = {
            "saga": SagaStrategy(),
            "2pc": TwoPhaseStrategy(),
        }
        self._initial_states = {
            "saga": "STARTED",
            "2pc": "INIT",
        }

    async def execute(self, workflow_id, definition, context):
        strategy = self._strategies.get(definition.strategy)
        if strategy is None:
            raise ValueError(f"Unknown strategy: {definition.strategy!r}")
        initial_state = self._initial_states[definition.strategy]
        # ... rest unchanged ...
```

### REF-01: Correct deletion order (single atomic wave)

```
1. Remove top-level `from saga import ...` from grpc_server.py
2. Remove top-level `from tpc import ...` from grpc_server.py
3. Delete run_checkout(), run_2pc_checkout(), run_compensation() from grpc_server.py
4. Delete resume_saga(), recover_incomplete_sagas(), resume_tpc(), recover_incomplete_tpc() from recovery.py
5. Delete elif fallback block from consumers.py
6. Update app.py: remove recover_incomplete_sagas, recover_incomplete_tpc from import and startup()
7. Delete saga.py
8. Delete tpc.py
9. Update/delete test_saga.py, test_tpc.py
10. Rewrite test_fault_tolerance.py and test_2pc_coordinator.py to use WorkflowEngine
```

---

## Environment Availability

Step 2.6: SKIPPED (no external dependencies — phase is code/config-only changes)

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `/Users/daniel/WebstormProjects/dds26-8/pytest.ini` |
| Quick run command | `cd /Users/daniel/WebstormProjects/dds26-8 && python -m pytest tests/test_strategies.py tests/test_workflow_engine.py tests/test_workflow_store.py -x -q` |
| Full suite command | `cd /Users/daniel/WebstormProjects/dds26-8 && python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REF-01 | saga.py and tpc.py deleted, no import matches | smoke (grep) | `grep -r "from saga import\|from tpc import" orchestrator/` returns no matches | N/A — grep check |
| REF-01 | Existing engine tests still pass after deletion | integration | `python -m pytest tests/test_strategies.py tests/test_workflow_engine.py -x -q` | Yes |
| REF-02 | Step names appear in log output during checkout | integration | `python -m pytest tests/test_workflow_engine.py -x -q -s` (inspect log output) | Yes — needs log assertion added |
| REF-03 | No module-level engine singleton | code review | `grep -n "_engine\s*=" orchestrator/workflow_engine.py` | N/A — grep check |
| REF-04 | Full suite green | integration | `python -m pytest tests/ -x -q` | Partial — old tests need migration |

### Wave 0 Gaps

- [ ] `tests/test_fault_tolerance.py` — needs rewrite to use WorkflowEngine instead of `run_checkout`/`saga` imports
- [ ] `tests/test_2pc_coordinator.py` — needs rewrite to use WorkflowEngine instead of `run_2pc_checkout`/`tpc` imports
- [ ] `tests/test_saga.py` — needs deletion or rewrite (currently tests deleted module)
- [ ] `tests/test_tpc.py` — needs deletion or rewrite (currently tests deleted module)
- [ ] REF-02 log assertion — no test currently verifies step names appear in logs; add `caplog` assertion to `test_workflow_engine.py` or new test file

---

## Open Questions

1. **Fate of test_saga.py and test_tpc.py**
   - What we know: These test files test the old `saga.py` and `tpc.py` module APIs directly. After deletion they are invalid.
   - What's unclear: Do the SAGA-01/TPC-01 requirements being marked "Complete" mean these tests are no longer needed, or must some equivalent remain?
   - Recommendation: Check whether `test_strategies.py` and `test_workflow_store.py` provide equivalent coverage; if yes, delete both old test files.

2. **test_fault_tolerance.py rewrite scope**
   - What we know: This file imports `run_checkout`, `run_compensation`, `saga` functions. Six tests use `recover_incomplete_sagas()`.
   - What's unclear: How much of FAULT-01 through FAULT-04 is already covered by `test_strategies.py`?
   - Recommendation: Read `test_strategies.py` before deciding scope of rewrite.

3. **_STRATEGIES placement (REF-03 interpretation)**
   - What we know: Module-level stateless strategy singletons. The requirement says "no global mutable state."
   - What's unclear: Whether stateless singletons count as "global mutable state" for the code reviewer.
   - Recommendation: Move to `WorkflowEngine.__init__` to be unambiguous. Low-risk change.

---

## Sources

### Primary (HIGH confidence)
- Direct source code inspection of all 14 orchestrator modules and all 15 test files
- `orchestrator/grpc_server.py` — complete import inventory of saga/tpc dependencies
- `orchestrator/recovery.py` — old vs new scanner function inventory
- `orchestrator/consumers.py` — fallback branch identification
- `orchestrator/workflow_engine.py` — REF-03 compliance assessment
- `orchestrator/saga_strategy.py`, `tpc_strategy.py` — logging gap identification
- `tests/` — all 4 test files requiring migration identified
- `.planning/STATE.md` — Phase 17 decisions confirming "Phase 18 REF-01 will delete them"

### Secondary (MEDIUM confidence)
- None needed — all findings are directly observable from source code

---

## Metadata

**Confidence breakdown:**
- Import blast radius: HIGH — all import sites enumerated from source
- Dead code scope: HIGH — functions identified and cross-referenced
- Logging pattern: HIGH — existing `tpc_strategy.py` logger provides the template
- REF-03 compliance: HIGH — code is directly readable; only interpretation ambiguity on `_STRATEGIES`
- Test migration scope: MEDIUM — depends on coverage overlap not yet read in `test_strategies.py`

**Research date:** 2026-03-27
**Valid until:** 2026-04-10 (stable codebase, no external dependencies)
