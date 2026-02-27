# Project Research Summary

**Project:** DDS26-8 — Distributed Checkout System (TU Delft DDS Course)
**Domain:** Distributed transactions / SAGA pattern / Microservices
**Researched:** 2026-02-27
**Confidence:** HIGH

## Executive Summary

This project is a subsequent milestone upgrade of an existing Flask+Redis checkout system (Order, Stock, Payment services) to a fully distributed, fault-tolerant architecture using SAGA orchestration, gRPC inter-service communication, Redis Cluster, and Kubernetes scaling. The system is evaluated against a Locust benchmark under a 20 CPU limit and an explicit container-kill fault injection test — meaning consistency under partial failure is the primary success criterion, not just throughput.

The recommended approach is to layer the upgrade in strict dependency order: async framework migration first (Flask to Quart+Uvicorn), then gRPC instrumentation on each domain service, then a dedicated SAGA orchestrator with Redis-persisted state, then compensation and idempotency logic, and finally Redis Cluster and Kubernetes infrastructure. The critical architectural decision — confirmed across all four research dimensions — is that the SAGA orchestrator must persist its state machine transitions to Redis before executing each step. Without this, the container-kill test cannot be passed regardless of other architectural choices. Redis Streams is chosen over Kafka for the event/message layer because it operates within the existing Redis infrastructure with no new operational overhead.

The top risks are not architectural uncertainty (the SAGA pattern is well-understood) but implementation sequencing: specifically, the interaction between async Python (Quart), gRPC's asyncio integration, Redis Cluster's cluster-aware client requirements, and the idempotency contract that every state-changing operation must honor. Five critical pitfalls can each independently cause the evaluation to fail: compensation transactions that themselves fail silently, SAGA state stored only in memory, concurrent checkout races on the same order ID, unknown gRPC call outcomes on pod kill, and using synchronous Redis inside an async event loop. All five must be addressed in Phase 1 before infrastructure work begins.

---

## Key Findings

### Recommended Stack

The migration from Flask+Gunicorn to Quart+Uvicorn is the foundation of everything else. Quart is a drop-in async Flask replacement — routes, blueprints, `abort()`, and `jsonify()` all migrate unchanged; only handlers become `async def` and I/O calls require `await`. This minimizes rewrite risk on a tight deadline (March 13). Uvicorn with `uvicorn[standard]` provides uvloop acceleration and is the industry-standard ASGI server; Hypercorn offers no benefit here.

gRPC via `grpcio 1.65.x` with `grpc.aio` (not plain grpcio, which blocks the event loop) handles all inter-service communication. Proto files for StockService, PaymentService, and SagaOrchestrator must define `idempotency_key` fields on every mutation RPC — this is not optional. Redis async client (`redis.asyncio.Redis` and `redis.asyncio.RedisCluster`) replaces the existing synchronous `redis.Redis`; the package is unchanged, only the import path. Redis Streams replaces the need for Kafka entirely, using the same redis-py client already installed.

**Core technologies:**
- **Quart 0.20.x + Uvicorn 0.30.x**: async Flask replacement, minimal migration cost — avoids FastAPI's greenfield friction
- **grpcio 1.65.x (grpc.aio)**: binary protocol, HTTP/2 multiplexing, strongly-typed RPC contracts — avoids event loop blocking from synchronous grpcio
- **redis.asyncio.RedisCluster (redis-py 5.x)**: async, cluster-aware Redis client — same package as current, different import; avoids MOVED/ASK failures from non-cluster client
- **Redis Streams (built into redis-py 5.x)**: message queue with consumer groups — no Kafka infrastructure required, fits within 20 CPU budget
- **msgspec 0.18.x**: keep as-is for Redis serialization; faster than pydantic, already deployed
- **tenacity 8.x**: async-compatible retry with exponential backoff on gRPC calls — avoids hand-rolled retry logic with subtle async bugs
- **structlog 24.x** (recommended): structured JSON logging across all services — critical for debugging distributed SAGA failures in production

