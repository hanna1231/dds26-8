# DDS26-8 Distributed Checkout System

## What This Is

A distributed microservice-based checkout system for the TU Delft Distributed Data Systems course. Four services (Order, Stock, Payment, SAGA Orchestrator) coordinate e-commerce checkout transactions using the SAGA pattern with gRPC communication, Redis Streams event-driven architecture, and Redis Cluster for high availability. Built on the wdm-project-template, upgraded from Flask+Redis to Quart+Uvicorn with async Redis, gRPC inter-service calls, and Kubernetes-based scaling.

## Core Value

Checkout transactions must never lose money or item counts — consistency is non-negotiable, even when containers crash mid-transaction.

## Requirements

### Validated

- ✓ Quart+Uvicorn async migration for all services — v1.0
- ✓ Async Redis (redis.asyncio + hiredis) for all operations — v1.0
- ✓ API contract preservation (identical routes/responses) — v1.0
- ✓ gRPC proto definitions for Stock and Payment — v1.0
- ✓ Dual-server (HTTP :5000 + gRPC :50051) on Stock and Payment — v1.0
- ✓ SAGA orchestrator with gRPC-only checkout path — v1.0
- ✓ Idempotency keys on all gRPC mutation RPCs — v1.0
- ✓ Redis-persisted SAGA state with Lua CAS transitions — v1.0
- ✓ Explicit SAGA states with validated transitions — v1.0
- ✓ Compensation with exponential backoff retry-until-success — v1.0
- ✓ Exactly-once checkout semantics via order_id idempotency — v1.0
- ✓ Lua atomic stock/payment operations preventing overselling — v1.0
- ✓ Circuit breakers preventing cascade failures — v1.0
- ✓ Startup SAGA recovery scanner — v1.0
- ✓ Container-kill consistency (no lost money/items after recovery) — v1.0
- ✓ Redis Streams for SAGA lifecycle events with consumer groups — v1.0
- ✓ At-least-once event delivery with XAUTOCLAIM — v1.0
- ✓ Per-domain Redis Cluster (3+3 nodes) with AOF and noeviction — v1.0
- ✓ Kubernetes HPA for domain service auto-scaling — v1.0
- ✓ System runs within 20 CPU benchmark constraint — v1.0
- ✓ Docker Compose + Kubernetes deployment — v1.0
- ✓ Architecture design document — v1.0
- ✓ contributions.txt placeholder — v1.0
- ✓ 37 integration tests passing — v1.0
- ✓ wdm-project-benchmark with 0 consistency violations — v1.0
- ✓ Automated kill-test scripts — v1.0
- ✓ 2PC as alternative transaction coordination pattern (env var toggle) — v2.0
- ✓ Orchestrator as 2PC coordinator (prepare/commit/abort phases) — v2.0
- ✓ Redis Streams message queues as default inter-service communication — v2.0
- ✓ gRPC kept as fallback communication path (env var toggle) — v2.0

### Active

- [x] WorkflowStep/WorkflowDefinition data model and WorkflowStore with Lua CAS — Phase 14
- [ ] Generic workflow engine as separate orchestrator artifact (Cadence/Temporal-inspired)
- [ ] Checkout rewritten as workflow definition using abstract engine API
- [x] Engine supports both SAGA and 2PC execution strategies — Phase 15
- [ ] Codebase refactoring for clarity, consistency, and maintainability

## Current Milestone: v3.0 Abstract Orchestrator & Refactoring

**Goal:** Abstract SAGA/2PC coordination into a generic workflow engine artifact and refactor the codebase for quality, maintainability, and interview readiness.

**Target features:**
- Generic workflow engine that executes abstract step sequences (action + compensation) without knowing about specific services
- Checkout defined as a workflow registration (steps with compensations), not hardcoded orchestrator logic
- Engine supports both SAGA (compensating) and 2PC (prepare/commit/abort) execution strategies
- Codebase refactoring for clarity, consistency, and maintainability

### Out of Scope

- Authentication/authorization — not required by project spec
- Custom benchmark creation — bonus points only
- Full event sourcing — significant complexity for zero grade benefit
- Distributed locking (Redlock) — idempotency + optimistic concurrency is correct approach
- Full Temporal/Cadence feature set (versioning, signals, queries, child workflows) — only core workflow abstraction needed

## Context

**Shipped:** v1.0 on 2026-03-11
**Codebase:** 5,553 LOC Python across 4 services + tests
**Tech stack:** Quart+Uvicorn, gRPC (grpcio), Redis Cluster (redis.asyncio), Redis Streams, Lua scripting, Docker Compose, Kubernetes + Helm
**Architecture:** SAGA + 2PC orchestrator with dual communication (gRPC + Redis Streams queues), event-driven coordination, per-domain Redis Cluster HA, circuit breakers + startup recovery for fault tolerance

**Course:** Distributed Data Systems (DDS), TU Delft Master's
**Team:** dds26-8
**Template:** https://github.com/delftdata/wdm-project-template
**Benchmark:** https://github.com/delftdata/wdm-project-benchmark

## Constraints

- **Language**: Python only (course requirement)
- **API Contract**: External-facing API routes cannot change (template contract)
- **Database**: Redis Cluster (course requirement)
- **Deployment**: Kubernetes, benchmarked against 20 CPUs
- **Framework**: Quart+Uvicorn (async Flask-compatible)
- **Inter-service**: gRPC (binary protocol, faster than REST)
- **Transaction pattern**: SAGA with orchestrator (Redis doesn't support 2PC)
- **Events**: Redis Streams (same client, no extra infra, fits CPU budget)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Quart+Uvicorn over Flask+Gunicorn | Async I/O for concurrent gRPC/Redis calls | ✓ Good — all services async, clean migration |
| SAGA over 2PC | Redis doesn't support XA transactions | ✓ Good — crash recovery works, compensation reliable |
| gRPC for inter-service calls | Lower latency than REST, binary protocol | ✓ Good — idempotency keys built into proto |
| Redis Cluster for DB scaling | HA + sharding, aligns with course requirement | ✓ Good — automatic failover, hash tags work |
| Redis Streams over Kafka | Same redis-py client, no new infra, fits CPU budget | ✓ Good — consumer groups + XAUTOCLAIM sufficient |
| SAGA orchestrator as single replica | Avoids split-brain; domain services scale via HPA | ✓ Good — no coordination issues |
| Lua CAS for atomicity | Single EVAL prevents TOCTOU, no distributed locks needed | ✓ Good — 0 consistency violations under benchmark |
| Fire-and-forget event publishing | Checkout never blocked by event failures | ✓ Good — events are audit trail, not critical path |
| Custom redis:7.2 image | bitnami/redis-cluster:8.0 unavailable | ⚠️ Revisit — works but adds maintenance burden |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-27 after Phase 15 (Execution Strategies) completion*
