---
phase: 11-2pc-state-machine-participants
plan: 02
subsystem: payments, stock
tags: [2pc, lua, redis, cas, hold-key, idempotent, atomic]

requires:
  - phase: 08-business-logic-extraction
    provides: stock/operations.py and payment/operations.py with CAS Lua patterns
provides:
  - prepare_stock, commit_stock, abort_stock functions in stock/operations.py
  - prepare_payment, commit_payment, abort_payment functions in payment/operations.py
  - Hold key pattern for tentative reservations ({item:<id>}:hold:<order_id>, {user:<id>}:hold:<order_id>)
affects: [12-2pc-coordinator, 13-integration]

tech-stack:
  added: []
  patterns: [2PC hold key with 7-day TTL safety net, CAS Lua for atomic deduct+hold, idempotency via hold key existence]

key-files:
  created: [tests/test_tpc_participants.py]
  modified: [stock/operations.py, payment/operations.py]

key-decisions:
  - "Hold key stores quantity/amount as string value for abort restoration"
  - "Idempotency via hold key EXISTS check (no separate idempotency keys for 2PC ops)"
  - "COMMIT is always idempotent (DEL on missing key is no-op)"
  - "ABORT returns success when hold key already gone (ALREADY_ABORTED)"

patterns-established:
  - "2PC hold key pattern: {resource:<id>}:hold:<order_id> with 604800s TTL"
  - "CAS retry loop for prepare/abort; single-shot Lua for commit"

requirements-completed: [TPC-02, TPC-03]

duration: 4min
completed: 2026-03-12
---

# Phase 11 Plan 02: 2PC Participant Operations Summary

**Stock and payment 2PC participant operations (prepare/commit/abort) with Lua CAS scripts and hold key idempotency**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-12T10:12:34Z
- **Completed:** 2026-03-12T10:16:38Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- 13 tests covering all 2PC participant behaviors (prepare/commit/abort for stock and payment)
- Atomic prepare operations that deduct balance and create hold keys in a single Lua eval
- Full idempotency: duplicate prepare returns ALREADY_PREPARED, duplicate commit/abort safe
- All 67 tests in full suite pass (no regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Write failing tests for stock and payment 2PC participant operations** - `6d4baa4` (test)
2. **Task 2: Implement stock 2PC participant operations** - `6cd19bf` (feat)
3. **Task 3: Implement payment 2PC participant operations** - `edabe20` (feat)

_Note: TDD flow -- Task 1 is RED (failing tests), Tasks 2-3 are GREEN (implementation)_

## Files Created/Modified
- `tests/test_tpc_participants.py` - 13 tests for stock and payment 2PC prepare/commit/abort
- `stock/operations.py` - Added prepare_stock, commit_stock, abort_stock with Lua CAS scripts
- `payment/operations.py` - Added prepare_payment, commit_payment, abort_payment with Lua CAS scripts

## Decisions Made
- Hold key stores quantity/amount as plain string (simple, sufficient for abort restoration)
- Idempotency achieved via hold key EXISTS check rather than separate idempotency keys (simpler than SAGA pattern, appropriate for 2PC where coordinator retries)
- COMMIT always returns success (DEL on missing key is inherently idempotent)
- ABORT returns success when hold key already gone (safe for coordinator retries)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Stock and payment 2PC participants ready for coordinator (Phase 12) to call
- Hold key format established: {item:<id>}:hold:<order_id> and {user:<id>}:hold:<order_id>
- Function signatures: prepare_X(db, id, amount, order_id), commit_X(db, id, order_id), abort_X(db, id, order_id)

---
*Phase: 11-2pc-state-machine-participants*
*Completed: 2026-03-12*