**Remove:** `requests` library entirely — synchronous, blocks Quart event loop, replaced by gRPC for inter-service calls.

### Expected Features

All table-stakes features map directly to the benchmark kill-container test. Missing any one causes the evaluation to fail.

**Must have (table stakes — Phase 1):**
- **SAGA-1: Persistent SAGA State** — Redis-backed state machine record per checkout; without this, crash recovery is impossible
- **SAGA-2: SAGA Orchestrator Service** — dedicated coordinator replacing ad-hoc rollback in Order service; owns all transaction sequencing
- **SAGA-3: Compensating Transactions with Guaranteed Execution** — retry-until-success compensation; current `rollback_stock()` silently drops failed rollbacks
- **SAGA-4: Exactly-Once Checkout Semantics** — order_id as natural idempotency key; prevents double-charge on client retry
- **IDMP-1: Idempotent Service Operations** — `idempotency_key` on every mutation; enables safe orchestrator retry after unknown gRPC outcomes
- **CRASH-1: Crash Recovery on Startup** — orchestrator scans non-terminal SAGAs on boot and resumes them
- **CONCUR-1: Redis Read-Modify-Write Atomicity** — Lua scripts for stock subtraction; eliminates TOCTOU race under concurrent load
- **PERF-1: Quart+Uvicorn Migration** — prerequisite for all async gRPC and Redis work
- **PERF-2: gRPC Inter-Service Communication** — replaces synchronous HTTP between services
- **CONSIST-1: Redis Cluster** — 3 primary + 3 replica nodes per domain; enables replica promotion on primary kill

**Should have (differentiators — Phase 1/2):**
- **DIFF-6: SAGA State Machine with Explicit Transitions** — Python enum + transition table; low effort, high conceptual value for interview/architecture doc
- **DIFF-1: Event-Driven SAGA via Redis Streams** — orchestrator publishes lifecycle events; scores Architecture Difficulty points; only pursue if idempotency is already solid
- **DIFF-3: Circuit Breaker** — prevents cascade failures; pybreaker or ~80 lines custom; moderate complexity
- **DIFF-4: Distributed Tracing (OpenTelemetry + Jaeger)** — visualizes SAGA flow for interview demo; moderate effort

**Defer (Phase 2+):**
- **DIFF-2: Outbox Pattern** — correct solution to dual-write on event publish; only needed if DIFF-1 is implemented
- **DIFF-5: HPA Custom Metrics** — custom Prometheus metrics for autoscaling; high config complexity, low grade uplift vs. effort

**Anti-features (do not build):**
- 2PC over Redis — wrong tool, Redis doesn't support XA
- Custom message queue on raw Redis keys — use Redis Streams
- Pure choreography without orchestration — makes crash recovery intractable
- Authentication/authorization — explicitly out of scope, zero grade impact

### Architecture Approach

The system has five layers: (1) Nginx gateway handling all external HTTP, routing by URL prefix to domain services; (2) three domain services (Order, Stock, Payment), each running both HTTP on port 5000 (for Nginx routing) and gRPC on port 50051 (for orchestrator calls); (3) a dedicated SAGA Orchestrator service that receives checkout commands from Order via gRPC, drives the state machine, persists every transition to Redis before acting, and triggers compensation on failure; (4) Redis Streams on the orchestrator's Redis instance for event publishing and compensation retry queuing; and (5) per-domain Redis Cluster instances providing HA via replica failover.

