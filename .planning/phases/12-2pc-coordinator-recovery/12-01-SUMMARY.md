---
phase: 12-2pc-coordinator-recovery
plan: 01
subsystem: api
tags: [grpc, protobuf, 2pc, transport, redis-streams]

# Dependency graph
requires:
  - phase: 11-2pc-state-machine-participants
    provides: "2PC participant operations (prepare/commit/abort) in stock and payment"
provides:
  - "6 new 2PC RPCs in stock.proto and payment.proto"
  - "gRPC servicer handlers for all 6 2PC RPCs"
  - "Queue consumer dispatch for all 6 2PC commands"
  - "gRPC client wrappers with circuit breakers for 2PC"
  - "Queue client wrappers for 2PC with order_id payloads"
  - "Transport adapter re-exports for all 6 2PC functions"
affects: [12-02-coordinator, orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns: ["2PC transport wrappers follow same pattern as SAGA wrappers"]

key-files:
  created: []
  modified:
    - protos/stock.proto
    - protos/payment.proto
    - stock/grpc_server.py
    - payment/grpc_server.py
    - stock/queue_consumer.py
    - payment/queue_consumer.py
    - orchestrator/client.py
    - orchestrator/queue_client.py
    - orchestrator/transport.py

key-decisions:
  - "2PC transport wrappers use order_id (not idempotency_key) matching operations.py signatures"

patterns-established:
  - "2PC functions follow identical pattern to SAGA functions across all transport layers"

requirements-completed: [TPC-04, TPC-07]

# Metrics
duration: 3min
completed: 2026-03-12
---

# Phase 12 Plan 01: 2PC Transport Layer Summary

**Extended gRPC protos, servicers, queue consumers, client wrappers, and transport adapter with prepare/commit/abort operations for both Stock and Payment services**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-12T10:45:42Z
- **Completed:** 2026-03-12T10:48:42Z
- **Tasks:** 2
- **Files modified:** 17 (including regenerated pb2 files)

## Accomplishments
- Added 6 new 2PC RPCs (PrepareStock/CommitStock/AbortStock, PreparePayment/CommitPayment/AbortPayment) to proto definitions
- Extended all transport paths (gRPC servicers, queue consumers, gRPC client, queue client, transport adapter)
- All 67 tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add 2PC RPCs to protos, regenerate pb2 files, extend gRPC servicers and queue consumers** - `190b614` (feat)
2. **Task 2: Add 2PC wrappers to gRPC client, queue client, and transport adapter** - `a252124` (feat)

## Files Created/Modified
- `protos/stock.proto` - Added PrepareStock/CommitStock/AbortStock RPCs and request messages
- `protos/payment.proto` - Added PreparePayment/CommitPayment/AbortPayment RPCs and request messages
- `stock/stock_pb2.py`, `stock/stock_pb2_grpc.py` - Regenerated from updated proto
- `payment/payment_pb2.py`, `payment/payment_pb2_grpc.py` - Regenerated from updated proto
- `orchestrator/stock_pb2.py`, `orchestrator/stock_pb2_grpc.py` - Copied from stock service
- `orchestrator/payment_pb2.py`, `orchestrator/payment_pb2_grpc.py` - Copied from payment service
- `stock/grpc_server.py` - Added 3 new RPC handlers delegating to operations
- `payment/grpc_server.py` - Added 3 new RPC handlers delegating to operations
- `stock/queue_consumer.py` - Added 3 new COMMAND_DISPATCH entries for 2PC
- `payment/queue_consumer.py` - Added 3 new COMMAND_DISPATCH entries for 2PC
- `orchestrator/client.py` - Added 6 gRPC client wrappers with circuit breakers
- `orchestrator/queue_client.py` - Added 6 queue client wrappers
- `orchestrator/transport.py` - Extended conditional imports and __all__ with 6 new functions
- `tests/test_transport_adapter.py` - Updated DOMAIN_FUNCTIONS list

## Decisions Made
- 2PC transport wrappers use order_id (not idempotency_key) to match the operations.py 2PC function signatures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated transport adapter test to include new 2PC exports**
- **Found during:** Task 2 (transport adapter updates)
- **Issue:** test_all_expected_names_exported had hardcoded DOMAIN_FUNCTIONS list that didn't include the 6 new 2PC functions
- **Fix:** Added prepare_stock, commit_stock, abort_stock, prepare_payment, commit_payment, abort_payment to DOMAIN_FUNCTIONS
- **Files modified:** tests/test_transport_adapter.py
- **Verification:** All 67 tests pass
- **Committed in:** a252124 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test update was necessary for correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Self-Check: PASSED

All 11 files verified present. Both task commits (190b614, a252124) verified in git log.

## Next Phase Readiness
- All 6 2PC transport functions are available via transport adapter
- Ready for Plan 02 to implement the 2PC coordinator using these transport-agnostic functions

---
*Phase: 12-2pc-coordinator-recovery*
*Completed: 2026-03-12*
