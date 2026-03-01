---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
last_updated: "2026-03-01T08:22:20.289Z"
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 18
  completed_plans: 18
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-27)

**Core value:** Checkout transactions must never lose money or item counts — consistency is non-negotiable, even when containers crash mid-transaction.
**Current focus:** Phase 3 — SAGA Orchestration

## Current Position

Phase: 6 of 6 (Infrastructure)
Plan: 3 of 3 in current phase
Status: Phase 6 COMPLETE (3/3 plans done)
Last activity: 2026-03-01 — Completed 06-03 (Docker Compose Redis Clusters + Makefile)

Progress: [██████████] ~100% (6 phases complete)

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
| Phase 05-event-driven-architecture P01 | 374 | 2 tasks | 4 files |
| Phase 05-event-driven-architecture P02 | 10 | 2 tasks | 1 files |
| Phase 06-infrastructure P01 | 675 | 2 tasks | 17 files |
| Phase 06-infrastructure P02 | 15 | 2 tasks | 12 files |
| Phase 06-infrastructure P03 | 5 | 2 tasks | 6 files |

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
- [Phase 05-event-driven-architecture 05-01]: publish_event() is fire-and-forget — never raises, drops and counts on Redis failure so checkout is never blocked
- [Phase 05-event-driven-architecture 05-01]: XAUTOCLAIM used for idle message reclaim (Redis 6.2+ modern approach, not XCLAIM+XPENDING)
- [Phase 05-event-driven-architecture 05-01]: Lazy imports for run_compensation and get_saga in _handle_compensation_message prevent circular imports
- [Phase 05-event-driven-architecture 05-01]: XPENDING_RANGE key is 'times_delivered' (verified from redis-py source, not 'delivery_count')
- [Phase 05-event-driven-architecture]: Patch grpc_server module (not client) for monkeypatching: direct import bindings require patching at call site
- [Phase 05-event-driven-architecture]: Pre-set stop_event before consumer creation to avoid spin-loop event loop starvation in unit tests
- [Phase 06-infrastructure]: RedisCluster startup_nodes from REDIS_NODE_HOST env var; hash tags {item:}, {user:}, {saga:} for slot co-location; stream names use shared {saga:events} hash tag
- [Phase 06-infrastructure]: Orchestrator shares payment-redis-cluster (not a 4th cluster) — {saga:} hash tag prefix isolates keys
- [Phase 06-infrastructure]: Bitnami redis-cluster service naming: <release>-redis-cluster, so REDIS_NODE_HOST=order-redis-cluster-redis-cluster
- [Phase 06-infrastructure]: Per-domain Redis nodes use profiles: full; shared nodes use profiles: simple — prevents topology overlap when switching between dev modes
- [Phase 06-infrastructure]: Application services have no profile and rely on restart: always + RedisCluster retry for Redis availability across both profiles

### Pending Todos

None yet.

### Blockers/Concerns

- Instructor may expect Kafka for "event-driven architecture" evaluation points. Redis Streams is architecturally equivalent but confirm with TAs before committing. (from research)
- gRPC async channel lifecycle (grpc.aio keepalive, connection health checks) requires careful attention during Phase 2 implementation.
- 20 CPU budget is tight — validate Redis Cluster actual CPU draw under benchmark load before finalizing HPA max replicas.

## Session Continuity

Last session: 2026-03-01T08:30:00Z
Stopped at: Completed 06-02-PLAN.md (K8s Manifests, HPA, Redis Cluster Helm values); Phase 6 plan 2 of 3 done
Resume file: None
