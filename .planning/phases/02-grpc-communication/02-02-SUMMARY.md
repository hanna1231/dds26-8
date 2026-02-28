---
phase: 02-grpc-communication
plan: "02"
subsystem: grpc
tags: [grpc, grpcio, redis, lua, idempotency, msgpack, msgspec, quart, python]

# Dependency graph
requires:
  - phase: 02-01
    provides: "Generated proto3 stubs (stock_pb2.py, stock_pb2_grpc.py, payment_pb2.py, payment_pb2_grpc.py) in service directories"
provides:
  - "stock/grpc_server.py: StockServiceServicer with ReserveStock, ReleaseStock, CheckStock + serve_grpc/stop_grpc_server"
  - "payment/grpc_server.py: PaymentServiceServicer with ChargePayment, RefundPayment, CheckPayment + serve_grpc/stop_grpc_server"
  - "Dual-server startup: HTTP (5000) + gRPC (50051) running concurrently via Quart background tasks"
  - "Lua atomic idempotency deduplication on all mutation RPCs"
affects:
  - 02-03
  - 02-04
  - 03-saga-orchestrator

# Tech tracking
tech-stack:
  added: [grpc.aio (async gRPC server), redis Lua scripting via eval]
  patterns: [Quart add_background_task for concurrent server startup, Lua TOCTOU-safe idempotency, business errors in response fields not gRPC status codes]

key-files:
  created:
    - stock/grpc_server.py
    - payment/grpc_server.py
  modified:
    - stock/app.py
    - stock/Dockerfile
    - payment/app.py
    - payment/Dockerfile

key-decisions:
  - "StockValue and UserValue structs redefined in grpc_server.py rather than imported from app.py — avoids circular import risk, keeps grpc_server.py self-contained"
  - "IDEMPOTENCY_ACQUIRE_LUA uses single atomic Redis eval: GET existing → return cached, else SET '__PROCESSING__' EX 30 → return '__NEW__' — eliminates TOCTOU window"
  - "Business errors (item not found, insufficient stock/credit, in-progress) returned as response fields (success=False + error_message), never via context.abort() or gRPC status codes"
  - "Processing lock TTL is 30s; committed result TTL is 86400s (24h) — matches locked design from 02-CONTEXT.md"

patterns-established:
  - "Idempotency pattern: eval Lua script → decode bytes → handle __PROCESSING__/__NEW__/cached — applied identically to both services"
  - "gRPC server lifecycle: serve_grpc() runs until termination, stop_grpc_server() stops with 5s grace — started via app.add_background_task(serve_grpc, db) in before_serving"
  - "Stock subtract: decrement first, check < 0 after — matches existing remove_stock HTTP behavior in app.py"

requirements-completed: [GRPC-02, GRPC-04]

# Metrics
duration: 6min
completed: 2026-02-28
---

# Phase 2 Plan 02: gRPC Servicer Implementation Summary

**gRPC servicers for Stock and Payment running on port 50051 alongside HTTP with Lua atomic idempotency deduplication on all mutation RPCs**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-28T08:57:28Z
- **Completed:** 2026-02-28T09:03:00Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Implemented StockServiceServicer (ReserveStock, ReleaseStock, CheckStock) with atomic Lua idempotency on mutations
- Implemented PaymentServiceServicer (ChargePayment, RefundPayment, CheckPayment) with the same idempotency pattern
- Both services now start dual HTTP+gRPC servers concurrently via Quart background tasks; all HTTP routes unchanged

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement Stock gRPC servicer with idempotency and dual-server startup** - `c07a350` (feat)
2. **Task 2: Implement Payment gRPC servicer with idempotency and dual-server startup** - `8ba0560` (feat)

**Plan metadata:** `(docs commit follows)`

## Files Created/Modified

- `stock/grpc_server.py` - StockServiceServicer with Lua idempotency, serve_grpc/stop_grpc_server
- `payment/grpc_server.py` - PaymentServiceServicer with Lua idempotency, serve_grpc/stop_grpc_server
- `stock/app.py` - Added grpc_server import, add_background_task(serve_grpc, db) in startup, stop_grpc_server() in shutdown
- `payment/app.py` - Added grpc_server import, add_background_task(serve_grpc, db) in startup, stop_grpc_server() in shutdown
- `stock/Dockerfile` - Added EXPOSE 50051
- `payment/Dockerfile` - Added EXPOSE 50051

## Decisions Made

- Redefined StockValue/UserValue structs in grpc_server.py rather than importing from app.py — keeps grpc_server.py self-contained and avoids potential circular import at module load time
- Idempotency TTLs: 30s processing lock, 86400s (24h) for committed results — consistent with plan design decisions
- Business errors exclusively in response fields; no gRPC status codes for domain failures — SAGA orchestrator in Phase 3 will inspect success/error_message fields

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Stock and Payment services ready to receive gRPC calls on port 50051
- Idempotency layer ready for SAGA orchestrator retries (Phase 3)
- Both Dockerfiles expose 50051 — docker-compose/Kubernetes manifests will need port mappings added in next deployment plan

---
*Phase: 02-grpc-communication*
*Completed: 2026-02-28*
