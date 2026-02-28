---
phase: 04-fault-tolerance
plan: 01
subsystem: orchestrator
tags: [circuit-breaker, fault-tolerance, saga-recovery, retry, grpc]
dependency_graph:
  requires: [03-01, 03-02]
  provides: [FAULT-01, FAULT-02, FAULT-03, FAULT-04]
  affects: [orchestrator/circuit.py, orchestrator/client.py, orchestrator/grpc_server.py, orchestrator/recovery.py, orchestrator/app.py]
tech_stack:
  added: [circuitbreaker==2.1.3]
  patterns: [circuit-breaker, bounded-retry, full-jitter-backoff, startup-recovery-scan, forward-first-recovery]
key_files:
  created:
    - orchestrator/circuit.py
    - orchestrator/recovery.py
  modified:
    - orchestrator/client.py
    - orchestrator/grpc_server.py
    - orchestrator/app.py
    - orchestrator/requirements.txt
    - docker-compose.yml
decisions:
  - "Independent per-service circuit breakers (stock_breaker, payment_breaker) with failure_threshold=5, recovery_timeout=30"
  - "retry_forward uses full-jitter exponential backoff; CircuitBreakerError propagates immediately without retry"
  - "Startup recovery blocks new requests (serve_grpc deferred) until all stale SAGAs are driven to terminal state"
  - "5-minute staleness threshold before treating a non-terminal SAGA as stuck"
  - "restart: always added to all 9 containers in docker-compose.yml"
metrics:
  duration: 227s
  completed: "2026-02-28"
  tasks: 2
  files: 7
---

# Phase 04 Plan 01: Fault Tolerance — Circuit Breakers and SAGA Recovery Summary

**One-liner:** Independent circuit breakers per gRPC service (threshold=5, timeout=30s), bounded 3-attempt forward retry with CircuitBreakerError compensation trigger, and startup SAGA recovery scanner that drives stale non-terminal SAGAs to terminal state before serving.

## What Was Built

### Task 1: Circuit breaker module, wrapped gRPC clients, bounded forward retry

**orchestrator/circuit.py** — New module with two independent `CircuitBreaker` instances:
- `stock_breaker` and `payment_breaker` each with `failure_threshold=5`, `recovery_timeout=30`, `expected_exception=grpc.aio.AioRpcError`
- Module-level instances ensure state is shared across all calls (not reset per request)

**orchestrator/client.py** — All 6 gRPC client functions wrapped with circuit breaker decorators:
- `@stock_breaker` on `reserve_stock`, `release_stock`, `check_stock`
- `@payment_breaker` on `charge_payment`, `refund_payment`, `check_payment`
- Re-exports `CircuitBreakerError` from `circuitbreaker` for callers

**orchestrator/grpc_server.py** — Added `retry_forward()` function and updated `run_checkout()`:
- `retry_forward(fn, max_attempts=3)`: full-jitter exponential backoff, `CircuitBreakerError` propagates immediately (never retried)
- `run_checkout` now uses `retry_forward()` for stock reservation and payment charge steps
- Top-level `except CircuitBreakerError` in `run_checkout` catches open-breaker errors, sets saga error, transitions to COMPENSATING, and runs compensation
- `retry_forever` unchanged — still used only by compensation steps (must retry indefinitely)

**orchestrator/requirements.txt** — Added `circuitbreaker==2.1.3`

### Task 2: SAGA startup recovery scanner and app lifecycle wiring

**orchestrator/recovery.py** — New module with full recovery logic:
- `recover_incomplete_sagas(db)`: scans `saga:*` keys, skips SAGAs younger than 300 seconds (still fresh), drives stale non-terminal SAGAs via `resume_saga()`
- `resume_saga(db, saga)`: forward-first recovery — replays idempotent gRPC calls from current state forward; `CircuitBreakerError` during recovery triggers compensation; COMPENSATING state drives directly to `run_compensation()`
- Detailed logging per SAGA (order_id, from state, outcome)

**orchestrator/app.py** — Recovery wired into startup lifecycle:
- `from recovery import recover_incomplete_sagas` added at top
- `await recover_incomplete_sagas(db)` called after `init_grpc_clients()` and before `app.add_background_task(serve_grpc, db)`
- Guarantees: no new requests served until all stale SAGAs are at terminal state

**docker-compose.yml** — Added `restart: always` to all 9 containers:
- Application services: `gateway`, `order-service`, `stock-service`, `payment-service`, `orchestrator-service`
- Database services: `order-db`, `stock-db`, `payment-db`, `orchestrator-db`

## Verification Results

All automated checks passed:
- Circuit breaker instances verified: `isinstance(stock_breaker, CircuitBreaker)`, `FAILURE_THRESHOLD==5`, `RECOVERY_TIMEOUT==30`
- `NON_TERMINAL_STATES` and `STALENESS_THRESHOLD_SECONDS` correct
- `recover_incomplete_sagas` present in `app.py` before `serve_grpc`
- 9 `restart: always` policies in docker-compose.yml
- `pytest tests/test_saga.py -x`: 10/10 tests pass (existing behavior unchanged)

## Deviations from Plan

None — plan executed exactly as written.

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| Independent per-service breakers | Stock outage must not block Payment processing |
| CircuitBreakerError propagates immediately from retry_forward | Open breaker means service down — retrying wastes time and delays compensation |
| Recovery blocks serving | No request should be processed while orphaned SAGAs exist — avoids concurrent modification |
| 5-minute staleness threshold | Fresh SAGAs (< 5 min) may still be in-flight from another orchestrator; only truly stale ones are recovered |
| restart: always on all containers | Ensures self-healing after container kills without manual intervention |

## Commits

- `bf509de`: feat(04-01): add circuit breakers, bounded forward retry, and CircuitBreakerError handling
- `33e1c6e`: feat(04-01): add SAGA startup recovery scanner and wire into app lifecycle

## Self-Check: PASSED

All created files exist on disk. Both task commits (bf509de, 33e1c6e) verified in git log.
