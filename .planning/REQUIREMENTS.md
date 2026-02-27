# Requirements: DDS26-8 Distributed Checkout System

**Defined:** 2026-02-27
**Core Value:** Checkout transactions must never lose money or item counts — consistency is non-negotiable, even when containers crash mid-transaction.

## v1 Requirements

Requirements for Phase 1 (due March 13). Each maps to roadmap phases.

### Async Migration

- [ ] **ASYNC-01**: All three services (Order, Stock, Payment) run on Quart+Uvicorn instead of Flask+Gunicorn
- [ ] **ASYNC-02**: Redis operations use async redis-py client (`redis.asyncio`) with hiredis acceleration
- [ ] **ASYNC-03**: All existing API endpoints preserve identical routes and response formats after migration

### gRPC Communication

- [ ] **GRPC-01**: Proto definitions exist for Stock and Payment service operations used by the orchestrator
- [ ] **GRPC-02**: Stock and Payment services expose gRPC server alongside HTTP (dual-server: HTTP :5000, gRPC :50051)
- [ ] **GRPC-03**: SAGA orchestrator communicates with Stock and Payment via gRPC (not HTTP)
- [ ] **GRPC-04**: gRPC calls include idempotency_key field in all mutation requests

### SAGA Orchestration

- [ ] **SAGA-01**: Every checkout creates a persistent SAGA record in Redis before any side effects
- [ ] **SAGA-02**: SAGA state machine has explicit states (STARTED, STOCK_RESERVED, PAYMENT_CHARGED, COMPLETED, COMPENSATING, FAILED) with validated transitions
- [ ] **SAGA-03**: Dedicated SAGA orchestrator coordinates checkout: reserve stock → charge payment → confirm order
- [ ] **SAGA-04**: Orchestrator drives compensation in reverse on failure: refund payment → restore stock → mark failed
- [ ] **SAGA-05**: Compensating transactions retry with exponential backoff until success (never silently dropped)
- [ ] **SAGA-06**: Checkout endpoint returns exactly-once semantics using order_id as idempotency key
- [ ] **SAGA-07**: SAGA orchestrator designed with clean interface boundary for Phase 2 extraction

### Idempotency & Atomicity

- [ ] **IDMP-01**: Stock subtract/add operations accept idempotency key and skip re-execution if already processed
- [ ] **IDMP-02**: Payment pay/refund operations accept idempotency key and skip re-execution if already processed
- [ ] **IDMP-03**: Redis read-modify-write operations use Lua scripts for atomicity (prevent concurrent overselling)

### Fault Tolerance

- [ ] **FAULT-01**: System recovers when any single container (service or database) is killed
- [ ] **FAULT-02**: On orchestrator startup, incomplete SAGAs are scanned and resolved (complete or compensate)
- [ ] **FAULT-03**: System remains consistent after container kill + recovery cycle
- [ ] **FAULT-04**: Circuit breaker prevents cascade failures when downstream services are unavailable

### Event-Driven Architecture

- [ ] **EVENT-01**: Redis Streams used for SAGA lifecycle events (checkout started, stock reserved, payment completed, etc.)
- [ ] **EVENT-02**: Consumer groups configured for reliable event processing with at-least-once delivery
- [ ] **EVENT-03**: SAGA orchestrator publishes events to streams and consumes responses

### Infrastructure

- [ ] **INFRA-01**: Redis Cluster configured per service domain for high availability (automatic failover)
- [ ] **INFRA-02**: Kubernetes HPA configured for auto-scaling service replicas
- [ ] **INFRA-03**: System runs within 20 CPU benchmark constraint
- [ ] **INFRA-04**: Docker Compose updated for local development with new architecture
- [ ] **INFRA-05**: Kubernetes manifests updated for production deployment

### Documentation

- [ ] **DOCS-01**: Architecture design document written in markdown for final presentation
- [ ] **DOCS-02**: Architecture doc covers: SAGA pattern, gRPC design, Redis Cluster topology, Kubernetes scaling, event-driven design, fault tolerance strategy
- [ ] **DOCS-03**: contributions.txt file at repo root with team member contributions

### Testing & Benchmark

- [ ] **TEST-01**: Existing integration tests pass against new architecture
- [ ] **TEST-02**: System passes the provided wdm-project-benchmark without modifications
- [ ] **TEST-03**: Consistency verified after kill-container recovery scenarios

## v2 Requirements

Deferred to Phase 2 (due April 1). Tracked but not in current roadmap.

### SAGA Orchestrator Abstraction

- **ORCH-01**: SAGA orchestrator extracted as standalone reusable service/library
- **ORCH-02**: Shopping-cart project rewritten to use the orchestrator abstraction
- **ORCH-03**: Orchestrator provides generic transaction coordination (not checkout-specific)

### Advanced Observability

- **OBS-01**: Distributed tracing with OpenTelemetry + Jaeger
- **OBS-02**: Kubernetes HPA with custom Prometheus metrics (queue depth)

### Reliability Patterns

- **REL-01**: Outbox pattern for reliable event publishing

## Out of Scope

| Feature | Reason |
|---------|--------|
| Two-Phase Commit (2PC) | Redis doesn't support XA transactions; SAGA is the correct pattern |
| Authentication/Authorization | Not required by course spec, no grade impact |
| Custom message queue | Redis Streams provides consumer groups natively |
| Pure choreography (no orchestrator) | Harder to make crash-recoverable; orchestrator + events is better |
| Full event sourcing | Significant complexity for zero grade benefit |
| Custom benchmark | Bonus points only; defer if it competes with core work |
| Distributed locking (Redlock) | Known edge cases; idempotency + optimistic concurrency is correct approach |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ASYNC-01 | Phase 1 | Pending |
| ASYNC-02 | Phase 1 | Pending |
| ASYNC-03 | Phase 1 | Pending |
| GRPC-01 | Phase 2 | Pending |
| GRPC-02 | Phase 2 | Pending |
| GRPC-03 | Phase 2 | Pending |
| GRPC-04 | Phase 2 | Pending |
| SAGA-01 | Phase 3 | Pending |
| SAGA-02 | Phase 3 | Pending |
| SAGA-03 | Phase 3 | Pending |
| SAGA-04 | Phase 3 | Pending |
| SAGA-05 | Phase 3 | Pending |
| SAGA-06 | Phase 3 | Pending |
| SAGA-07 | Phase 3 | Pending |
| IDMP-01 | Phase 3 | Pending |
| IDMP-02 | Phase 3 | Pending |
| IDMP-03 | Phase 3 | Pending |
| FAULT-01 | Phase 4 | Pending |
| FAULT-02 | Phase 4 | Pending |
| FAULT-03 | Phase 4 | Pending |
| FAULT-04 | Phase 4 | Pending |
| EVENT-01 | Phase 5 | Pending |
| EVENT-02 | Phase 5 | Pending |
| EVENT-03 | Phase 5 | Pending |
| INFRA-01 | Phase 6 | Pending |
| INFRA-02 | Phase 6 | Pending |
| INFRA-03 | Phase 6 | Pending |
| INFRA-04 | Phase 6 | Pending |
| INFRA-05 | Phase 6 | Pending |
| DOCS-01 | Phase 7 | Pending |
| DOCS-02 | Phase 7 | Pending |
| DOCS-03 | Phase 7 | Pending |
| TEST-01 | Phase 7 | Pending |
| TEST-02 | Phase 7 | Pending |
| TEST-03 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 35 total
- Mapped to phases: 35
- Unmapped: 0 -- complete

---
*Requirements defined: 2026-02-27*
*Last updated: 2026-02-27 after roadmap creation*
