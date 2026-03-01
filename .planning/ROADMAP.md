# Roadmap: DDS26-8 Distributed Checkout System

## Overview

Starting from an existing Flask+Redis checkout system (Order, Stock, Payment services), this roadmap upgrades the system to a fully distributed, fault-tolerant architecture in seven phases. The dependency chain is strict: async framework migration unblocks gRPC, which unblocks the SAGA orchestrator, which enables idempotent compensation and fault recovery, which enables event-driven coordination, which is then hardened with Redis Cluster and Kubernetes infrastructure, and finally validated against the benchmark and documented for the final presentation. Every phase delivers a verifiable capability; consistency and crash recovery are the primary evaluation criteria throughout.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Async Foundation** - Migrate all three services from Flask+Gunicorn to Quart+Uvicorn with async Redis (completed 2026-02-28)
- [x] **Phase 2: gRPC Communication** - Add gRPC servers to Stock and Payment; define proto contracts with idempotency keys (completed 2026-02-28)
- [x] **Phase 3: SAGA Orchestration** - Build the SAGA orchestrator with Redis-persisted state, idempotent service operations, and compensation (completed 2026-02-28)
- [ ] **Phase 4: Fault Tolerance** - Add crash recovery, circuit breakers, and verified consistency under container-kill scenarios
- [x] **Phase 5: Event-Driven Architecture** - Integrate Redis Streams for SAGA lifecycle events and compensation retry (completed 2026-02-28)
- [x] **Phase 6: Infrastructure** - Configure Redis Cluster per domain, Kubernetes HPA, and benchmark tuning (completed 2026-03-01)
- [ ] **Phase 7: Validation and Delivery** - Pass the benchmark, verify consistency under kill tests, write architecture doc and contributions

## Phase Details

### Phase 1: Async Foundation
**Goal**: All three domain services run on Quart+Uvicorn with async Redis, with all existing API routes and response formats preserved
**Depends on**: Nothing (first phase)
**Requirements**: ASYNC-01, ASYNC-02, ASYNC-03
**Success Criteria** (what must be TRUE):
  1. All three services (Order, Stock, Payment) start and respond on Quart+Uvicorn, replacing Flask+Gunicorn
  2. All existing HTTP routes return identical responses before and after migration (no API contract changes)
  3. Redis operations use `redis.asyncio` client with hiredis; no synchronous Redis calls remain in any service
  4. Existing integration tests pass against the migrated services
**Plans**: 3 plans (all Wave 1, parallel)

Plans:
- [ ] 01-01: Migrate Order service to Quart+Uvicorn with async Redis + httpx; update docker-compose.yml for all services (Wave 1)
- [ ] 01-02: Migrate Stock service to Quart+Uvicorn with async Redis (Wave 1)
- [ ] 01-03: Migrate Payment service to Quart+Uvicorn with async Redis (Wave 1)

### Phase 2: gRPC Communication
**Goal**: Stock and Payment services expose gRPC alongside HTTP; all inter-service mutation calls carry idempotency keys via gRPC
**Depends on**: Phase 1
**Requirements**: GRPC-01, GRPC-02, GRPC-03, GRPC-04
**Success Criteria** (what must be TRUE):
  1. Proto definitions exist for Stock and Payment service operations used by the orchestrator
  2. Stock and Payment each run a gRPC server on port 50051 alongside the existing HTTP server on port 5000
  3. The SAGA orchestrator calls Stock and Payment exclusively via gRPC (no HTTP inter-service calls remain on the checkout path)
  4. Every gRPC mutation RPC includes an `idempotency_key` field in its proto definition and accepts it at runtime
**Plans**: 3 plans (Wave 1 → Wave 2 → Wave 3, sequential)

Plans:
- [ ] 02-01: Define proto files for StockService and PaymentService; generate Python stubs; add grpcio dependencies (Wave 1)
- [ ] 02-02: Implement gRPC servicers with Lua idempotency; add dual-server startup to Stock and Payment (Wave 2)
- [ ] 02-03: Create thin gRPC client module in orchestrator/ for Phase 3 SAGA orchestrator (Wave 3)

### Phase 3: SAGA Orchestration
**Goal**: A dedicated SAGA orchestrator coordinates checkout with Redis-persisted state, idempotent service operations, and retry-until-success compensation
**Depends on**: Phase 2
**Requirements**: SAGA-01, SAGA-02, SAGA-03, SAGA-04, SAGA-05, SAGA-06, SAGA-07, IDMP-01, IDMP-02, IDMP-03
**Success Criteria** (what must be TRUE):
  1. Every checkout creates a SAGA record in Redis before any service call is made (state survives pod kill at any point)
  2. SAGA states (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPLETED, COMPENSATING, FAILED) are explicit with validated transitions; no invalid state jumps occur
  3. A dedicated SAGA orchestrator drives checkout (reserve stock → charge payment → confirm order) with gRPC calls
  4. Compensation runs in reverse (refund payment → restore stock → mark failed) and retries with exponential backoff until each step succeeds — failures are never silently dropped
  5. Submitting a checkout request twice with the same order_id charges the user exactly once
  6. Stock subtract/add and payment pay/refund operations deduplicate when called with the same idempotency key; Redis read-modify-write uses Lua scripts to prevent overselling under concurrency
**Plans**: TBD

