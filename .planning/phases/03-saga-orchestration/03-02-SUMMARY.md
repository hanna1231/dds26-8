---
phase: 03-saga-orchestration
plan: "02"
subsystem: api
tags: [quart, grpc, redis, saga, python, orchestrator]

# Dependency graph
requires:
  - phase: 03-01
    provides: saga.py SAGA state machine primitives and orchestrator protobuf stubs
  - phase: 02-grpc-communication
    provides: client.py gRPC wrappers for Stock and Payment services
provides:
  - Quart HTTP shell (orchestrator/app.py) with health endpoint and Redis+gRPC lifecycle hooks
  - OrchestratorServiceServicer implementing StartCheckout gRPC RPC
  - run_checkout() driving full SAGA lifecycle (forward + compensation + exactly-once)
  - run_compensation() reversing completed steps with per-step idempotency flags
  - retry_forever() exponential backoff for compensation steps
  - orchestrator/Dockerfile and production requirements.txt
affects: [03-03, 03-04, 04-fault-tolerance, 05-event-driven]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Quart before_serving/after_serving hooks for async resource lifecycle (Redis + gRPC clients + background gRPC task)"
    - "SAGA forward execution with per-step state transitions using atomic Lua CAS"
    - "Compensation in reverse order with per-step boolean flags (refund_done, stock_restored) for idempotency"
    - "retry_forever with exponential backoff (base=0.5s, cap=30s) for compensation — never gives up"
    - "Exactly-once semantics via SAGA record pre-check before create_saga_record"
    - "Lambda capture pattern for loop-variable capture in async retry callbacks"

key-files:
  created:
    - orchestrator/app.py
    - orchestrator/grpc_server.py
    - orchestrator/Dockerfile
  modified:
    - orchestrator/requirements.txt

key-decisions:
  - "Compensation reads SAGA record fresh from Redis before acting to avoid stale flag data (Pitfall 2 avoidance)"
  - "Lambda default-argument capture (lambda iid=item_id, qty=quantity: ...) used in for-loop compensation to prevent closure-over-loop-variable bug"
  - "Dockerfile exposes port 5000 only (HTTP); port 50053 opened programmatically in grpc_server.py — consistent with stock/payment pattern"
  - "pytest/pytest-asyncio removed from orchestrator/requirements.txt — test deps run from repo root, not inside container"

patterns-established:
  - "SAGA exactly-once: get_saga() before create_saga_record(); if exists return stored state"
  - "SAGA compensation: transition to COMPENSATING first, then run_compensation(); finalize to FAILED last"
  - "Compensation flag pattern: hset(saga_key, 'refund_done', '1') after each step succeeds"

requirements-completed: [SAGA-03, SAGA-04, SAGA-05, SAGA-06]

# Metrics
duration: 2min
completed: 2026-02-28
---

# Phase 3 Plan 02: SAGA Orchestrator Service Summary

**Quart+gRPC orchestrator service with full SAGA lifecycle: forward execution, reverse compensation with per-step idempotency flags, exponential backoff retry, and exactly-once semantics via Redis pre-check**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-28T13:06:51Z
- **Completed:** 2026-02-28T13:08:25Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- `orchestrator/app.py` — Quart HTTP shell following stock/app.py pattern with before_serving/after_serving hooks wiring Redis, gRPC clients, and gRPC background task
- `orchestrator/grpc_server.py` — StartCheckout servicer calling run_checkout() which drives full SAGA (forward steps, compensation on failure, exactly-once guard)
- `run_compensation()` reads flags fresh from Redis and reverses in order: refund payment then restore stock; each step marked with a boolean flag for crash-recovery idempotency
- `retry_forever()` implements exponential backoff (base=0.5s, cap=30s) for compensation steps that must never give up
- Dockerfile and trimmed requirements.txt ready for container image build

## Task Commits

1. **Task 1: Implement orchestrator app.py and grpc_server.py with SAGA execution** - `98be943` (feat)
2. **Task 2: Create orchestrator Dockerfile and update requirements.txt** - `a8bee0a` (chore)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `orchestrator/app.py` - Quart HTTP shell: Redis init, gRPC client init, gRPC background task, /health endpoint
- `orchestrator/grpc_server.py` - StartCheckout servicer, run_checkout(), run_compensation(), retry_forever(), serve_grpc()/stop_grpc_server() on port 50053
- `orchestrator/Dockerfile` - FROM python:3.12-slim, EXPOSE 5000, matches stock/payment pattern
- `orchestrator/requirements.txt` - Runtime deps only: quart, uvicorn, redis[hiredis], msgspec, grpcio, protobuf; pytest removed

## Decisions Made

- Compensation reads the SAGA hash fresh from Redis inside run_compensation() to avoid acting on stale flags passed in from the caller (Pitfall 2 from 03-RESEARCH.md).
- Lambda default-argument capture (`lambda iid=item_id, qty=quantity: ...`) used inside the items for-loop to prevent closure-over-loop-variable bug when building retry callbacks.
- Dockerfile exposes only port 5000 (Quart HTTP); port 50053 is opened programmatically so the pattern stays consistent with stock and payment services.
- pytest and pytest-asyncio removed from orchestrator/requirements.txt — tests run from the repo root using the root pytest configuration.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Orchestrator service is complete and ready for integration testing (03-03)
- StartCheckout RPC can be called to drive full SAGA lifecycle end-to-end
- All SAGA-03/04/05/06 requirements satisfied
- 03-04 (docker-compose integration) can proceed immediately after 03-03

---
*Phase: 03-saga-orchestration*
*Completed: 2026-02-28*
