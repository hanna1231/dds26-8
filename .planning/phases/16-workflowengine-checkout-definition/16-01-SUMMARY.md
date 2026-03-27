---
phase: 16-workflowengine-checkout-definition
plan: 01
subsystem: orchestrator
tags: [workflow-engine, saga, 2pc, events, tdd, python]

# Dependency graph
requires:
  - phase: 15-execution-strategies
    provides: SagaStrategy and TwoPhaseStrategy classes with execute() API
  - phase: 14-engine-core
    provides: WorkflowStore, WorkflowDefinition, WorkflowStep dataclasses
provides:
  - WorkflowEngine class with execute(workflow_id, definition, context) entry point
  - Strategy routing via _STRATEGIES dict (saga->SagaStrategy, 2pc->TwoPhaseStrategy)
  - Lifecycle event publishing (workflow_started, workflow_succeeded, workflow_failed)
  - store.create() with strategy-appropriate initial states (STARTED for saga, INIT for 2pc)
  - events.py public API updated: saga_id param renamed to workflow_id
affects:
  - phase: 16-02 (checkout definition wiring WorkflowEngine)
  - any caller of publish_event (parameter renamed from saga_id to workflow_id)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "WorkflowEngine as thin routing shell (~70 LOC) delegating to strategy objects"
    - "_STRATEGIES dict with singleton stateless strategy instances"
    - "_INITIAL_STATES dict for strategy-appropriate initial workflow states"
    - "Constructor injection of WorkflowStore and Redis db for testability"
    - "Fire-and-forget event publishing (never raises, never blocks checkout)"

key-files:
  created:
    - orchestrator/workflow_engine.py
    - tests/test_workflow_engine.py
  modified:
    - orchestrator/events.py

key-decisions:
  - "WorkflowEngine receives WorkflowStore via constructor (injectable per REF-03)"
  - "_STRATEGIES dict with pre-instantiated singleton strategy objects (stateless)"
  - "store.create() called before publish_event and strategy.execute() (correct ordering)"
  - "events.py public API: saga_id param renamed to workflow_id; wire format retains saga_id"

patterns-established:
  - "Engine is routing-only: no service names (stock/payment), no transport imports"
  - "Lifecycle events wrap strategy.execute: started -> strategy -> succeeded/failed"

requirements-completed: [ENG-03]

# Metrics
duration: 2min
completed: 2026-03-27
---

# Phase 16 Plan 01: WorkflowEngine Summary

**WorkflowEngine routing shell: saga/2pc strategy dispatch with lifecycle event wrapping and injectable WorkflowStore**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-03-27T11:38:30Z
- **Completed:** 2026-03-27T11:40:20Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- WorkflowEngine.execute() routes to SagaStrategy or TwoPhaseStrategy via _STRATEGIES dict based on definition.strategy
- Lifecycle events published in correct order: workflow_started before strategy, workflow_succeeded/workflow_failed after
- store.create() called with "STARTED" (saga) or "INIT" (2pc) before strategy delegation
- ValueError raised for unknown strategy names
- events.py publish_event parameter renamed from saga_id to workflow_id (wire format unchanged)
- 8 new unit tests, all passing; 26 existing tests unaffected

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Write failing tests for WorkflowEngine** - `11cb46e` (test)
2. **Task 2: GREEN -- Implement WorkflowEngine and update events.py** - `f9fa523` (feat)

## Files Created/Modified
- `orchestrator/workflow_engine.py` - WorkflowEngine class with execute() entry point and strategy routing
- `tests/test_workflow_engine.py` - 8 unit tests covering all engine behaviors
- `orchestrator/events.py` - publish_event signature: saga_id param renamed to workflow_id

## Decisions Made
- Constructor injection of WorkflowStore and db (per REF-03 for testability)
- _STRATEGIES dict with pre-instantiated singletons (strategies are stateless per D-07)
- _INITIAL_STATES dict separates state knowledge from routing logic cleanly
- events.py parameter rename: saga_id -> workflow_id; _build_event internal call unchanged so wire format stays backward compatible

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Worktree was behind main branch by 7 commits including the phase 16 plan files. Merged main into worktree branch before starting (fast-forward merge, no conflicts).

## Next Phase Readiness
- WorkflowEngine.execute() is fully tested and ready for wiring in 16-02 (checkout workflow definition)
- events.py publish_event signature updated; existing callers use positional args so no changes needed
- All prerequisite strategy and store classes confirmed compatible

## Self-Check: PASSED

- orchestrator/workflow_engine.py: FOUND
- tests/test_workflow_engine.py: FOUND
- .planning/phases/16-workflowengine-checkout-definition/16-01-SUMMARY.md: FOUND
- Commit 11cb46e: FOUND
- Commit f9fa523: FOUND

---
*Phase: 16-workflowengine-checkout-definition*
*Completed: 2026-03-27*
