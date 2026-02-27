# Features Research: Distributed Checkout System

**Research Date:** 2026-02-27
**Milestone:** Phase 1 — SAGA pattern, fault tolerance, and consistency guarantees
**Context:** Adding distributed transaction coordination to an existing Flask+Redis checkout system (Order, Stock, Payment services)

---

## What This Research Covers

This document maps the features a production-grade distributed checkout system needs for SAGA orchestration, idempotency, crash recovery, consistency guarantees, and observability — then classifies each as table stakes (graded), differentiators (grade boosters), or anti-features (traps to avoid) for the DDS26-8 TU Delft course evaluation.

**Evaluation criteria driving classification:**
1. Consistency — no lost money or item counts (kill-container test)
2. Performance — latency and throughput (locust benchmark, 20 CPU max)
3. Architecture Difficulty — synchronous < asynchronous < event-driven (harder = more points)

---

## Table Stakes

*Must-have for consistency and correctness. The course benchmark and kill-container test will exercise these directly. Missing any of these means failing the consistency evaluation.*

---

### SAGA-1: Persistent SAGA State

**What it is:** Every checkout transaction creates a SAGA record in durable storage before any side effects occur. The record tracks which steps have completed and what compensations are needed.

**Why it's required:** When a container is killed mid-transaction (the exact evaluation scenario), the system must know what happened. Without persisted state, a crash between stock deduction and payment leaves the system permanently inconsistent — stock reduced, payment never taken, no record of what to roll back.

**Minimum viable form:** A Redis key per saga_id storing: `{saga_id, order_id, status, steps_completed[], compensation_log[]}`. Status transitions: STARTED → STOCK_RESERVED → PAYMENT_PENDING → COMPLETED | COMPENSATING → COMPENSATED.

**Complexity:** Medium. Redis WATCH/MULTI/EXEC or a single hset with field-level updates. The schema must be designed upfront because changes require migration.

**Dependencies:** SAGA-2 (orchestrator), SAGA-5 (idempotency), CRASH-1 (recovery on startup).

---

### SAGA-2: SAGA Orchestrator Service

**What it is:** A dedicated coordinator (separate from the Order service) that drives the checkout workflow: reserve stock → charge payment → confirm order → done. On failure, it drives compensation in reverse: refund payment → restore stock → mark order failed.

**Why it's required:** The current `order/app.py` does inline rollback with no durability guarantees. If the order service crashes during rollback, rollback stops silently. An orchestrator with persisted state can resume compensation from wherever it left off.

**Minimum viable form:** A single orchestrator module (can live inside the Order service as a separate class/module for Phase 1, extracted as a standalone service for Phase 2). Accepts checkout commands, drives steps sequentially, writes state transitions to Redis before each step.

**Complexity:** High. Most complex single piece of the system. Every state transition must be atomic with respect to the SAGA log. Requires careful sequencing: log the intent, perform the action, log the result.

**Dependencies:** SAGA-1 (persistent state), IDMP-1 (idempotent steps), CRASH-1 (recovery trigger).

**Note for Phase 2:** The course explicitly requires extracting this as a separate abstraction. Design the orchestrator with a clean interface from day one. The boundary is: Order service owns order domain logic, Orchestrator owns transaction coordination.

---

### SAGA-3: Compensating Transactions with Guaranteed Execution

**What it is:** Each SAGA step has a corresponding compensation action. Compensations must be idempotent and must be retried until they succeed. A compensation cannot fail permanently.

**Existing state:** The current `rollback_stock()` makes HTTP calls to restore stock. If any restore call fails (service down, network error), it silently drops the rollback and returns an error. This is incorrect behavior for a compensation.

**Minimum viable form:**
- Stock compensation: `add_stock(item_id, quantity)` — idempotent, retried until success
- Payment compensation: `refund_payment(user_id, amount)` — idempotent, retried until success
- Compensations must be logged to SAGA state before execution so they are not lost on crash

**Complexity:** Medium-High. The compensation logic itself is simple (reverse the operation), but guaranteeing execution requires retry loops with backoff and the orchestrator must persist "compensation in progress" state.

