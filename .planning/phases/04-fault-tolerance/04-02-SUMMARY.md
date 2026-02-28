---
phase: 04-fault-tolerance
plan: 02
subsystem: testing
tags: [circuit-breaker, fault-tolerance, saga-recovery, retry, pytest, grpc, redis]
dependency_graph:
  requires:
    - phase: 04-01
      provides: "circuit.py (stock_breaker, payment_breaker), recovery.py (recover_incomplete_sagas), grpc_server.py (retry_forward, run_checkout)"
  provides:
    - "12 fault tolerance tests covering FAULT-01 through FAULT-04"
    - "requires_docker pytest marker for future Docker-dependent tests"
  affects: [tests/test_fault_tolerance.py, tests/conftest.py]
tech-stack:
  added: []
  patterns: [circuit-breaker state manipulation for testing, monotonic-time patching for half-open, seed_saga helper for Redis test setup]
key-files:
  created:
    - tests/test_fault_tolerance.py
  modified:
    - tests/conftest.py
key-decisions:
  - "Use _open_breaker() helper (directly sets _state=STATE_OPEN) to avoid slow threshold-loop for most circuit breaker tests"
  - "Half-open recovery tested by setting _opened to past monotonic time rather than sleeping RECOVERY_TIMEOUT seconds"
  - "seed_saga() helper injects SAGA records directly into Redis with arbitrary state and timestamps for recovery scanner tests"
  - "AioRpcError constructed with grpc.aio.Metadata() instances for threshold-trip test (only test needing real gRPC error propagation)"
patterns-established:
  - "Circuit breaker state reset: always use breaker.reset() in finally block to prevent cross-test pollution"
  - "Recovery tests use stale_ts = int(time.time()) - 600 to guarantee STALENESS_THRESHOLD_SECONDS (300s) is exceeded"
requirements-completed: [FAULT-01, FAULT-02, FAULT-03, FAULT-04]
duration: 5min
completed: "2026-02-28"
---

# Phase 04 Plan 02: Fault Tolerance Tests Summary

**12 pytest tests proving circuit breaker tripping/recovery, bounded retry exhaustion and CircuitBreakerError propagation, startup SAGA recovery of all non-terminal states, and post-recovery terminal-state consistency**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-02-28T14:10:00Z
- **Completed:** 2026-02-28T14:15:00Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- 12 tests for FAULT-01 through FAULT-04 all pass in 0.14 seconds (no real backoff waits)
- Circuit breaker tests use direct `_state`/`_opened` manipulation to avoid slow threshold loops and sleep delays
- Recovery tests use `seed_saga()` helper to inject arbitrary SAGA state directly into Redis, enabling all non-terminal state paths
- `requires_docker` marker registered in conftest.py for future Docker-dependent kill tests
- All 10 existing `test_saga.py` tests continue to pass (no regression)

## Task Commits

Each task was committed atomically:

1. **Task 1: Register requires_docker marker and write fault tolerance tests** - `1a78053` (test)

## Files Created/Modified

- `tests/test_fault_tolerance.py` - 12 fault tolerance tests: circuit breaker (4), retry_forward (3), recovery scanner (5)
- `tests/conftest.py` - Added `pytest_configure` with `requires_docker` marker registration

## Decisions Made

- Used `_open_breaker()` helper that sets `_state=STATE_OPEN` directly for most tests — avoids slow threshold-loop that would require real gRPC errors
- Only `test_circuit_breaker_trips_after_threshold` actually calls through the decorated function with patched `AioRpcError` to verify the real threshold path
- Half-open recovery tested by setting `_opened = monotonic() - RECOVERY_TIMEOUT - 1` — eliminates 30-second sleep while correctly triggering half-open state
- `seed_saga()` helper seeds Redis directly rather than going through `create_saga_record` — allows injecting arbitrary states (COMPENSATING, STOCK_RESERVED) that are not initial states

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed stray import line from half-open recovery test**
- **Found during:** Task 1 (test_circuit_breaker_half_open_recovery)
- **Issue:** `from stock_pb2 import ReserveStockResponse` — proto generates no `ReserveStockResponse` class (only `ReserveStockRequest`); added as a placeholder comment check and caused ImportError
- **Fix:** Removed the import line; `MagicMock()` already fully mocks the response
- **Files modified:** tests/test_fault_tolerance.py
- **Verification:** Test passes after removal
- **Committed in:** 1a78053 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — stray import)
**Impact on plan:** Minor — fixed before test run completed. No scope creep.

## Issues Encountered

- `stock_pb2.ReserveStockResponse` does not exist (proto only generates `Request` types, not `Response` types at the stub level in this codebase). Fixed by using `MagicMock()` for the success response directly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- FAULT-01 through FAULT-04 requirements fully verified by test suite
- Phase 4 (Fault Tolerance) is now complete — both implementation (04-01) and test (04-02) plans done
- Ready to proceed to Phase 5 (Event-Driven) or Phase 6 depending on roadmap

---
*Phase: 04-fault-tolerance*
*Completed: 2026-02-28*