**Major components:**
1. **Nginx Gateway** — external API boundary; routes unchanged; no modification required
2. **Order Service** — owns order domain; delegates checkout to SAGA Orchestrator via gRPC; stateless and horizontally scalable
3. **Stock Service** — owns inventory; exposes gRPC for SubtractStock/AddStock with idempotency keys; horizontally scalable
4. **Payment Service** — owns user credit; exposes gRPC for ChargeUser/RefundUser with idempotency keys; horizontally scalable
5. **SAGA Orchestrator** — new service; central coordinator; single replica (avoids split-brain); persists state to its own Redis; resumes in-progress SAGAs on restart
6. **Redis Cluster (x3 + orchestrator Redis)** — one cluster per domain plus orchestrator state store; AOF persistence; `maxmemory-policy noeviction`
7. **Redis Streams** — event bus on orchestrator Redis; `saga.events` (lifecycle) and `saga.compensation.retry` (failed compensations awaiting retry)
8. **Kubernetes HPA** — scales Order/Stock/Payment on CPU > 70%; orchestrator stays at single replica; Redis via Bitnami Helm chart StatefulSets

### Critical Pitfalls

1. **Compensation transactions that also fail (P1)** — The existing `rollback_stock()` silently drops failed rollback calls. Fix: persist compensation intent to Redis before executing; retry until success; escalate to COMPENSATION_FAILED dead-letter state rather than swallowing errors. This is the single most dangerous architectural failure mode.

2. **SAGA state stored only in memory (P4/P12)** — Any in-memory SAGA state (Python dict, class instance) is lost on pod kill. Fix: every state transition writes to Redis with the SAGA ID as key before the corresponding service call is made. The orchestrator is stateless between transitions.

3. **Concurrent checkout race on same order (P7)** — Without a distributed lock or atomic check-and-set, two concurrent requests can both succeed on the same order, double-charging and double-deducting. Fix: Redis SETNX on `checkout_in_progress:{order_id}` at checkout start, or a Lua script that atomically checks `paid=False` and sets `paid=True`.

4. **Unknown gRPC outcome on pod kill (P11)** — When a service is killed mid-gRPC call, the outcome is unknown. Triggering compensation on `UNKNOWN` is wrong (may compensate something that never happened). Fix: retry with the same idempotency key until a definitive answer is obtained or retry budget exhausted; enter `STUCK_SAGA` state rather than compensating blindly.

5. **Synchronous Redis/gRPC blocking the Quart event loop (P10/P5)** — Using `redis.Redis` (synchronous) or plain grpcio stubs inside async handlers serializes the event loop, destroying throughput. Fix: use `redis.asyncio.Redis` and `grpc.aio` exclusively; never use `run_in_executor` for gRPC; profile with asyncio debug mode under load.

---

## Implications for Roadmap

Based on research, the dependency chain is unambiguous: async framework before gRPC, gRPC before orchestrator, orchestrator before compensation/idempotency, all logic before infrastructure (Redis Cluster, Kubernetes). Two phases cover the full scope.

### Phase 1: Core SAGA Implementation (Async + gRPC + Orchestrator + Fault Tolerance)

**Rationale:** All table-stakes features are tightly coupled. The async migration (PERF-1) unblocks gRPC (PERF-2), which unblocks the orchestrator (SAGA-2), which requires persistent state (SAGA-1), idempotency (IDMP-1), compensations (SAGA-3), atomicity (CONCUR-1), and crash recovery (CRASH-1). These cannot be meaningfully parallelized. This phase represents the evaluation-critical work — passing the kill-container test and achieving correctness under concurrency.

**Delivers:** A functionally correct distributed checkout that passes the consistency evaluation: concurrent checkout safety, crash recovery, idempotent compensations, exactly-once semantics.

**Addresses:** SAGA-1, SAGA-2, SAGA-3, SAGA-4, IDMP-1, CRASH-1, CONCUR-1, PERF-1, PERF-2, DIFF-6

**Build sequence within phase:**
1. Migrate to Quart+Uvicorn (all three domain services) — validates async compatibility in isolation
2. Add gRPC servers to Stock, Payment, Order services — define proto files with `idempotency_key` fields; test stubs in isolation
3. Build SAGA Orchestrator — state machine, Redis persistence, idempotency key handling; wire Order checkout endpoint to orchestrator
4. Implement compensating transactions — retry-until-success, persist compensation intent before execution
5. Add crash recovery — startup scan of non-terminal SAGAs, resume via idempotent re-execution
6. Add Redis Streams integration — XADD saga lifecycle events; `saga.compensation.retry` stream for failed compensations