**Dependencies:** SAGA-1 (state log), IDMP-1 (idempotent operations), SAGA-2 (orchestrator drives retries).

---

### SAGA-4: Exactly-Once Checkout Semantics

**What it is:** A single checkout request produces exactly one transaction outcome regardless of how many times it is submitted. Retrying a checkout that already succeeded returns the success result without re-executing.

**Why it's required:** The locust benchmark will hammer the system with concurrent requests. Network timeouts cause clients to retry. Without this, a retry after a successful payment charges the user twice.

**Minimum viable form:** An idempotency key on the checkout endpoint (can use order_id as the natural key — an order can only be checked out once). Before starting a new SAGA, check if a SAGA already exists for this order_id. If completed, return the existing result. If in-progress, return 409 or wait.

**Complexity:** Medium. The order_id as natural idempotency key simplifies this significantly compared to client-generated keys. Redis SET NX (set if not exists) on the SAGA key provides the atomic check-and-create.

**Dependencies:** SAGA-1 (SAGA record stores outcome), SAGA-2 (orchestrator checks before starting).

---

### IDMP-1: Idempotent Service Operations

**What it is:** Individual service operations (subtract stock, charge payment) can be called multiple times with the same parameters and produce the same result without double-applying effects.

**Why it's required:** The orchestrator must retry failed steps. If a gRPC call to the Stock service times out (network partition, not a crash), the orchestrator doesn't know if the subtraction happened. It must be able to call subtract again safely.

**Minimum viable form:** Each operation accepts an optional `idempotency_key` parameter (can be `saga_id + step_name`). The service checks if this key was already processed. If yes, return the previous result without re-applying.

**Implementation for Redis:** Store processed keys with a TTL (e.g., 24 hours): `SET idempotent:{key} result EX 86400`. Before executing, check this key. This adds one Redis read per operation but eliminates double-application bugs.

**Complexity:** Medium. The mechanism is straightforward. The tricky part is defining what "same result" means when the underlying state has changed (e.g., stock was added then subtracted again). Scoping idempotency to the SAGA step key solves this.

**Dependencies:** SAGA-3 (compensations must be idempotent), SAGA-4 (checkout idempotency).

---

### CRASH-1: Crash Recovery on Service Startup

**What it is:** When a service restarts after a crash, it scans for in-progress SAGAs and either completes them or compensates them. No SAGA is abandoned in a partial state.

**Why it's required:** This is the explicit evaluation scenario — kill one container, let system recover, check consistency. If the orchestrator crashes mid-checkout, all in-flight transactions must resolve to one of: fully committed or fully compensated.

**Minimum viable form:** On orchestrator startup, query Redis for all SAGAs with status STARTED, STOCK_RESERVED, or PAYMENT_PENDING. For each:
- If STARTED and no steps completed: mark COMPENSATED (nothing happened yet, safe)
- If STOCK_RESERVED and payment not attempted: trigger compensation (restore stock)
- If PAYMENT_PENDING (timeout ambiguous): query payment service for final status, then commit or compensate

**Complexity:** Medium-High. The startup scan must handle concurrent recovery (multiple orchestrator instances starting simultaneously) — use Redis SET NX to claim a SAGA for recovery before processing.

**Dependencies:** SAGA-1 (persistent state to scan), IDMP-1 (recovery operations must be idempotent), SAGA-2 (orchestrator drives recovery).

---

### CONCUR-1: Read-Modify-Write Atomicity in Redis

**What it is:** Stock subtraction and payment deduction must be atomic. Two concurrent checkouts for the same item cannot both read the same stock level and both succeed, leaving the count negative.

**Existing state:** Current code does `GET item → check stock → SET item` without any concurrency protection. This is a documented bug in CONCERNS.md — concurrent checkouts on the same item will oversell.

**Minimum viable form:** Use Redis `WATCH/MULTI/EXEC` (optimistic locking). WATCH the key, read the value, compute new value, execute MULTI/EXEC — if key changed between WATCH and EXEC, the transaction aborts and must retry.

