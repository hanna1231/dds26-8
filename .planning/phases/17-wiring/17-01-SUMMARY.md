---
phase: 17-wiring
plan: 01
subsystem: orchestrator
tags: [wiring, workflow-engine, grpc, duplicate-detection, testing]
dependency_graph:
  requires: [16-01, 16-02]
  provides: [CHK-02]
  affects: [grpc_server, app, conftest, test_2pc_coordinator, test_saga]
tech_stack:
  added: []
  patterns: [engine-injection, duplicate-detection, strategy-persistence]
key_files:
  created: []
  modified:
    - orchestrator/workflow_engine.py
    - orchestrator/grpc_server.py
    - orchestrator/app.py
    - tests/conftest.py
    - tests/test_2pc_coordinator.py
    - tests/test_saga.py
    - tests/test_workflow_engine.py
decisions:
  - "Store strategy field in metadata on store.create() for crash recovery prep (CHK-03)"
  - "Duplicate detection returns stored result without re-executing; maps COMPLETED/COMMITTED to success, FAILED/ABORTED to failure"
  - "Preserve run_checkout/run_2pc_checkout functions in grpc_server.py for backward-compatible tests (deleted in Phase 18 REF-01)"
  - "compensation test patches checkout_workflow.release_stock (not grpc_server.release_stock) because engine calls via checkout_workflow namespace"
metrics:
  duration: 3min
  completed: 2026-03-27
  tasks: 2
  files: 7
---

# Phase 17 Plan 01: Wiring grpc_server.py to WorkflowEngine Summary

Wire grpc_server.py StartCheckout to engine.execute() with duplicate detection and strategy persistence, updating app.py and all affected tests.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Harden WorkflowEngine.execute() and wire grpc_server.py + app.py | 5921273 | workflow_engine.py, grpc_server.py, app.py |
| 2 | Update conftest.py and fix 3 broken tests | c7ab8ab | conftest.py, test_2pc_coordinator.py, test_saga.py, test_workflow_engine.py |

## What Was Built

**Task 1:**
- Added duplicate detection to `WorkflowEngine.execute()`: captures `created` return value from `store.create()`, reads stored result for duplicates and maps state to success/failure dict
- Persists `strategy` field in metadata dict alongside context for crash recovery prep
- Updated `OrchestratorServiceServicer` constructor to accept `engine: WorkflowEngine`
- Replaced `StartCheckout` body to call `engine.execute()` with `make_checkout_workflow(TRANSACTION_PATTERN)` definition
- Updated `serve_grpc(db, engine)` signature to accept and inject engine
- Updated `app.py` to construct `WorkflowStore` and `WorkflowEngine` after `db.initialize()`, inject into `serve_grpc`

**Task 2:**
- Updated `conftest.py` `orchestrator_grpc_server` fixture to construct `WorkflowEngine` and pass to servicer
- Rewrote `test_pattern_toggle_saga` and `test_pattern_toggle_2pc` to patch `engine.execute` and assert `definition.strategy` instead of patching run_checkout/run_2pc_checkout
- Fixed `test_compensation_retries_until_success` to patch `checkout_workflow.release_stock` (correct namespace for engine path) and assert via `WorkflowStore.get()` checking `step_0_done` and `state == "FAILED"`
- Updated `test_workflow_engine.py` store.create assertions to include `strategy` field in expected metadata

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_workflow_engine.py metadata assertion mismatch**
- **Found during:** Task 2 verification
- **Issue:** `test_engine_calls_store_create_saga` and `test_engine_calls_store_create_2pc` asserted `metadata=context` but engine now passes `metadata={**context, "strategy": ...}`
- **Fix:** Updated both assertions to include `strategy` field in expected metadata dict
- **Files modified:** tests/test_workflow_engine.py
- **Commit:** c7ab8ab

## Known Stubs

None. All wiring is complete and functional.

## Self-Check: PASSED

- orchestrator/workflow_engine.py: FOUND (modified with duplicate detection)
- orchestrator/grpc_server.py: FOUND (modified with engine injection)
- orchestrator/app.py: FOUND (modified with engine construction)
- tests/conftest.py: FOUND (modified with engine fixture)
- tests/test_2pc_coordinator.py: FOUND (modified with engine.execute mock)
- tests/test_saga.py: FOUND (modified with checkout_workflow patch)
- Commit 5921273: FOUND
- Commit c7ab8ab: FOUND
