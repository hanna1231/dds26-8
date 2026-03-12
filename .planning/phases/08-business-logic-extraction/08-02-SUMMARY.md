---
phase: 08-business-logic-extraction
plan: 02
subsystem: payments
tags: [redis, lua, msgpack, grpc, idempotency, cas]

# Dependency graph
requires:
  - phase: none
    provides: existing payment service with business logic in grpc_server.py
provides:
  - payment/operations.py with transport-independent charge/refund/check functions
  - thin grpc_server.py adapter delegating to operations module
affects: [09-queue-consumers, 11-2pc-participants]

# Tech tracking
tech-stack:
  added: []
  patterns: [operations-module extraction, thin-adapter gRPC servicer]

key-files:
  created: [payment/operations.py]
  modified: [payment/grpc_server.py, payment/app.py, tests/conftest.py]

key-decisions:
  - "Return plain dicts from operations functions for transport independence"
  - "Clear operations module from sys.modules in conftest to avoid cross-service module cache collision"

patterns-established:
  - "Operations module pattern: business logic in operations.py, gRPC servicer as thin adapter"
  - "Dict return convention: operations return {'success': bool, 'error_message': str} dicts"

requirements-completed: [BLE-02]

# Metrics
duration: 4min
completed: 2026-03-12
---

# Phase 8 Plan 2: Payment Business Logic Extraction Summary

**Payment charge/refund/check logic extracted to operations.py with CAS loops, Lua scripts, and idempotency -- grpc_server.py reduced to 3-line adapter methods**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-12T07:16:19Z
- **Completed:** 2026-03-12T07:20:17Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Extracted all payment business logic (charge, refund, check) into payment/operations.py with plain dict returns
- Converted payment/grpc_server.py from 186-line monolith to 49-line thin adapter with zero Lua scripts, zero Redis calls, zero msgpack usage
- Updated app.py to import UserValue from the single source of truth in operations.py
- Fixed test conftest.py sys.modules cache collision between stock and payment operations modules

## Task Commits

Each task was committed atomically:

1. **Task 1: Create payment/operations.py with all business logic** - `6259eb3` (feat)
2. **Task 2: Refactor payment/grpc_server.py to thin adapter and update app.py import** - `ae9e351` (refactor)

## Files Created/Modified
- `payment/operations.py` - All payment business logic: UserValue, Lua scripts, charge/refund/check async functions
- `payment/grpc_server.py` - Thin gRPC adapter delegating to operations module
- `payment/app.py` - Updated UserValue import from operations module
- `tests/conftest.py` - Clear operations from sys.modules cache between stock and payment imports

## Decisions Made
- Return plain dicts from operations functions instead of protobuf types, enabling any transport layer to consume them
- Clear operations module from sys.modules in conftest.py to prevent stock's operations module from shadowing payment's during test collection

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed sys.modules cache collision for operations module in conftest.py**
- **Found during:** Task 2 (integration test verification)
- **Issue:** Stock's grpc_server.py also imports operations; when conftest imports stock first, Python caches stock/operations.py as the "operations" module. Payment's grpc_server.py then gets stock's operations instead of its own.
- **Fix:** Added `operations` to the list of modules cleared from sys.modules before importing payment's grpc_server
- **Files modified:** tests/conftest.py
- **Verification:** All 7 integration tests pass
- **Committed in:** ae9e351 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential fix for test infrastructure -- both services now use operations.py pattern and need module cache isolation.

## Issues Encountered
- Pre-existing saga test failure (test_checkout_insufficient_credit_compensates) unrelated to payment extraction -- confirmed fails on unmodified code too. Out of scope.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Payment operations module ready for queue consumers (Phase 9) and 2PC participants (Phase 11)
- Same operations extraction pattern established for both stock and payment services
- All integration tests pass with no modifications to test logic

---
*Phase: 08-business-logic-extraction*
*Completed: 2026-03-12*