**Alternative:** Redis Lua scripts execute atomically on the server. A Lua script for `subtract_stock` that checks-and-subtracts in one atomic operation is simpler than WATCH/MULTI/EXEC and does not require client-side retry loops.

**Complexity:** Low-Medium. Redis Lua scripts are the simpler path. The script is ~10 lines. The tricky part is handling the MULTI/EXEC retry loop correctly if using WATCH approach.

**Dependencies:** IDMP-1 (concurrent retries need idempotency), SAGA-3 (compensations touch the same keys).

---

### PERF-1: Async Framework Migration (Quart+Uvicorn)

**What it is:** Replace Flask+Gunicorn with Quart+Uvicorn. Quart is an async-compatible Flask fork. Uvicorn is an ASGI server.

**Why it's required for this milestone:** Async I/O is mandatory for gRPC inter-service calls (grpcio-tools requires async context) and for handling concurrent requests without thread exhaustion. With 2 Gunicorn workers and blocking HTTP calls, the system saturates quickly under locust load.

**Complexity:** Medium. Quart's API is near-identical to Flask. Route handlers change from `def` to `async def`, and calls to gRPC/Redis use `await`. Most existing code migrates mechanically. The risk is async-incompatible dependencies.

**Dependencies:** PERF-2 (gRPC requires async), all service endpoints must be re-validated after migration.

---

### PERF-2: gRPC for Inter-Service Communication

**What it is:** Replace HTTP REST calls between services with gRPC (binary protocol, HTTP/2, strongly-typed contracts via protobuf).

**Why it's required for this milestone:** Team decision already made. gRPC has lower per-call overhead and enables streaming. More importantly, gRPC provides proper status codes for retry logic (UNAVAILABLE vs ALREADY_EXISTS vs OK), which the current requests library conflates into HTTP 400s.

**Minimum viable form:** Define `.proto` files for StockService, PaymentService operations used by the orchestrator. Generate Python stubs. Replace `send_post_request()` calls in the orchestrator with gRPC stub calls.

**Complexity:** Medium-High. Protobuf schema design is upfront cost. gRPC-asyncio client requires Quart-compatible async patterns. Connection management (channel pooling, reconnects) needs explicit handling.

**Dependencies:** PERF-1 (async framework), SAGA-3 (retry logic uses gRPC status codes).

---

### CONSIST-1: Redis Cluster for High Availability

**What it is:** Replace single Redis instances with Redis Cluster (3 primary + 3 replica nodes). Cluster provides automatic failover — if a primary crashes, its replica is promoted.

**Why it's required:** The kill-container test kills services AND potentially databases. If the stock-db Redis instance crashes, all stock operations fail permanently. With Redis Cluster, the replica takes over within seconds.

**Minimum viable form:** Redis Cluster configured with `cluster-enabled yes`, at least 3 primaries. Application connects via cluster-aware Redis client (`redis-py` with `RedisCluster` client class).

**Note:** SAGA state storage (SAGA-1) must also use Redis Cluster. Single-node Redis for SAGA state defeats the purpose if that node crashes.

**Complexity:** Medium. Redis Cluster is well-supported in `redis-py`. Kubernetes StatefulSet configuration for Redis Cluster is the harder part (persistent volumes, pod disruption budgets).

**Dependencies:** All SAGA features (SAGA state is stored in Redis), CRASH-1 (recovery needs Redis to be available after crash).

---

## Differentiators

*Features that earn Architecture Difficulty points or demonstrate deeper understanding. Not tested directly by the benchmark, but evaluated in the rigorous interview and assessed in architecture document.*

---

### DIFF-1: Event-Driven SAGA via Message Queue (Choreography Layer)

**What it is:** In addition to (or instead of) the orchestrator driving steps via direct gRPC calls, services emit domain events to a message queue (Kafka or Redis Streams). Services subscribe to events and react independently.

**Why it differentiates:** The evaluation criteria explicitly score event-driven architecture higher than synchronous or async-but-synchronous. Pure orchestration with gRPC is asynchronous but still synchronous in coordination (orchestrator waits for each reply). Event-driven choreography decouples services in time.