Plans:
- [ ] 03-01: Build SAGA state machine with Redis persistence (state record per checkout, validated transitions)
- [ ] 03-02: Implement SAGA orchestrator service wiring Order checkout to orchestrator via gRPC
- [ ] 03-03: Implement idempotent Stock and Payment operations with Lua atomicity
- [ ] 03-04: Implement compensating transactions with retry backoff and compensation intent persistence

### Phase 4: Fault Tolerance
**Goal**: The system remains consistent when any single container is killed mid-transaction; incomplete SAGAs resume on orchestrator restart; cascade failures are contained
**Depends on**: Phase 3
**Requirements**: FAULT-01, FAULT-02, FAULT-03, FAULT-04
**Success Criteria** (what must be TRUE):
  1. Killing any single running container (Order, Stock, Payment, or orchestrator) does not result in lost money or item counts after recovery
  2. On orchestrator restart, all non-terminal SAGAs are scanned and either driven to completion or compensated (no SAGA left stranded in a partial state)
  3. After a container kill and recovery cycle, the database state is consistent: no phantom stock deductions or duplicate charges
  4. When Stock or Payment is unavailable, the circuit breaker prevents the orchestrator from flooding it with retries and returns a clean failure response
**Plans**: 2 plans (Wave 1 → Wave 2, sequential)

Plans:
- [ ] 04-01: Add circuit breakers, bounded forward retry, and SAGA startup recovery to orchestrator (Wave 1)
- [ ] 04-02: Write fault tolerance tests for circuit breaker, recovery scanner, and consistency (Wave 2)

### Phase 5: Event-Driven Architecture
**Goal**: SAGA lifecycle events are published to Redis Streams with consumer groups; compensation retries are queued reliably for at-least-once processing
**Depends on**: Phase 3
**Requirements**: EVENT-01, EVENT-02, EVENT-03
**Success Criteria** (what must be TRUE):
  1. Every SAGA lifecycle transition (checkout started, stock reserved, payment completed, compensation triggered, etc.) is published as an event to a Redis Stream
  2. Consumer groups are configured on the streams; events are re-delivered if not acknowledged (at-least-once delivery)
  3. The SAGA orchestrator both publishes events and consumes responses from Redis Streams; event processing does not block the checkout path
**Plans**: TBD

**Plans**: 2 plans (Wave 1 → Wave 2, sequential)

Plans:
- [ ] 05-01: Create events.py and consumers.py modules; wire event publishing into grpc_server.py and consumer lifecycle into app.py (Wave 1)
- [ ] 05-02: Write event-driven architecture tests for EVENT-01, EVENT-02, EVENT-03 (Wave 2)

### Phase 6: Infrastructure
**Goal**: Redis Cluster provides high availability per service domain; Kubernetes HPA scales domain service replicas; the system runs within the 20 CPU benchmark constraint
**Depends on**: Phase 5
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. Redis Cluster (3 primary + 3 replica nodes per domain) is configured with AOF persistence and noeviction policy; killing a Redis primary triggers automatic failover without data loss
  2. Kubernetes HPA scales Order, Stock, and Payment service replicas on CPU > 70%; orchestrator remains at a single replica
  3. The system runs all services and Redis Cluster nodes within 20 CPUs under benchmark load
  4. Local development works via updated Docker Compose (new orchestrator service, Redis Cluster); production deployment works via updated Kubernetes manifests
**Plans**: TBD

Plans:
- [ ] 06-01: Configure Redis Cluster per domain (Bitnami Helm); switch services to `redis.asyncio.RedisCluster`; validate Lua scripts with hash tags
- [ ] 06-02: Update Kubernetes manifests (orchestrator Deployment, HPA for domain services, Redis Cluster StatefulSets)
- [ ] 06-03: Update Docker Compose for local development; profile CPU allocation under benchmark load

### Phase 7: Validation and Delivery
**Goal**: The system passes the wdm-project-benchmark, survives the kill-container consistency test, and is documented for the final presentation
**Depends on**: Phase 6
**Requirements**: DOCS-01, DOCS-02, DOCS-03, TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. All existing integration tests pass against the new architecture without modification
  2. The wdm-project-benchmark runs to completion without modifications and reports no consistency violations
  3. A container kill during active benchmark load, followed by recovery, results in a consistent database state (no lost money or item counts)
  4. An architecture design document exists covering SAGA pattern, gRPC design, Redis Cluster topology, Kubernetes scaling, event-driven design, and fault tolerance strategy
  5. contributions.txt exists at the repo root with team member contributions
**Plans**: 3 plans (all Wave 1, parallel)

Plans:
- [x] 07-01: Run integration tests and wdm-project-benchmark consistency test; fix any failures (Wave 1)
- [ ] 07-02: Create kill-container consistency test scripts; make STALENESS_THRESHOLD configurable (Wave 1)
- [ ] 07-03: Write architecture design document and contributions.txt placeholder (Wave 1)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7
Note: Phase 5 depends on Phase 3 (not Phase 4); Phases 4 and 5 can proceed in either order once Phase 3 is complete.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Async Foundation | 3/3 | Complete   | 2026-02-28 |
| 2. gRPC Communication | 4/4 | Complete   | 2026-02-28 |
| 3. SAGA Orchestration | 4/4 | Complete   | 2026-02-28 |
| 4. Fault Tolerance | 1/2 | In Progress|  |
| 5. Event-Driven Architecture | 2/2 | Complete   | 2026-02-28 |
| 6. Infrastructure | 3/3 | Complete   | 2026-03-01 |
| 7. Validation and Delivery | 3/3 | Complete   | 2026-03-01 |
