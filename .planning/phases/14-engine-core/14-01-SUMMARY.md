---
phase: 14-engine-core
plan: "01"
subsystem: orchestrator
tags: [workflow, dataclasses, redis, lua-cas, tdd]
dependency_graph:
  requires: []
  provides: [WorkflowStep, WorkflowDefinition, WorkflowStore]
  affects: [orchestrator/workflow_types.py, orchestrator/workflow_store.py, tests/test_workflow_store.py]
tech_stack:
  added: []
  patterns: [dataclasses, lua-cas, hsetnx-exactly-once, hash-tag-cluster-locality, manual-byte-decode]
key_files:
  created:
    - orchestrator/workflow_types.py
    - orchestrator/workflow_store.py
    - tests/test_workflow_store.py
  modified: []
decisions:
  - "WorkflowStore as class (not module functions) to pre-align with REF-03 injectable dependency pattern in Phase 16"
  - "TRANSITION_LUA extracted verbatim from saga.py:42-49 -- identical to tpc.py:43-50, zero modification"
  - "workflow_store.py stub created first to allow test collection when both imports at module top-level"
  - "15 tests total (5 type + 10 store) -- plan mentioned 16 but behavior section defined 10 store tests"
metrics:
  duration: "7min"
  completed: "2026-03-27"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
requirements:
  - ENG-01
  - ENG-02
  - ENG-04
  - ENG-05
---

# Phase 14 Plan 01: Engine Core -- WorkflowStep, WorkflowDefinition, WorkflowStore Summary

**One-liner:** Generic Redis-persisted WorkflowStore with Lua CAS and WorkflowStep/WorkflowDefinition dataclasses extracted from saga.py/tpc.py patterns.

## What Was Built

Three new files created, zero existing files modified:

1. **`orchestrator/workflow_types.py`** -- Two minimal dataclasses:
   - `WorkflowStep(name, action, compensation)` -- named pair of async callables typed as `Callable[..., Awaitable[Any]]`
   - `WorkflowDefinition(name, steps, strategy)` -- ordered steps list with `field(default_factory=list)` and `Literal["saga", "2pc"]` strategy defaulting to `"saga"`

2. **`orchestrator/workflow_store.py`** -- `WorkflowStore` class with:
   - `TRANSITION_LUA` -- verbatim extraction from `saga.py:42-49` (identical in `tpc.py:43-50`)
   - `create(workflow_id, initial_state, metadata)` -- HSETNX exactly-once + hset + 7-day expire
   - `transition(workflow_id, from_state, to_state, flag_field, flag_value)` -- `db.eval()` Lua CAS
   - `mark_step_done(workflow_id, step_index)` -- writes `step_N_done = "1"` flat hash field
   - `get(workflow_id)` -- `hgetall` + manual `k.decode(): v.decode()` (no `decode_responses`)
   - Key prefix: `{workflow:<workflow_id>}` per D-01

3. **`tests/test_workflow_store.py`** -- 15 tests covering all 4 requirements:
   - 5 type tests (ENG-01, ENG-02): fields, async callables, strategy defaults, independent steps
   - 10 integration tests (ENG-04, ENG-05): create, duplicate, metadata, TTL, transition valid/mismatch/with_flag, mark_step_done, multiple steps, get_nonexistent

## TDD Execution

**Task 1 (RED):** Test file written with all 15 tests + stub `workflow_store.py` to allow collection. Type tests fail on missing `workflow_types` module.

**Task 1 (GREEN):** `orchestrator/workflow_types.py` created. 5 type tests pass.

**Task 2 (RED):** Store tests fail against stub `WorkflowStore` (no `create()` attribute).

**Task 2 (GREEN):** Full `WorkflowStore` implementation. All 15 tests pass.

## Verification

```
python3 -m pytest tests/test_workflow_store.py -x -v
# 15 passed in 0.07s

python3 -m pytest tests/test_workflow_store.py tests/test_saga.py -x -v
# 25 passed in 1.05s (no regressions)
```

## Requirements Satisfied

- ENG-01: WorkflowStep dataclass with name, action, compensation -- tested by test_workflow_step_fields, test_workflow_step_callables_async
- ENG-02: WorkflowDefinition dataclass with name, steps, strategy -- tested by test_workflow_definition_fields, test_workflow_definition_strategy, test_workflow_definition_independent_steps
- ENG-04: Redis Lua CAS persistence -- tested by test_workflow_store_create, create_duplicate, create_ttl, transition_valid, transition_mismatch, transition_with_flag
- ENG-05: Per-step completion flags -- tested by test_workflow_store_mark_step_done, test_workflow_store_multiple_steps

## Decisions Made

1. **WorkflowStore as class (not module functions):** Pre-aligns with Phase 16 REF-03 injectable dependency. Cost is ~5 lines of boilerplate.

2. **TRANSITION_LUA verbatim copy:** Byte-for-byte identical to saga.py:42-49 and tpc.py:43-50. No modification needed; production-proven.

3. **Stub before implementation:** Test file imports both `workflow_types` and `workflow_store` at module level. Created stub `workflow_store.py` first to allow pytest collection for Task 1 type tests without breaking the single-file approach.

4. **15 tests not 16:** Plan said 16 but the behavior section defined 10 store test functions. 5 type + 10 store = 15 actual tests. All listed test functions are present and passing.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] Created workflow_store stub to enable test collection**
- **Found during:** Task 1 RED phase
- **Issue:** Test file imports both `workflow_types` and `workflow_store` at module top level. Python collection fails if either module is missing, preventing Task 1 type tests from running independently.
- **Fix:** Created minimal stub `workflow_store.py` with empty `WorkflowStore` class to allow test collection. Task 1 type tests ran and passed. Stub was then replaced with full implementation in Task 2.
- **Files modified:** `orchestrator/workflow_store.py`
- **Commit:** 29db409

## Known Stubs

None -- all 3 created files are fully implemented with passing tests.

## Self-Check: PASSED

Files exist:
- FOUND: orchestrator/workflow_types.py
- FOUND: orchestrator/workflow_store.py
- FOUND: tests/test_workflow_store.py
- FOUND: .planning/phases/14-engine-core/14-01-SUMMARY.md

Commits:
- 4a792c8 test(14-01): add failing tests
- 29db409 feat(14-01): implement WorkflowStep and WorkflowDefinition dataclasses
- b5b6628 feat(14-01): implement WorkflowStore with Lua CAS
