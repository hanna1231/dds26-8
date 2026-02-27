# DDS26-8 Distributed Checkout System

## What This Is

A distributed microservice-based checkout system for the TU Delft Distributed Data Systems course. Three services (Order, Stock, Payment) coordinate to handle e-commerce checkout transactions with consistency guarantees, fault tolerance, and high performance. Built on an existing Flask+Redis template that must be upgraded with proper distributed transaction protocols (SAGAs), async performance (Quart+Uvicorn), gRPC for inter-service calls, and Kubernetes-based scaling.

## Core Value

Checkout transactions must never lose money or item counts — consistency is non-negotiable, even when containers crash mid-transaction.

## Requirements

### Validated

<!-- Existing capabilities from the template codebase -->

- ✓ Order CRUD (create, find, addItem) — existing
- ✓ Stock CRUD (create item, find, add, subtract) — existing
- ✓ Payment CRUD (create user, find user, add funds, pay) — existing
- ✓ Nginx API gateway routing to all three services — existing
- ✓ Redis persistence with msgpack serialization — existing
- ✓ Docker containerization for all services — existing
- ✓ Basic checkout flow with compensating rollback — existing
- ✓ Integration test suite — existing
- ✓ Kubernetes deployment manifests — existing

### Active

<!-- Phase 1 scope: 2PC + SAGAs + fault tolerance, due March 13 -->

- [ ] Migrate from Flask+Gunicorn to Quart+Uvicorn for async performance
- [ ] Implement SAGA pattern for checkout transaction coordination
- [ ] Build SAGA orchestrator as foundation for Phase 2 abstraction
- [ ] Replace synchronous REST inter-service calls with gRPC
- [ ] Implement compensating transactions with proper rollback guarantees
- [ ] Add fault tolerance: system recovers when any single container dies mid-transaction
- [ ] Persist SAGA state so incomplete transactions resume after crash recovery
- [ ] Choose and integrate message queue (Kafka or Redis Streams) for event-driven coordination
- [ ] Configure Redis Cluster for database scaling and high availability
- [ ] Set up Kubernetes autoscaling (HPA) for service instances
- [ ] Achieve consistency under concurrent checkout operations
- [ ] Handle partial failures: payment service crash after stock deduction triggers rollback on recovery
- [ ] Ensure system passes the provided benchmark (locust stress + consistency tests)
- [ ] Write architectural design document for final presentation
- [ ] Create contributions.txt

### Out of Scope

- Phase 2 SAGA Orchestrator abstraction as separate artifact — deferred to April 1 deadline, but architecture designed to support it
- Authentication/authorization — not required by project spec
- OAuth, rate limiting, API keys — not part of evaluation criteria
- Custom benchmark creation — bonus points only, defer if time-constrained
- Mobile/frontend clients — API-only evaluation

## Context

**Course:** Distributed Data Systems (DDS), TU Delft Master's
**Team:** dds26-8
**Template:** https://github.com/delftdata/wdm-project-template
**Benchmark:** https://github.com/delftdata/wdm-project-benchmark (must work without changes against 20 CPUs max)

**Evaluation criteria (from PDF):**
1. **Consistency** — no lost money or item counts
2. **Performance** — latency and throughput
3. **Architecture Difficulty** — synchronous, asynchronous, event-driven (harder = more points)

**Evaluation process:**
- Benchmark run against 20 CPUs max
- Kill one container at a time, let system recover, then fail another
- System must remain consistent throughout
- Rigorous interview at end of course

**Existing codebase state:**
- Three Flask microservices with basic CRUD and simple rollback
- No proper distributed transaction protocol (just application-level compensating actions)
- Synchronous HTTP calls between services (slow, blocking)
- No fault tolerance for mid-transaction crashes
- No message queue or event-driven architecture
- Single Redis instances (no clustering/HA)

## Constraints

- **Language**: Python only (course requirement)
- **API Contract**: External-facing API routes cannot change (template contract)
- **Database**: Redis (course requirement), Redis Cluster for scaling
- **Deployment**: Must run on Kubernetes, benchmarked against 20 CPUs
- **Framework**: Quart+Uvicorn (team decision — async Flask-compatible, allowed by course)
- **Inter-service sync**: gRPC (team decision — faster than REST for service-to-service)
- **Transaction pattern**: SAGA with orchestrator (team decision — Redis doesn't support XA/2PC)
- **Message queue**: TBD (Kafka vs Redis Streams — decide during research)
- **Timeline**: Phase 1 due March 13, Phase 2 due April 1

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Quart+Uvicorn over Flask+Gunicorn | Async I/O for concurrent gRPC/Redis calls, higher throughput | — Pending |
| SAGA over 2PC | Redis doesn't support XA transactions; SAGAs natural fit for compensation-based consistency | — Pending |
| gRPC for inter-service calls | Lower latency than REST, binary protocol, streaming support | — Pending |
| Redis Cluster for DB scaling | HA + sharding built into Redis, aligns with course Redis requirement | — Pending |
| SAGA orchestrator as separate service | Clean separation prepares for Phase 2 abstraction requirement | — Pending |
| Kafka vs Redis Streams | TBD — research phase will determine best fit | — Pending |

---
*Last updated: 2026-02-27 after initialization*
