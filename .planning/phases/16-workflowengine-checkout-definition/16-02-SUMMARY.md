---
phase: 16-workflowengine-checkout-definition
plan: 02
subsystem: orchestrator
tags: [workflow, saga, 2pc, transport, checkout, tdd]

# Dependency graph
requires:
  - phase: 16-01
    provides: WorkflowEngine, WorkflowStore, WorkflowDefinition, WorkflowStep types
  - phase: 15-execution-strategies
    provides: SagaStrategy and TwoPhaseStrategy with STATE_SEQUENCE constraint
  - phase: 10-transport-adapter
    provides: transport.py with reserve_stock, charge_payment, prepare_stock, etc.

provides:
  - make_checkout_workflow("saga") factory returning WorkflowDefinition with 2 steps (reserve_stock, charge_payment)
  - make_checkout_workflow("2pc") factory returning WorkflowDefinition with 2 steps (prepare_stock, prepare_payment)
  - Complete separation: engine/strategy modules have zero references to Stock/Payment service names
  - Idempotency key format matching grpc_server.py pattern for all SAGA operations

affects:
  - grpc_server.py (can now delegate to engine.execute() with make_checkout_workflow())
  - any future checkout endpoint integration

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Module-level async functions for workflow steps (Option A) -- avoids closure late-binding risk"
    - "Transport-agnostic step callables: checkout_workflow.py is the ONLY module touching transport functions"
    - "Short-circuit on first failure in _reserve_all (matches grpc_server.py behavior)"
    - "Best-effort compensation in _release_all (compensate ALL items unconditionally)"

key-files:
  created:
    - orchestrator/checkout_workflow.py
    - tests/test_checkout_workflow.py
  modified: []

key-decisions:
  - "make_checkout_workflow uses module-level async functions (not lambdas/closures) to avoid late-binding risk"
  - "Exactly 2 steps for both saga and 2pc strategies (saga matches STATE_SEQUENCE of length 4: STARTED+2 transitions+COMPLETED)"
  - "Idempotency key format {saga:ORDER_ID}:step:reserve:ITEM_ID matches grpc_server.py exactly"
  - "2PC uses order_id directly as correlation key (no separate idempotency_key parameter)"
  - "checkout_workflow.py is the sole boundary layer; engine and strategies remain service-name-free"

patterns-established:
  - "Workflow definition factory pattern: one factory per domain operation, accepts strategy string, returns WorkflowDefinition"
  - "Transport isolation: domain-specific step implementations live only in checkout_workflow.py"

requirements-completed: [CHK-01]

# Metrics
duration: 8min
completed: 2026-03-27
---

# Phase 16 Plan 02: Checkout Workflow Definition Summary

**make_checkout_workflow() factory wiring SAGA and 2PC checkout steps to transport.py with idempotency keys matching grpc_server.py format**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-27T00:00:00Z
- **Completed:** 2026-03-27T00:08:00Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- Implemented `make_checkout_workflow()` factory supporting both "saga" and "2pc" strategies
- SAGA path: `_reserve_all` (per-item reserve with idempotency keys) + `_charge` (payment with idempotency key)
- 2PC path: `_prepare_all_stock` (per-item prepare) + `_prepare_payment` (payment prepare with order_id)
- All compensations implemented: `_release_all`, `_refund`, `_abort_all_stock`, `_abort_payment`
- Verified engine and strategy modules contain zero references to Stock/Payment service names
- 130 tests pass (10 new checkout workflow tests + 120 existing)

## Task Commits

Each task was committed atomically:

1. **Task 1: RED -- Write failing tests for checkout workflow definition** - `7cc2f5a` (test)
2. **Task 2: GREEN -- Implement checkout_workflow.py with saga and 2pc factories** - `559641a` (feat)

_Note: TDD plan with RED commit first, then GREEN implementation commit._

## Files Created/Modified

- `orchestrator/checkout_workflow.py` - Checkout workflow factory with SAGA and 2PC step implementations
- `tests/test_checkout_workflow.py` - 10 unit tests covering structure, transport call contracts, and separation of concerns

## Decisions Made

- Module-level async functions (not closures) to avoid Python late-binding risks in loops
- Exactly 2 steps for both strategies (SAGA `STATE_SEQUENCE` has 4 entries: STARTED + 2 transitions + COMPLETED)
- Idempotency key format `{saga:ORDER_ID}:step:reserve:ITEM_ID` matches `grpc_server.py` exactly
- 2PC uses `order_id` directly as the correlation key (matching existing `tpc.py` pattern)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The worktree branch was missing phase 15/16 files (saga_strategy.py, tpc_strategy.py, workflow_types.py, workflow_engine.py). These were on `main` branch. Resolved by fast-forward merging `main` into the worktree branch before execution.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `make_checkout_workflow()` is ready for integration into `grpc_server.py` checkout handler
- `WorkflowEngine.execute(workflow_id, make_checkout_workflow(strategy), context)` is the complete checkout path
- 2PC integration requires TwoPhaseStrategy to correctly handle `_prepare_all_stock` + `_prepare_payment` (note: 2PC strategy sends phase-2 commits using `step.action` again, not separate commit functions -- may need review if full 2PC commit path is needed)

---
*Phase: 16-workflowengine-checkout-definition*
*Completed: 2026-03-27*
