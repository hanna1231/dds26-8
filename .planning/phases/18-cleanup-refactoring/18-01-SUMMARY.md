---
phase: 18-cleanup-refactoring
plan: "01"
subsystem: tests
tags: [refactoring, cleanup, tests, engine-apis]
dependency_graph:
  requires: [17-wiring, 15-execution-strategies, 14-engine-core]
  provides: [clean-test-suite-for-phase-02-deletion]
  affects: [tests/test_fault_tolerance.py, tests/test_2pc_coordinator.py, tests/test_events.py]
tech_stack:
  added: []
  patterns: [engine.execute() in tests, WorkflowStore/WorkflowEngine test injection]
key_files:
  created: []
  modified:
    - tests/test_fault_tolerance.py
    - tests/test_2pc_coordinator.py
    - tests/test_events.py
  deleted:
    - tests/test_saga.py
    - tests/test_tpc.py
decisions:
  - Test lifecycle event names updated to workflow_started/workflow_succeeded (engine publishes these, not old saga event names)
  - test_run_checkout_compensates_on_circuit_breaker rewritten to use engine.execute() with WorkflowStore/WorkflowEngine injection
  - Toggle tests in test_2pc_coordinator.py already use engine APIs after main merge; only needed to strip old tpc/saga tests
metrics:
  duration: 3min
  completed: "2026-03-27"
  tasks_completed: 2
  files_modified: 3
  files_deleted: 2
requirements:
  - REF-01
  - REF-04
---

# Phase 18 Plan 01: Test Suite Migration to Engine APIs Summary

Migrate all test files that import from saga.py, tpc.py, or dead grpc_server.py functions to use the new engine APIs. Delete test_saga.py and test_tpc.py whose coverage is fully superseded by test_strategies.py and test_workflow_store.py.

## One-liner

Deleted test_saga.py and test_tpc.py, rewrote test_fault_tolerance.py/test_2pc_coordinator.py/test_events.py to use WorkflowEngine/WorkflowStore APIs, eliminating all old saga/tpc/grpc_server imports.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Delete test_saga.py and test_tpc.py, rewrite test_fault_tolerance.py | 55cd656 | tests/test_saga.py (D), tests/test_tpc.py (D), tests/test_fault_tolerance.py |
| 2 | Rewrite test_2pc_coordinator.py and test_events.py lifecycle test | 89031c3 | tests/test_2pc_coordinator.py, tests/test_events.py |

## What Was Done

### Task 1

**Deleted files:**
- `tests/test_saga.py` - Tested saga.py state machine directly; superseded by test_workflow_store.py and test_strategies.py
- `tests/test_tpc.py` - Tested tpc.py state machine directly; superseded by test_workflow_store.py and test_strategies.py

**Rewrote `tests/test_fault_tolerance.py`:**
- Removed imports: `from saga import ...`, `from grpc_server import retry_forward, run_checkout, run_compensation`, `from recovery import recover_incomplete_sagas, resume_saga`
- Added imports: `from workflow_store import WorkflowStore`, `from workflow_engine import WorkflowEngine`, `from checkout_workflow import make_checkout_workflow`
- Kept circuit breaker tests unchanged (test_circuit_breaker_trips_after_threshold, test_circuit_breaker_half_open_recovery, test_independent_breakers)
- Rewrote `test_run_checkout_compensates_on_circuit_breaker` to use `engine.execute()` instead of `run_checkout()`; verify via `store.get()` instead of `get_saga()`
- Removed recovery tests (recover_incomplete_sagas deleted in Phase 17)
- Removed retry_forward tests (covered by test_strategies.py)
- Removed `seed_saga` helper function

### Task 2

**Rewrote `tests/test_2pc_coordinator.py`:**
- Removed all tests using tpc/saga/run_2pc_checkout (10 tests removed; covered by test_strategies.py and test_workflow_store.py)
- Kept TPC-07 toggle tests: test_pattern_toggle_saga, test_pattern_toggle_2pc
- Toggle tests were already updated post-main-merge to use WorkflowEngine directly
- Imports reduced to: `grpc_server.OrchestratorServiceServicer`, `WorkflowStore`, `WorkflowEngine`

**Updated `tests/test_events.py`:**
- Replaced `from grpc_server import run_checkout` (function-level import inside test) with engine-based approach
- Rewrote `test_checkout_publishes_lifecycle_events` to use `engine.execute()` with mocked `transport` module functions
- Updated expected event names from old saga events (`checkout_started`, `saga_completed`) to engine events (`workflow_started`, `workflow_succeeded`)
- Added module-level imports: `WorkflowStore`, `WorkflowEngine`, `make_checkout_workflow`, `import transport as _transport_mod`

## Deviations from Plan

None - plan executed exactly as written.

The toggle tests in test_2pc_coordinator.py were already using the engine APIs after merging main (phases 14-17 work). Only needed to remove the old tpc/saga test functions.

## Known Stubs

None. No stub patterns introduced.

## Self-Check

### Created/Modified Files Exist

- tests/test_fault_tolerance.py: exists
- tests/test_2pc_coordinator.py: exists
- tests/test_events.py: exists
- tests/test_saga.py: deleted (confirmed)
- tests/test_tpc.py: deleted (confirmed)

### Commits Exist

- 55cd656: confirmed
- 89031c3: confirmed

## Self-Check: PASSED
