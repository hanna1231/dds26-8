---
phase: 03-saga-orchestration
plan: 03
subsystem: infra
tags: [grpc, saga, orchestrator, docker-compose, redis, python]

# Dependency graph
requires:
  - phase: 03-02
    provides: orchestrator app.py + grpc_server.py with SAGA execution logic
  - phase: 02-grpc-communication
    provides: OrchestratorServiceStub, orchestrator_pb2 stubs (CheckoutRequest/LineItem/CheckoutResponse)

provides:
  - Order /checkout proxied to orchestrator via gRPC StartCheckout (no HTTP fan-out)
  - orchestrator-service container definition with single-worker uvicorn
  - orchestrator-db dedicated Redis instance
  - env/orchestrator_redis.env environment config

affects: [03-04, phase-04-fault-tolerance]

# Tech tracking
tech-stack:
  added: [grpcio==1.78.0, protobuf>=6.31.1 (order service)]
  patterns: [gRPC channel/stub initialized in before_serving hook, closed in after_serving hook]

key-files:
  created: [env/orchestrator_redis.env]
  modified: [order/app.py, order/requirements.txt, docker-compose.yml]

key-decisions:
  - "orchestrator-service runs --workers 1 (single replica to avoid SAGA split-brain per roadmap decision)"
  - "send_post_request() removed from order/app.py — only used by checkout/rollback which SAGA now owns; httpx kept for addItem stock lookup"
  - "orchestrator command is uvicorn app:app (Quart app manages gRPC server as background task), not python grpc_server.py directly"

patterns-established:
  - "gRPC channel lifecycle: global var initialized in before_serving, await channel.close() in after_serving"
  - "SAGA proxy: order fetches order from DB, builds LineItem list, delegates entire checkout to orchestrator gRPC — no local rollback logic"

requirements-completed: [SAGA-03, SAGA-07]

# Metrics
duration: 2min
completed: 2026-02-28
---

# Phase 3 Plan 3: Wire Order to Orchestrator gRPC Summary

**Order /checkout now delegates the entire SAGA checkout flow to the orchestrator via gRPC StartCheckout, replacing HTTP fan-out; orchestrator-service and orchestrator-db added to Docker Compose with single-worker uvicorn and dedicated Redis.**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-28T13:11:16Z
- **Completed:** 2026-02-28T13:13:11Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Replaced order's HTTP fan-out checkout with single gRPC StartCheckout call to orchestrator
- Removed `rollback_stock()` and `send_post_request()` from order/app.py — SAGA orchestrator owns compensation
- Added orchestrator-service (--workers 1, SAGA split-brain safe) and orchestrator-db (dedicated Redis) to docker-compose.yml
- Created env/orchestrator_redis.env following existing env file pattern

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire Order /checkout to orchestrator gRPC and update dependencies** - `c35fa9e` (feat)
2. **Task 2: Add orchestrator service to Docker Compose and create env file** - `42f3bc2` (feat)

**Plan metadata:** _(docs commit follows)_

## Files Created/Modified

- `order/app.py` - gRPC imports + ORCHESTRATOR_ADDR env var + startup/shutdown channel lifecycle + new checkout() using StartCheckout; removed rollback_stock, send_post_request
- `order/requirements.txt` - Added grpcio==1.78.0 and protobuf>=6.31.1
- `docker-compose.yml` - Added orchestrator-service (uvicorn --workers 1, STOCK_GRPC_ADDR, PAYMENT_GRPC_ADDR, depends on orchestrator-db/stock-service/payment-service) and orchestrator-db; updated order-service with ORCHESTRATOR_GRPC_ADDR and depends_on orchestrator-service
- `env/orchestrator_redis.env` - REDIS_HOST=orchestrator-db, REDIS_PORT=6379, REDIS_PASSWORD=redis, REDIS_DB=0

## Decisions Made

- **Single-worker orchestrator:** `--workers 1` enforced on orchestrator-service uvicorn command — aligns with roadmap split-brain decision. SAGA state machine in Redis must have exactly one process driving it per request.
- **uvicorn app:app (not python grpc_server.py):** The orchestrator gRPC server runs as a background task spawned from Quart's before_serving hook; starting via uvicorn correctly manages the event loop lifecycle.
- **send_post_request removed:** Only ever used by checkout flow and rollback_stock; httpx AsyncClient kept because addItem still uses send_get_request for stock price lookup via gateway.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 03-04 (integration tests) can now verify end-to-end SAGA flow through docker-compose
- IDMP-01/02/03 compatibility (Phase 2 Lua idempotency with SAGA idempotency keys) verified in Plan 03-04 Test 10
- Full infrastructure wiring complete: order → orchestrator → stock/payment

---
*Phase: 03-saga-orchestration*
*Completed: 2026-02-28*
