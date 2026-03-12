---
phase: 12-2pc-coordinator-recovery
plan: 02
subsystem: orchestrator
tags: [2pc, coordinator, recovery, wal, asyncio, redis]

requires:
  - phase: 12-01
    provides: "2PC transport wrappers (prepare/commit/abort for stock and payment)"
  - phase: 11-01
    provides: "TPC state machine (create_tpc_record, transition_tpc_state, get_tpc)"
  - phase: 11-02
    provides: "2PC participant operations (prepare/commit/abort in stock and payment)"
provides:
  - "run_2pc_checkout coordinator with concurrent PREPARE, WAL, phase-2 commit/abort"
  - "2PC recovery scanner (recover_incomplete_tpc, resume_tpc)"
  - "TRANSACTION_PATTERN env var toggle between SAGA and 2PC"
  - "Unified recovery in app.py (SAGA + TPC)"
affects: [13-integration-testing]

tech-stack:
  added: []
  patterns: [WAL-before-phase-2, concurrent-prepare-via-asyncio-gather, presumed-abort-recovery]

key-files:
  created:
    - tests/test_2pc_coordinator.py
  modified:
    - orchestrator/grpc_server.py
    - orchestrator/recovery.py
    - orchestrator/app.py

key-decisions:
  - "Patch transport module for recovery tests since resume_tpc uses lazy imports"
  - "WAL pattern: persist COMMITTING/ABORTING state before sending phase-2 messages"
  - "Presumed abort: INIT and PREPARING states both recovered to ABORTED"

patterns-established:
  - "WAL-before-phase-2: Always persist decision state before sending commit/abort messages"
  - "Concurrent 2PC: asyncio.gather for all prepare calls, asyncio.gather for all phase-2 calls"
  - "TRANSACTION_PATTERN toggle: env var switches coordinator path without code changes"

requirements-completed: [TPC-04, TPC-05, TPC-06, TPC-07]

duration: 5min
completed: 2026-03-12
---

# Phase 12 Plan 02: 2PC Coordinator & Recovery Summary

**2PC coordinator with concurrent PREPARE, WAL decision persistence, crash recovery scanner, and SAGA/2PC toggle**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-12T10:51:27Z
- **Completed:** 2026-03-12T10:56:19Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Implemented run_2pc_checkout with concurrent PREPARE via asyncio.gather, WAL-before-phase-2, and exactly-once semantics
- Added 2PC recovery scanner handling INIT/PREPARING (presumed abort), COMMITTING (re-commit), and ABORTING (re-abort)
- TRANSACTION_PATTERN env var toggle routes StartCheckout to SAGA or 2PC coordinator
- 12 new tests (79 total) all passing with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Write tests for 2PC coordinator, WAL, recovery, and toggle** - `e1321ab` (test)
2. **Task 2: Implement run_2pc_checkout, 2PC recovery, and TRANSACTION_PATTERN toggle** - `22575e3` (feat)

_TDD workflow: RED (failing tests) then GREEN (implementation)_

## Files Created/Modified
- `tests/test_2pc_coordinator.py` - 12 unit tests for coordinator, WAL ordering, recovery, and toggle
- `orchestrator/grpc_server.py` - run_2pc_checkout function, TRANSACTION_PATTERN routing, 2PC transport imports
- `orchestrator/recovery.py` - resume_tpc and recover_incomplete_tpc for 2PC crash recovery
- `orchestrator/app.py` - Unified recovery calling both SAGA and TPC scanners on startup

## Decisions Made
- Patched transport module (not recovery module) for recovery tests since resume_tpc uses lazy imports inside the function
- WAL pattern persists COMMITTING/ABORTING decision BEFORE sending phase-2 messages to ensure crash safety
- Recovery uses presumed abort for both INIT and PREPARING states (INIT transitions through PREPARING first)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed mock patch targets for recovery tests**
- **Found during:** Task 2 (running tests)
- **Issue:** Plan specified patching "recovery.abort_stock" but resume_tpc uses lazy import so functions are local variables, not module attributes
- **Fix:** Changed patch targets from "recovery.*" to "transport.*" for all recovery test patches
- **Files modified:** tests/test_2pc_coordinator.py
- **Verification:** All 12 tests pass
- **Committed in:** 22575e3 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Fix necessary for tests to work with lazy imports. No scope creep.

## Issues Encountered
None beyond the patch target fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 2PC protocol is complete end-to-end: state machine, participants, transport, coordinator, recovery
- TRANSACTION_PATTERN toggle enables seamless switching between SAGA and 2PC
- Ready for Phase 13 integration testing

---
*Phase: 12-2pc-coordinator-recovery*
*Completed: 2026-03-12*
