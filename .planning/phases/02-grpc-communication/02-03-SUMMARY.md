---
phase: 02-grpc-communication
plan: "03"
subsystem: grpc
tags: [grpc, grpcio, python, async, orchestrator, saga]

# Dependency graph
requires:
  - phase: 02-grpc-communication
    provides: "Generated protobuf stubs (stock_pb2, payment_pb2, stock_pb2_grpc, payment_pb2_grpc) in orchestrator/"
provides:
  - "orchestrator/client.py — thin async gRPC client with six wrapper functions and single-channel reuse"
  - "orchestrator/requirements.txt — orchestrator Python dependency manifest"
affects:
  - 03-saga-orchestrator
  - 04-fault-tolerance

# Tech tracking
tech-stack:
  added: [grpcio==1.78.0, protobuf>=6.31.1]
  patterns:
    - "Module-level channel globals initialised once in init_grpc_clients() — no per-call channel creation"
    - "Wrapper functions return plain dicts (success, error_message) — gRPC exceptions propagate to caller"
    - "Idempotency key passed through on all mutation RPCs; read-only RPCs have no idempotency_key param"

key-files:
  created:
    - orchestrator/client.py
    - orchestrator/requirements.txt
  modified: []

key-decisions:
  - "gRPC exceptions (grpc.aio.AioRpcError) intentionally not caught here — Phase 4 adds circuit breaker handling at orchestrator level"
  - "RPC_TIMEOUT = 5.0s as module constant — matches locked decision from 02-CONTEXT.md"
  - "init_grpc_clients() accepts optional address overrides for test-time injection without env var manipulation"

patterns-established:
  - "gRPC client pattern: global stub instances initialised in lifecycle hook, closed in shutdown hook"
  - "Wrapper return shape: {success: bool, error_message: str} for mutations; extra fields appended for check ops"

requirements-completed: [GRPC-03, GRPC-04]

# Metrics
duration: 1min
completed: "2026-02-28"
---

# Phase 2 Plan 03: Orchestrator gRPC Client Module Summary

**Thin async gRPC client wrapping all Stock and Payment RPCs with idempotency key pass-through and single-channel reuse per service**

## Performance

- **Duration:** 1 min
- **Started:** 2026-02-28T09:02:03Z
- **Completed:** 2026-02-28T09:03:00Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- Created `orchestrator/client.py` with six async wrapper functions: `reserve_stock`, `release_stock`, `check_stock`, `charge_payment`, `refund_payment`, `check_payment`
- All four mutation wrappers accept and forward `idempotency_key`; read-only wrappers do not
- All six calls use `timeout=RPC_TIMEOUT` (5.0 s); channels are reused via module-level globals
- Created `orchestrator/requirements.txt` listing grpcio and protobuf dependencies

## Task Commits

Each task was committed atomically:

1. **Task 1: Create orchestrator gRPC client module** - `a1092cd` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `orchestrator/client.py` — Async gRPC client module with `init_grpc_clients`, `close_grpc_clients`, and six wrapper functions
- `orchestrator/requirements.txt` — Orchestrator Python dependencies (grpcio, protobuf, pytest)

## Decisions Made

- gRPC transport errors (`grpc.aio.AioRpcError`) are intentionally not caught in this module — Phase 4 adds circuit breaker handling at the SAGA orchestrator layer
- `init_grpc_clients()` accepts optional `stock_addr`/`payment_addr` overrides to facilitate unit testing without environment variable manipulation
- `RPC_TIMEOUT = 5.0` as a named module constant (not an inline literal) so Phase 4 can import and reference it

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `orchestrator/client.py` is ready to be imported by the Phase 3 SAGA orchestrator
- Phase 3 should call `init_grpc_clients()` in the application startup hook and `close_grpc_clients()` in shutdown
- The module surfaces raw `grpc.aio.AioRpcError` exceptions — Phase 4 should wrap calls with circuit breaker / retry logic

---
*Phase: 02-grpc-communication*
*Completed: 2026-02-28*