**Avoids:** P1 (silent compensation failure), P2 (WATCH misuse — use Lua scripts), P4 (in-memory SAGA state), P5 (gRPC blocking), P7 (concurrent checkout race), P10 (sync Redis in async handlers), P11 (blind compensation on unknown outcome), P12 (orchestrator SPOF via Redis-persisted state)

**Research flag:** Standard patterns — SAGA with Redis is well-documented. gRPC + asyncio lifecycle management (P5, P9) warrants careful attention to grpc.aio channel setup. No `/gsd:research-phase` needed; ARCHITECTURE.md and STACK.md provide sufficient implementation detail.

---

### Phase 2: Infrastructure Hardening (Redis Cluster + Kubernetes + Tuning)

**Rationale:** Redis Cluster and Kubernetes configuration depend on stable application logic. Attempting to debug SAGA correctness on top of cluster failover and HPA simultaneously makes both harder. Infrastructure work is largely mechanical once the application layer is correct. This phase converts the working system into a production-grade deployment and optimizes for benchmark throughput.

**Delivers:** High-availability deployment with automatic failover, horizontal autoscaling, and benchmark-optimized resource allocation within the 20 CPU limit.

**Addresses:** CONSIST-1, DIFF-4, DIFF-5 (optional), Kubernetes HPA configuration

**Build sequence within phase:**
1. Configure Redis Cluster for each domain service — switch to `redis.asyncio.RedisCluster`; test MOVED/ASK handling; verify AOF persistence and `noeviction` policy
2. Update Kubernetes manifests — add orchestrator Deployment (single replica, `maxSurge=0`); add Redis Cluster Helm values; configure HPA for domain services
3. Validate Redis hash tags for co-located keys — ensure intra-service Lua scripts stay within single slot (P3)
4. Benchmark and tune — profile CPU allocation; adjust Uvicorn worker counts; tune connection pool sizes; verify Redis Cluster failover under load (P14, P15)

**Uses:** Redis Cluster (Bitnami Helm), redis.asyncio.RedisCluster, Kubernetes HPA, grpc health checking protocol for readiness probes

**Implements:** Redis Cluster per-domain architecture, Kubernetes scaling layer, observability (structlog, optional OpenTelemetry)

**Avoids:** P3 (cross-slot Lua scripts), P8 (non-cluster Redis client), P9 (gRPC connection exhaustion on HPA scale-up), P14 (Redis failover window shorter than SAGA timeout), P15 (CPU budget misallocation)

**Research flag:** Standard patterns for Redis Cluster Helm deployment and Kubernetes HPA. One area warrants validation: the 20 CPU budget allocation across 3 domain services (up to 8 pods each at 0.5 CPU request = 12 CPU), orchestrator (0.5 CPU), and Redis Cluster nodes (up to 12 nodes × 0.5 CPU = 6 CPU) totals 18.5 CPU — tight but within budget. Verify actual Redis CPU draw under benchmark load before finalizing HPA max replicas.

---

### Phase Ordering Rationale

- Phase 1 before Phase 2 because Redis Cluster adds a new failure mode (MOVED/ASK, failover windows) that should not be debugged simultaneously with SAGA correctness. Validating Phase 1 on single-node Redis first isolates problems.
- Within Phase 1, async migration before gRPC because grpc.aio requires an asyncio event loop; adding gRPC to a WSGI service is unsupported.
- SAGA orchestrator before compensations/recovery because compensation logic requires an existing state machine to extend.
- Redis Streams after orchestrator because the orchestrator is the producer; the infrastructure is meaningless without the producer being correct.
- Kubernetes configuration last because containerization is already in place from the existing system; this phase adds cluster configuration, not container scaffolding.

### Research Flags