**Example flow:** Orchestrator publishes `CheckoutStarted` → Stock service consumes and publishes `StockReserved` or `StockFailed` → Orchestrator consumes and publishes `PaymentRequested` → Payment service consumes and publishes `PaymentCompleted` or `PaymentFailed` → Orchestrator consumes and commits or compensates.

**Kafka vs Redis Streams:** Both qualify. Redis Streams has lower operational overhead (same Redis cluster already required). Kafka has better consumer group semantics and broader industry recognition. For the course, Redis Streams is probably the pragmatic choice.

**Complexity:** High. Adds consumer group management, at-least-once delivery handling, event schema versioning concerns, and partial failure during event processing. Significantly harder to debug than direct gRPC calls.

**Dependencies:** SAGA-1 (state must track event correlation), IDMP-1 (events may be delivered more than once), SAGA-2 (orchestrator now consumes events instead of awaiting gRPC replies).

**Trade-off:** Worth doing if time allows. Interview questions will probe whether the team understands the consistency implications of at-least-once vs exactly-once event delivery. Do not add this without understanding the answers.

---

### DIFF-2: Outbox Pattern for Reliable Event Publishing

**What it is:** Instead of publishing events directly to Kafka/Redis Streams after a database write (which creates a dual-write problem), write events to an "outbox" table in Redis atomically with the state change. A background poller reads the outbox and publishes events, then marks them as sent.

**Why it differentiates:** This is the correct solution to "what happens if the service crashes after writing to Redis but before publishing the event?" Without the outbox pattern, events are lost. With it, events are published reliably even after crashes.

**Complexity:** Medium. The outbox is a Redis sorted set ordered by timestamp. The poller is a background asyncio task. The tricky part is handling "at least once" semantics (event may be published twice if the poller crashes after publishing but before marking sent) — which requires IDMP-1 to already be in place.

**Dependencies:** DIFF-1 (requires message queue), IDMP-1 (downstream consumers must handle duplicate events), SAGA-1 (outbox entries tied to SAGA state).

---

### DIFF-3: Circuit Breaker for Service Dependencies

**What it is:** Track failure rates for calls to each downstream service (Stock, Payment). When the failure rate exceeds a threshold (e.g., 5 failures in 10 seconds), open the circuit — fail fast without attempting the call. After a timeout, allow one trial call through (half-open state). On success, close the circuit.

**Why it differentiates:** Prevents cascade failures. When the Payment service is overloaded, the Stock service should not also fail because it is waiting for Payment responses. Circuit breakers are a standard resilience pattern discussed in distributed systems courses.

**Complexity:** Medium. Python library `tenacity` handles retry logic; circuit breaker requires either a library (`pybreaker`) or ~80 lines of custom code. State must be shared across async workers (store in Redis or in-process with asyncio lock).

