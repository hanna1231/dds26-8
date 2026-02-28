---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
last_updated: "2026-02-28T14:15:36.423Z"
progress:
  total_phases: 4
  completed_phases: 4
  total_plans: 13
  completed_plans: 13
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-27)

**Core value:** Checkout transactions must never lose money or item counts — consistency is non-negotiable, even when containers crash mid-transaction.
**Current focus:** Phase 3 — SAGA Orchestration

## Current Position

Phase: 4 of 7 (Fault Tolerance)
Plan: 2 of 2 in current phase (both plans complete)
Status: Phase 4 COMPLETE (2/2 plans done)
Last activity: 2026-02-28 — Completed 04-02 (Fault Tolerance Tests)

Progress: [████████░░] 80% (phase 4 complete)

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: -

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 02-grpc-communication P03 | 1 | 1 tasks | 2 files |
| Phase 02-grpc-communication P01 | 2 | 2 tasks | 18 files |
| Phase 01-async-foundation P03 | 3 | 1 tasks | 2 files |
| Phase 01-async-foundation P02 | 2 | 1 tasks | 2 files |
| Phase 01-async-foundation P01 | 2 | 2 tasks | 3 files |
| Phase 02-grpc-communication P04 | 2 | 2 tasks | 5 files |
| Phase 03-saga-orchestration P01 | 93 | 2 tasks | 8 files |
| Phase 03-saga-orchestration P02 | 2 | 2 tasks | 4 files |
| Phase 03-saga-orchestration P03 | 2 | 2 tasks | 4 files |
| Phase 03-saga-orchestration P04 | 176 | 2 tasks | 2 files |
| Phase 04-fault-tolerance P01 | 227 | 2 tasks | 7 files |
| Phase 04-fault-tolerance P02 | 5 | 1 tasks | 2 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: Redis Streams chosen over Kafka — same redis-py client, no new infrastructure, fits 20 CPU budget
- Roadmap: SAGA orchestrator runs as single replica (avoids split-brain); domain services scale horizontally via HPA
- Roadmap: Phases 4 (Fault Tolerance) and 5 (Event-Driven) both depend on Phase 3; either can follow Phase 3
- [Phase 01-async-foundation]: redis.asyncio (redis-py bundled) used over aioredis for Payment async Redis — simpler, same API
- [Phase 01-async-foundation]: Payment lifecycle: before_serving/after_serving hooks replace atexit for async Redis client management
- [Phase 01-async-foundation]: Stock service migration folded into 01-01 commit during parallel phase execution; all success criteria verified against current file state
- [Phase 01-async-foundation 01-01]: Module-level globals (db=None, http_client=None) initialized in before_serving hook — correct pattern for Uvicorn multi-worker lifecycle
- [Phase 01-async-foundation 01-01]: abort() does not need await in Quart — raises HTTPException synchronously same as Flask
- [Phase 01-async-foundation 01-01]: db.aclose() required for redis.asyncio (not .close())
- [Phase 02-grpc-communication 02-01]: grpcio-tools installed via pip3 (system Python); pip maps to pipx venv where grpc_tools not accessible
- [Phase 02-grpc-communication 02-01]: Generated stubs use absolute imports (import stock_pb2) — correct for services run from their own directory, not installed packages
- [Phase 02-grpc-communication 02-01]: Generated stubs committed to repo; no runtime codegen needed in containers
- [Phase 02-grpc-communication 02-02]: StockValue/UserValue redefined in grpc_server.py (not imported from app.py) to avoid circular import risk
- [Phase 02-grpc-communication 02-02]: Idempotency — 30s processing lock TTL, 86400s committed result TTL; single Lua eval eliminates TOCTOU
- [Phase 02-grpc-communication 02-02]: Business errors (not found, insufficient) in response fields only — SAGA orchestrator inspects success/error_message, never gRPC status codes
- [Phase 02-grpc-communication 02-03]: gRPC transport errors (grpc.aio.AioRpcError) not caught in client.py — Phase 4 adds circuit breaker at orchestrator level
- [Phase 02-grpc-communication 02-03]: init_grpc_clients() accepts optional address overrides for test-time injection without env var manipulation
- [Phase 02-grpc-communication 02-04]: asyncio_default_test_loop_scope=session required in pytest.ini alongside asyncio_default_fixture_loop_scope=session to prevent grpc.aio channel loop mismatch errors in tests
- [Phase 02-grpc-communication 02-04]: Stock gRPC server on port 50051, Payment on 50052 in tests — manual gRPC server creation in fixtures avoids modifying service code while preventing port collision
- [Phase 03-saga-orchestration 03-01]: orchestrator_pb2 stubs use absolute imports — consistent with Phase 2 convention, works when run from service directory
- [Phase 03-saga-orchestration 03-01]: HSETNX on 'state' field prevents duplicate SAGA record creation under concurrent requests
- [Phase 03-saga-orchestration 03-01]: TRANSITION_LUA Lua CAS validates from_state before update — same atomic pattern as Phase 2 IDEMPOTENCY_ACQUIRE_LUA
- [Phase 03-saga-orchestration 03-01]: Manual byte decoding in get_saga (k.decode()/v.decode()) — consistent with existing codebase, no decode_responses=True
- [Phase 03-saga-orchestration 03-02]: Compensation reads SAGA hash fresh from Redis before acting to avoid stale flag data (Pitfall 2 avoidance)
- [Phase 03-saga-orchestration 03-02]: Lambda default-argument capture in for-loop compensation callbacks prevents closure-over-loop-variable bug
- [Phase 03-saga-orchestration 03-02]: Dockerfile exposes port 5000 only; port 50053 opened programmatically in grpc_server.py — consistent with stock/payment pattern
- [Phase 03-saga-orchestration 03-02]: pytest/pytest-asyncio removed from orchestrator/requirements.txt — test deps run from repo root, not inside container
- [Phase 03-saga-orchestration]: orchestrator-service runs --workers 1 (single replica to avoid SAGA split-brain per roadmap decision)
- [Phase 03-saga-orchestration]: orchestrator command is uvicorn app:app (Quart app manages gRPC server as background task)
- [Phase 03-saga-orchestration]: send_post_request removed from order/app.py — SAGA owns compensation; httpx kept for addItem stock lookup
- [Phase 03-saga-orchestration 03-04]: orchestrator_db uses Redis db=3 to avoid collision with stock/payment (both db=0 in tests)
- [Phase 03-saga-orchestration 03-04]: Orchestrator gRPC server started manually via grpc.aio.server() in conftest — not serve_grpc() which blocks on wait_for_termination()
- [Phase 03-saga-orchestration 03-04]: Test 9 patches grpc_server.release_stock + asyncio.sleep to simulate transient gRPC failures without real delays
- [Phase 03-saga-orchestration 03-04]: Test 10 replays stock/payment operations with same idempotency keys to validate Phase 2 Lua caching prevents double execution
- [Phase 04-fault-tolerance 04-01]: Independent per-service circuit breakers (stock_breaker, payment_breaker) with failure_threshold=5, recovery_timeout=30 — Stock outage must not block Payment
- [Phase 04-fault-tolerance 04-01]: CircuitBreakerError propagates immediately from retry_forward (never retried) — open breaker means service down, retrying wastes time and delays compensation
- [Phase 04-fault-tolerance 04-01]: Startup recovery blocks serve_grpc until all stale SAGAs (>5 min old) are driven to terminal state — forward-first replay using idempotent keys from Phase 2
- [Phase 04-fault-tolerance 04-01]: restart: always added to all 9 containers in docker-compose.yml for self-healing after container kills
- [Phase 04-fault-tolerance]: Half-open recovery tested by setting _opened to past monotonic time (not sleeping); seed_saga() helper injects arbitrary SAGA state directly into Redis for recovery scanner tests

### Pending Todos

None yet.

### Blockers/Concerns

- Instructor may expect Kafka for "event-driven architecture" evaluation points. Redis Streams is architecturally equivalent but confirm with TAs before committing. (from research)
- gRPC async channel lifecycle (grpc.aio keepalive, connection health checks) requires careful attention during Phase 2 implementation.
- 20 CPU budget is tight — validate Redis Cluster actual CPU draw under benchmark load before finalizing HPA max replicas.

## Session Continuity

Last session: 2026-02-28T14:15:00Z
Stopped at: Completed 04-02-PLAN.md (Fault Tolerance Tests); Phase 4 complete (2/2 plans done)
Resume file: None