Phases needing deeper research during planning:
- **Phase 1 (gRPC channel lifecycle):** grpc.aio channel creation, keepalive configuration, and connection health checks are non-obvious. The research identifies the pattern but implementation requires careful attention to `grpc.aio.insecure_channel()` lifecycle and the interaction with Quart startup/shutdown hooks.
- **Phase 2 (Redis Cluster CPU budget):** Actual Redis CPU consumption under benchmark load is unknown. Budget math is tight. Recommend a brief load test with Redis Cluster configured before finalizing K8s resource limits.

Phases with standard patterns (skip research-phase):
- **Phase 1 (SAGA state machine):** Well-documented pattern. ARCHITECTURE.md provides the complete state transition table and step/compensation mapping.
- **Phase 1 (Quart+Uvicorn migration):** Mechanical. Flask routes translate directly. STACK.md covers all edge cases.
- **Phase 2 (Kubernetes HPA):** Standard Kubernetes configuration. Bitnami Redis Cluster Helm chart is well-documented.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Technology choices are well-reasoned with clear tradeoff analysis. Quart/Uvicorn/grpcio are stable, production-used libraries. Only specific patch versions require PyPI verification (web search was unavailable during research). |
| Features | HIGH | Feature classification is directly driven by the course evaluation criteria (kill-container test, benchmark, Architecture Difficulty interview). Table stakes map 1:1 to evaluation scenarios. |
| Architecture | HIGH | SAGA orchestrator pattern with Redis-persisted state is the industry-standard approach for this problem class. Component boundaries are clear and internally consistent across all four research files. |
| Pitfalls | HIGH | 15 concrete pitfalls identified with specific code-level prevention strategies. Five critical pitfalls (P1, P4, P7, P10, P11) independently cause evaluation failure and align across FEATURES.md and ARCHITECTURE.md findings. |

**Overall confidence:** HIGH

### Gaps to Address

- **Library version pinning:** Specific patch versions (Quart 0.20.x, Uvicorn 0.30.x, grpcio 1.65.x) were derived from training data (cutoff August 2025). Verify each against `pip index versions <package>` before pinning in requirements.txt. Major.minor version recommendations are reliable; patch versions may have advanced.
- **20 CPU budget validation:** The resource math fits within 20 CPUs on paper but Redis Cluster CPU draw under benchmark load is empirically unknown. A profiling run before finalizing K8s resource limits is recommended.
- **Redis Cluster vs. shared cluster tradeoff:** Research recommends per-domain Redis Clusters (three separate clusters) for architectural cleanliness but acknowledges a simpler alternative (single shared cluster with keyspace prefixes) may be necessary under the 20 CPU constraint. Decide after profiling.
- **Instructor expectation on message queue:** STACK.md flags a risk that the instructor may expect Kafka for "event-driven architecture" points. Redis Streams is architecturally equivalent and superior for this use case, but clarifying with TAs before committing to Redis Streams is recommended.

---

## Sources

### Primary (HIGH confidence)
- STACK.md research (2026-02-27) — Quart, Uvicorn, grpcio, redis-py, Redis Streams vs. Kafka analysis; requirements.txt for target stack
- FEATURES.md research (2026-02-27) — table stakes/differentiator/anti-feature classification; feature dependency map; benchmark test scenario mapping
- ARCHITECTURE.md research (2026-02-27) — component boundaries, SAGA state machine design, data flow diagrams, K8s topology, build order
- PITFALLS.md research (2026-02-27) — 15 concrete pitfalls with severity classification and prevention strategies

### Secondary (MEDIUM confidence)
- Existing codebase analysis (CONCERNS.md referenced in research) — confirmed oversell race condition in stock subtraction, silent rollback failure in `rollback_stock()`
- Training data on grpcio, redis-py, Quart (cutoff August 2025) — architecture patterns HIGH confidence; specific patch versions MEDIUM confidence

### Tertiary (requires validation)
- CPU budget math — modeled from K8s resource request guidelines; actual Redis Cluster CPU draw under locust benchmark load is estimated, not measured

---

*Research completed: 2026-02-27*
*Ready for roadmap: yes*
