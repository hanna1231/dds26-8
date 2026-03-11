# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Distributed Checkout System

**Shipped:** 2026-03-11
**Phases:** 7 | **Plans:** 21 | **Commits:** 98
**Timeline:** 6 days (Feb 24 → Mar 2)

### What Was Built
- Complete SAGA orchestrator with Redis-persisted state, Lua CAS transitions, and idempotent compensation
- gRPC communication layer with proto3 contracts, dual-server pattern, and idempotency keys
- Event-driven architecture via Redis Streams with consumer groups and XAUTOCLAIM
- Fault tolerance: circuit breakers, startup SAGA recovery, container-kill consistency
- Per-domain Redis Cluster (3+3 nodes) with hash tag slot co-location
- Kubernetes HPA + Docker Compose for both production and local dev
- 37 integration tests, benchmark passing with 0 consistency violations, automated kill-test scripts

### What Worked
- Wave-based plan parallelization within phases (e.g., Phase 1 all 3 service migrations in parallel)
- Lua CAS pattern established early (Phase 2 idempotency) and reused throughout (Phase 3 SAGA transitions, Phase 7 CAS retry)
- Strict phase dependency chain prevented rework — each phase built cleanly on the previous
- Proto-first gRPC design made orchestrator wiring straightforward
- Fire-and-forget event publishing kept checkout path clean and fast

### What Was Inefficient
- SUMMARY.md frontmatter `requirements_completed` field missed in Phases 3 and 7 — audit caught it but shouldn't have been needed
- ROADMAP.md Phase 4 status not updated to "Complete" — stale metadata
- `_stop_event` global bug in orchestrator/app.py went unnoticed until audit — graceful shutdown path broken but CancelledError masked the issue
- Phase 6 Redis Cluster plan (06-01) took disproportionate effort (675 execution units) due to hash tag migration across all services

### Patterns Established
- `before_serving/after_serving` lifecycle hooks for async resource management in Quart
- Lua CAS (compare-and-swap) pattern for all Redis atomicity needs — no distributed locks
- Dual-server pattern: HTTP :5000 + gRPC :50051 running concurrently via Quart background tasks
- Hash tag prefixes (`{item:}`, `{user:}`, `{saga:}`) for Redis Cluster slot co-location
- Circuit breaker per downstream service (independent failure domains)

### Key Lessons
1. Lua scripts must be tested with hash tags from the start — retrofitting hash tags across services is expensive
2. Fire-and-forget event publishing is the right default for audit/observability events — never block the critical path
3. Single-replica orchestrator avoids split-brain complexity; horizontal scaling belongs at the domain service layer
4. Custom Docker images (redis:7.2) add maintenance burden — prefer upstream images when available

### Cost Observations
- Model mix: primarily Sonnet for execution, Opus for planning/research
- Notable: Phase 6 infrastructure was the most expensive phase; proto/gRPC phases were fastest

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Commits | Phases | Key Change |
|-----------|---------|--------|------------|
| v1.0 | 98 | 7 | Initial build — established SAGA + gRPC + Redis Cluster patterns |

### Cumulative Quality

| Milestone | Tests | Requirements | Audit Score |
|-----------|-------|-------------|-------------|
| v1.0 | 37 | 35/35 | 34/35 integration |

### Top Lessons (Verified Across Milestones)

1. Lua CAS eliminates the need for distributed locks in Redis — simpler and more reliable
2. Strict phase dependencies prevent rework but require accurate roadmap ordering
