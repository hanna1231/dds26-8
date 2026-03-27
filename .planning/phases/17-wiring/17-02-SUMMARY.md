---
phase: 17-wiring
plan: 02
subsystem: orchestrator
tags: [wiring, recovery, resume, engine, consumers, workflow]
dependency_graph:
  requires: [17-01]
  provides: [CHK-03]
  affects: [workflow_engine, saga_strategy, tpc_strategy, recovery, consumers, app]
tech_stack:
  added: []
  patterns: [strategy-resume, engine-resume, workflow-recovery-scanner]
key_files:
  created: []
  modified:
    - orchestrator/workflow_engine.py
    - orchestrator/saga_strategy.py
    - orchestrator/tpc_strategy.py
    - orchestrator/recovery.py
    - orchestrator/consumers.py
    - orchestrator/app.py
decisions:
  - "SagaStrategy.resume() skips already-completed steps by reading step_N_done flags before re-executing forward path"
  - "TwoPhaseStrategy.resume() uses presumed abort for INIT/PREPARING states (mirrors existing recovery.py:resume_tpc pattern)"
  - "consumers.py engine=None default and old-path fallback preserves backward compat for tests not passing engine"
  - "recover_incomplete_workflows() imports make_checkout_workflow to reconstruct definition from stored strategy field"
  - "Old recover_incomplete_sagas/recover_incomplete_tpc functions preserved -- Phase 18 REF-01 will delete them"
metrics:
  duration: 3min
  completed: 2026-03-27
  tasks: 2
  files: 6
---

# Phase 17 Plan 02: Engine-based recovery scanner and resume() methods Summary

Engine.resume() delegates to strategy.resume() for SAGA and 2PC, recovery.py scans {workflow:*} keys via engine.resume(), consumers.py uses engine.resume() for compensation.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add resume() to WorkflowEngine and both strategies | 2fb70dd | workflow_engine.py, saga_strategy.py, tpc_strategy.py |
| 2 | Rewrite recovery.py, update consumers.py and app.py | 2064c05 | recovery.py, consumers.py, app.py |

## What Was Built

**Task 1:**
- Added `SagaStrategy.resume()`: dispatches to `compensate()` for COMPENSATING state; re-runs forward steps from `STATE_SEQUENCE.index(state)` skipping already-completed steps (reads `step_N_done` flags); on failure transitions to COMPENSATING and compensates
- Added `TwoPhaseStrategy.resume()`: COMMITTING re-sends phase-2 commits; INIT/PREPARING uses presumed abort (PREPARING -> ABORTING); ABORTING re-sends phase-2 aborts; returns unrecoverable error for unknown states
- Added `WorkflowEngine.resume()`: reads workflow state from store, looks up strategy in `_STRATEGIES` registry, delegates to `strategy.resume(workflow_id, definition, context, self._store, state)`

**Task 2:**
- `recovery.py`: Added `WORKFLOW_NON_TERMINAL` set with all 8 non-terminal states (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPENSATING, INIT, PREPARING, COMMITTING, ABORTING)
- `recovery.py`: Added `recover_incomplete_workflows(db, engine)` scanning `{workflow:*}` keys, reconstructing context and definition from stored fields, calling `engine.resume()`
- `recovery.py`: Added `from checkout_workflow import make_checkout_workflow` import; preserved all old functions
- `consumers.py`: Updated `compensation_consumer(db, engine=None)` signature; passes `engine` to `_handle_compensation_message`
- `consumers.py`: Rewrote `_handle_compensation_message` to use `engine.resume()` for workflow-based SAGAs; falls back to old `run_compensation` path when engine is None (backward compat)
- `app.py`: Added `recover_incomplete_workflows` import; calls `recover_incomplete_workflows(db, engine)` after existing recovery calls; passes `engine` to `compensation_consumer` background task

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None. All wiring is complete and functional.

## Self-Check: PASSED

- orchestrator/workflow_engine.py: FOUND (resume() method added)
- orchestrator/saga_strategy.py: FOUND (resume() method added)
- orchestrator/tpc_strategy.py: FOUND (resume() method added)
- orchestrator/recovery.py: FOUND (recover_incomplete_workflows added, old functions preserved)
- orchestrator/consumers.py: FOUND (engine parameter and engine.resume() call added)
- orchestrator/app.py: FOUND (recover_incomplete_workflows and engine passed to consumer)
- Commit 2fb70dd: FOUND
- Commit 2064c05: FOUND
