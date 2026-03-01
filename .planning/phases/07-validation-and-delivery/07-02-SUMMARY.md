---
phase: 07-validation-and-delivery
plan: 02
subsystem: testing
tags: [kill-test, consistency, saga, recovery, docker-compose, automation]

# Dependency graph
requires:
  - phase: 04-fault-tolerance
    provides: recover_incomplete_sagas with STALENESS_THRESHOLD_SECONDS constant
  - phase: 03-saga-orchestration
    provides: SAGA state machine and idempotent forward/compensation replay
provides:
  - Configurable STALENESS_THRESHOLD_SECONDS via SAGA_STALENESS_SECONDS env var
  - scripts/kill_test.py automated kill-container consistency test for all four services
  - Makefile kill-test and kill-test-all targets
affects: [07-validation-and-delivery]

# Tech tracking
tech-stack:
  added: [aiohttp, requests (for kill_test.py async checkout concurrency)]
  patterns:
    - "Kill-test pattern: populate -> concurrent checkout -> kill service -> wait 30s -> assert credits_deducted == stock_consumed"
    - "Env var passthrough: SAGA_STALENESS_SECONDS=${SAGA_STALENESS_SECONDS:-300} in docker-compose.yml"

key-files:
  created:
    - scripts/kill_test.py
  modified:
    - orchestrator/recovery.py
    - Makefile
    - docker-compose.yml

key-decisions:
  - "SAGA_STALENESS_SECONDS passed via docker-compose.yml env var override (not docker exec patch) — simplest approach, works at container launch"
  - "kill-test-all manages cluster lifecycle internally (docker compose down -v + SAGA_STALENESS_SECONDS=10 up) per --all flag"
  - "kill-test Makefile target starts cluster with SAGA_STALENESS_SECONDS=10 so orchestrator recovery scanner uses 10s staleness threshold enabling 30s recovery window"

patterns-established:
  - "Consistency invariant: credits_deducted == stock_consumed (regardless of which SAGAs completed vs compensated)"
  - "Recovery window: 30 seconds after service restart before asserting consistency (matches CONTEXT.md decision)"

requirements-completed: [TEST-03]

# Metrics
duration: 7min
completed: 2026-03-01
---

# Phase 7 Plan 02: Kill-Test Automation Summary

**Automated kill-container consistency tests using credits_deducted == stock_consumed invariant, with SAGA_STALENESS_SECONDS env var making recovery staleness configurable for 30-second kill-test windows**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-01T08:58:20Z
- **Completed:** 2026-03-01T09:05:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Made `STALENESS_THRESHOLD_SECONDS` configurable via `SAGA_STALENESS_SECONDS` env var (default 300s, kill-test uses 10s)
- Created `scripts/kill_test.py` with automated kill-container tests for all four services (order, stock, payment, orchestrator)
- Added `kill-test` and `kill-test-all` Makefile targets; passed `SAGA_STALENESS_SECONDS` through docker-compose.yml

## Task Commits

Each task was committed atomically:

1. **Task 1: Make STALENESS_THRESHOLD_SECONDS configurable via env var** - `a94fc54` (feat)
2. **Task 2: Create automated kill-test script and Makefile target** - `f4c549c` (feat)

## Files Created/Modified

- `orchestrator/recovery.py` - Added `import os`; changed constant to `int(os.environ.get('SAGA_STALENESS_SECONDS', '300'))`
- `scripts/kill_test.py` - New executable: populate, fire concurrent checkouts, kill service, wait 30s, assert_consistency
- `Makefile` - Added `kill-test` (single service) and `kill-test-all` (all services) targets; updated .PHONY
- `docker-compose.yml` - Added `SAGA_STALENESS_SECONDS=${SAGA_STALENESS_SECONDS:-300}` to orchestrator-service environment

## Decisions Made

- **SAGA_STALENESS_SECONDS passthrough via docker-compose.yml:** The orchestrator container needed to receive the `SAGA_STALENESS_SECONDS` env var for the 30-second recovery window to work (otherwise the 5-minute staleness threshold would skip all kill-test SAGAs). Added `- SAGA_STALENESS_SECONDS=${SAGA_STALENESS_SECONDS:-300}` to docker-compose.yml orchestrator environment — simplest approach that works at container launch without requiring `docker exec` patching.
- **kill-test-all cluster lifecycle:** The `--all` flag manages cluster lifecycle internally (down -v + up with SAGA_STALENESS_SECONDS=10) rather than requiring the Makefile to orchestrate multi-service tests.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added SAGA_STALENESS_SECONDS env var passthrough to docker-compose.yml**
- **Found during:** Task 2 (Create kill-test script and Makefile target)
- **Issue:** The plan noted that docker-compose.yml needed to forward `SAGA_STALENESS_SECONDS` to the orchestrator container, but this wasn't in the Makefile task files list. Without this passthrough, `SAGA_STALENESS_SECONDS=10` set in the shell would never reach the orchestrator container, making kill-tests for the orchestrator-kill scenario non-functional.
- **Fix:** Added `- SAGA_STALENESS_SECONDS=${SAGA_STALENESS_SECONDS:-300}` to the orchestrator-service environment section in docker-compose.yml
- **Files modified:** `docker-compose.yml`
- **Verification:** Variable present in orchestrator-service environment, defaults to 300 in production
- **Committed in:** `f4c549c` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Auto-fix essential for kill-test correctness. Without it, SAGA_STALENESS_SECONDS=10 would never reach the orchestrator container. No scope creep.

## Issues Encountered

None — plan executed with one auto-fix for the env var passthrough that was anticipated in the plan's "At Claude's discretion" note.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Kill-test automation complete — all four services can be tested individually with `make kill-test SERVICE=<name>` or all at once with `make kill-test-all`
- Prerequisite: cluster must be running (start with `make dev-up`) before `make kill-test SERVICE=<name>`; `make kill-test-all` manages its own cluster lifecycle
- Ready for Phase 7 Plan 03: benchmark load testing

---
*Phase: 07-validation-and-delivery*
*Completed: 2026-03-01*