**Dependencies:** PERF-1 (async framework), PERF-2 (gRPC calls are what's circuit-broken), SAGA-2 (orchestrator opens circuit on repeated SAGA failures).

---

### DIFF-4: Distributed Tracing

**What it is:** Propagate a trace ID through every SAGA step, gRPC call, and Redis operation. Use OpenTelemetry to emit traces. Visualize in Jaeger or Zipkin.

**Why it differentiates:** Makes distributed transaction debugging tractable. Without tracing, debugging "why did this checkout fail" in a distributed system means correlating logs across 3+ services by timestamp and guessing. With tracing, the entire checkout flow is visible in one UI with timing and error attribution.

**Practical value:** During the interview, being able to show a trace of a failed SAGA with compensation is significantly more convincing than describing it verbally.

**Complexity:** Medium. OpenTelemetry Python SDK auto-instruments gRPC and Redis. Manual instrumentation needed for SAGA steps. Jaeger runs as an additional container. The main cost is time, not complexity.

**Dependencies:** PERF-2 (gRPC instrumented automatically), SAGA-2 (add span per SAGA step).

---

### DIFF-5: Kubernetes HPA with Custom Metrics

**What it is:** Configure Horizontal Pod Autoscaler to scale service replicas based on checkout queue depth or pending SAGA count (custom Prometheus metrics), rather than just CPU utilization.

**Why it differentiates:** CPU-based autoscaling is the default and straightforward. Custom metric autoscaling demonstrates understanding of application-level load signals. Under locust benchmark, queue depth is a more accurate scaling signal than CPU for I/O-bound services.

**Complexity:** Medium-High. Requires Prometheus adapter for Kubernetes custom metrics API, plus Prometheus scraping the application metrics endpoint. Non-trivial to configure correctly.

**Dependencies:** PERF-1 (async framework produces different CPU profiles), correct resource limits in K8s manifests.

---

### DIFF-6: SAGA State Machine with Explicit Transitions

**What it is:** Model SAGA state as an explicit finite state machine with validated transitions. Invalid transitions (e.g., COMPENSATED → STOCK_RESERVED) raise errors. All transitions are logged.

**Why it differentiates:** Demonstrates formalism about distributed transaction states. Easy to reason about correctness. Easy to present in the architecture document.

**Minimum viable form:** A Python enum for SAGA states and a transition table. The orchestrator validates before writing new state. ~50 lines of code but high conceptual value.

**Complexity:** Low. Pure Python. No additional infrastructure.

**Dependencies:** SAGA-1 (state storage), SAGA-2 (orchestrator enforces transitions).

---

## Anti-Features

*Things to deliberately not build. These are over-engineering traps that consume time without improving the grade.*

---

### ANTI-1: Two-Phase Commit (2PC) Protocol

**Why not:** Redis does not support XA transactions. Implementing 2PC across three Redis instances requires a custom coordinator that is strictly harder to build correctly than SAGA. The course constraint (Redis as database) makes 2PC a wrong fit, not a harder version of the right answer. SAGAs are the correct choice here.

**What to say in the interview:** "We chose SAGA over 2PC because Redis doesn't support XA/distributed transactions. SAGA's compensating transaction model is the appropriate pattern for services with independent data stores. 2PC would require either a custom distributed lock manager or accepting that one component failure blocks the entire system."

---

### ANTI-2: Custom Message Queue Implementation

**Why not:** Building a custom queue on top of raw Redis keys (not Redis Streams) wastes significant time. Redis Streams (or Kafka) already provide consumer groups, at-least-once delivery, retention, and replay. A custom implementation will be buggy under concurrent consumers.

---

### ANTI-3: Saga Choreography Without Orchestration

**Why not:** Pure choreography (services react to events without a central coordinator) is architecturally elegant but harder to make crash-recoverable. Without a coordinator tracking SAGA state, determining "which compensations have run" requires querying multiple services and correlating event logs. For a 3-service system, an orchestrator is simpler and more robust.

**Note:** Adding choreography on top of orchestration (DIFF-1) is valuable. Replacing orchestration with pure choreography is not.

---

### ANTI-4: Synchronous Distributed Locking with Redis SETNX

**Why not:** Using Redis SETNX as a distributed lock for checkout (lock the order, run checkout, unlock) is tempting but incorrect. Locks require TTL management, lock extension under slow operations, and proper release on crash. Redlock (the standard Redis distributed lock) has known edge cases under clock skew. The correct approach is idempotency (IDMP-1) + optimistic concurrency (CONCUR-1), not locks.

---

### ANTI-5: Full Event Sourcing

**Why not:** Storing all state as an immutable event log (event sourcing) instead of mutable Redis records is a significant architectural change. It adds complexity (projections, snapshots, event schema migration) and time cost that far exceeds the grade benefit. The course is not evaluating event sourcing.

---

### ANTI-6: Authentication and Authorization

**Why not:** Explicitly out of scope per course requirements. The benchmark and kill-container test do not authenticate. Adding JWT validation, API keys, or RBAC consumes time with zero grade impact.

---

### ANTI-7: Custom Benchmark

**Why not:** Bonus points only per project spec. If everything else works, this is fine. If building custom benchmark competes for time with SAGA implementation or crash recovery, defer it.

---

## Feature Dependencies Map

```
SAGA-1 (Persistent State)
  └── required by SAGA-2 (Orchestrator)
  └── required by CRASH-1 (Recovery)
  └── required by SAGA-4 (Exactly-Once)
  └── required by DIFF-2 (Outbox Pattern)

SAGA-2 (Orchestrator)
  └── required by SAGA-3 (Compensating Transactions)
  └── required by CRASH-1 (Recovery)
  └── required by DIFF-1 (Event-Driven Layer)
  └── required by DIFF-3 (Circuit Breaker)

IDMP-1 (Idempotent Operations)
  └── required by SAGA-3 (Compensations)
  └── required by SAGA-4 (Checkout Idempotency)
  └── required by CRASH-1 (Recovery Retries)
  └── required by DIFF-1 (Event-Driven at-least-once)

CONCUR-1 (Redis Atomicity)
  └── required by SAGA-3 (Compensation correctness)
  └── enables correct behavior under PERF-2 (concurrent gRPC calls)

PERF-1 (Quart+Uvicorn)
  └── required by PERF-2 (gRPC async)
  └── required by DIFF-1 (async event consumption)

PERF-2 (gRPC)
  └── required by DIFF-3 (circuit breaker wraps gRPC)
  └── required by DIFF-4 (tracing auto-instruments gRPC)

CONSIST-1 (Redis Cluster)
  └── depends on SAGA-1 (cluster stores SAGA state)
  └── depends on CONCUR-1 (cluster-aware atomicity)
```

**Critical path for Phase 1:** SAGA-1 → SAGA-2 → SAGA-3 → CRASH-1 → IDMP-1 → CONCUR-1 → PERF-1 → PERF-2 → CONSIST-1

---

## Complexity Summary

| Feature | Category | Complexity | Phase |
|---------|----------|------------|-------|
| SAGA-1: Persistent SAGA State | Table Stakes | Medium | Phase 1 |
| SAGA-2: SAGA Orchestrator | Table Stakes | High | Phase 1 (extract in Phase 2) |
| SAGA-3: Compensating Transactions | Table Stakes | Medium-High | Phase 1 |
| SAGA-4: Exactly-Once Checkout | Table Stakes | Medium | Phase 1 |
| IDMP-1: Idempotent Operations | Table Stakes | Medium | Phase 1 |
| CRASH-1: Crash Recovery on Startup | Table Stakes | Medium-High | Phase 1 |
| CONCUR-1: Redis Read-Modify-Write Atomicity | Table Stakes | Low-Medium | Phase 1 |
| PERF-1: Quart+Uvicorn Migration | Table Stakes | Medium | Phase 1 |
| PERF-2: gRPC Inter-Service | Table Stakes | Medium-High | Phase 1 |
| CONSIST-1: Redis Cluster | Table Stakes | Medium | Phase 1 |
| DIFF-1: Event-Driven SAGA | Differentiator | High | Phase 1/2 |
| DIFF-2: Outbox Pattern | Differentiator | Medium | Phase 2 |
| DIFF-3: Circuit Breaker | Differentiator | Medium | Phase 1/2 |
| DIFF-4: Distributed Tracing | Differentiator | Medium | Phase 2 |
| DIFF-5: HPA Custom Metrics | Differentiator | Medium-High | Phase 2 |
| DIFF-6: SAGA State Machine | Differentiator | Low | Phase 1 |

---

## What the Benchmark Actually Tests

The locust benchmark and kill-container evaluation test specific behaviors that map directly to features:

| Test Scenario | Feature Required |
|---------------|-----------------|
| Concurrent checkouts for same item | CONCUR-1 |
| Retry after timeout — no double charge | IDMP-1, SAGA-4 |
| Kill order service mid-checkout | CRASH-1, SAGA-1 |
| Kill stock service mid-checkout | SAGA-3 (compensation), CONSIST-1 |
| Kill payment service after stock deducted | CRASH-1, SAGA-3 |
| High throughput (locust, 20 CPUs) | PERF-1, PERF-2, CONSIST-1 |
| Check consistency after recovery | SAGA-1 (all SAGAs resolved), CRASH-1 |

No test directly measures event-driven architecture, circuit breakers, or tracing. Those score Architecture Difficulty points in the interview, not consistency points in the benchmark.

---

*Research: 2026-02-27 — feeds into requirements definition for Phase 1 (due March 13)*
