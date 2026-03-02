# Deliverables

This document maps each deliverable from the course assignment to our implementation, explaining what choices were made and why.

---

## Phase 1 (March 13)

### 1. Implement Two-Phase Commit and SAGAs protocols in Flask + Redis

**What the assignment asks:** Choose between 2PC, SAGAs, or a managed distributed database for transaction coordination. Implement it using Flask + Redis.

**Our choice: SAGA orchestrator pattern with Quart + Redis Cluster**

We chose SAGAs over Two-Phase Commit (2PC) because Redis does not support XA transactions, which 2PC requires. 2PC also blocks all participants when the coordinator crashes — the opposite of what we need for fault tolerance.

We chose the **orchestrator** variant of SAGAs (as opposed to choreography) because it gives a single owner per transaction. This means crash recovery is straightforward: on startup, the orchestrator scans for incomplete SAGAs and drives them to completion or compensation. With choreography, each service would need its own recovery logic, and detecting "stuck" transactions becomes much harder.

**How it works:**
- Every checkout creates a persistent SAGA record in Redis before any side effects
- The SAGA state machine has explicit states: `STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED`
- On failure at any step, compensation runs in reverse: refund payment (if charged), release stock (if reserved), mark SAGA `FAILED`
- State transitions use a Lua compare-and-swap (CAS) script that validates the expected `from_state` before applying the update, preventing invalid state jumps under concurrent execution
- SAGA creation uses `HSETNX` on the `state` field to prevent duplicate SAGAs from concurrent requests

**Framework choice: Quart + Uvicorn instead of Flask + Gunicorn.** The assignment explicitly allows this ("If you want to use another Python framework (e.g., async Flask with Quart, etc.) you have the right to do so"). Quart is the async equivalent of Flask with the same API. This gives us native `async/await` support, which is critical for non-blocking Redis and gRPC calls. Uvicorn (ASGI server) replaces Gunicorn (WSGI server) to match the async runtime.

**Where to find it:**
- SAGA orchestrator: `orchestrator/saga_orchestrator.py`
- State machine and Lua scripts: `orchestrator/saga_orchestrator.py`
- Checkout endpoint (triggers the SAGA): `order/app.py` -> `/orders/checkout/{order_id}`
- Architecture rationale: `docs/architecture.md` sections 3 and 5

---

### 2. Fault Tolerance — recover from ONE container failing at a time

**What the assignment asks:** The system should recover when any single container (service or database) is killed. The evaluators will kill one container, let it recover, then kill another. The system must remain consistent throughout.

**Our implementation: three independent fault tolerance layers**

**Layer 1 — Circuit Breakers.**
Independent per-service circuit breakers protect the orchestrator from cascading failures. Each breaker (Stock, Payment) has a failure threshold of 5 and a recovery timeout of 30 seconds. When a circuit breaker opens, further calls return immediately without attempting the RPC, and compensation is triggered. The Stock and Payment breakers are independent: a Stock outage does not affect Payment operations.

**Layer 2 — Startup SAGA Recovery Scanner.**
On orchestrator startup, a recovery scanner queries Redis for all non-terminal SAGAs (`STARTED`, `STOCK_RESERVED`, `PAYMENT_CHARGED`, `COMPENSATING`) older than a configurable staleness threshold (default: 300 seconds). Each stale SAGA is driven forward using the original idempotency keys (safe to replay) or to compensation. The gRPC server does not begin accepting new requests until recovery completes. This handles the exact scenario the assignment describes: the payment service dies after receiving a rollback message but before committing it — on recovery, the orchestrator replays the compensation with the same idempotency key, which is safe because operations are idempotent.

**Layer 3 — Container Self-Healing.**
All containers have `restart: always` in Docker Compose, so crashed containers restart automatically. Combined with Redis Cluster retry configuration, services reconnect to Redis automatically after restart.

**Idempotency is the foundation.** Every mutation operation (stock reserve/release, payment charge/refund) accepts an idempotency key and caches the result in Redis using Lua scripts. This means replaying any SAGA step after a crash is always safe — the operation either executes (if it hasn't run before) or returns the cached result (if it already ran). This is implemented via Lua scripts that do an atomic check-and-execute: check if the idempotency key exists, if yes return cached result, if no execute the operation and cache the result.

**Where to find it:**
- Circuit breakers: `orchestrator/circuit_breaker.py`
- Recovery scanner: `orchestrator/saga_orchestrator.py` (startup recovery method)
- Idempotency Lua scripts: `stock/app.py` and `payment/app.py`
- Kill-container test: `scripts/kill_test.py` and `make kill-test`
- Architecture rationale: `docs/architecture.md` section 5

---

### 3. Consistency — no lost money or item counts

**What the assignment asks:** The transaction implementation must provide consistency guarantees (e.g., eventual consistency, serializability, snapshot isolation). No lost money or item counts.

**Our guarantee: eventual consistency via SAGA compensation**

Every checkout reaches a terminal state — either `COMPLETED` (stock deducted, payment charged, order marked paid) or `FAILED` (all side effects compensated). There is no state where money is deducted but stock is not reserved, or vice versa, that persists indefinitely.

**How consistency is enforced:**
- **Atomic Redis operations via Lua scripts.** All read-modify-write operations (subtract stock, charge payment) use Lua scripts that run atomically on the Redis server. This prevents race conditions where two concurrent checkouts both read the same stock count and both succeed.
- **Hash-tagged keys for cluster atomicity.** Keys use Redis Cluster hash tags (`{item:X}`, `{user:X}`, `{saga:X}`) to guarantee that related keys land on the same cluster slot. This is required for Lua scripts that operate on multiple keys (e.g., the stock key and its idempotency cache key must be on the same shard).
- **Compensation retries with exponential backoff.** If a compensation step fails (e.g., the payment service is down when we need to refund), it retries with backoff until it succeeds. Compensation is never abandoned.
- **SAGA state persistence.** SAGA state is stored in Redis, not in memory. If the orchestrator crashes, the state survives and recovery picks it up.

**Verified by:** The `wdm-project-benchmark` consistency test (`make benchmark`) creates 1 item with 100 stock at 1 credit each and 1000 users with 1 credit each. It sends 1000 concurrent checkouts. Only ~100 should succeed (limited by stock). The test then verifies that the final stock count and the sum of user credit deductions are consistent.

**Where to find it:**
- Lua scripts for atomic operations: `stock/app.py`, `payment/app.py`
- Hash tag usage: all Redis key formats in each service
- Consistency test: `make benchmark`

---

### 4. Performance — latency and throughput

**What the assignment asks:** High throughput, low latency, efficient scaling. Stretch goal: zero downtime under failures. Evaluated against 20 CPUs max.

**Our choices for performance:**

- **Quart + Uvicorn (async).** Non-blocking I/O means a single service instance can handle many concurrent requests without thread pool exhaustion. Each checkout involves multiple Redis calls and gRPC calls — async `await` on each means other requests can proceed while waiting for I/O.
- **gRPC with protobuf for inter-service communication.** Binary serialization (protobuf) has lower overhead than JSON. gRPC uses HTTP/2 with multiplexed streams, so multiple concurrent RPCs share a single TCP connection. This matters because the orchestrator makes multiple sequential calls per checkout.
- **Redis Cluster with `hiredis` C parser.** The `hiredis` library is a C extension that parses Redis protocol ~10x faster than the pure Python parser. Combined with `redis.asyncio`, this gives high-throughput non-blocking Redis access.
- **Pipeline for bulk operations.** Batch initialization (`batch_init` endpoints) uses Redis pipelines to send thousands of SET commands in a single round-trip, rather than one-by-one.

**Scaling:**
- Kubernetes HPA scales Order, Stock, and Payment deployments when CPU exceeds 70%
- Three independent Redis Clusters provide fault-isolated data storage
- Total resource budget fits within 20 CPUs

**Stress tested with:** Locust via `make stress-test` (100k pre-populated items, users, and orders). Recommended test: 50-100 concurrent users with 10 users/second ramp-up.

**Where to find it:**
- Async framework: all service `app.py` files
- gRPC definitions: `protos/stock.proto`, `protos/payment.proto`
- Kubernetes HPA: `k8s/` manifests
- Stress test: `make stress-init` and `make stress-test`
- Architecture rationale: `docs/architecture.md` sections 2 and 6

---

### 5. Event-Driven Design (bonus points)

**What the assignment asks:** Event-driven asynchronous architectures get extra points. Reactive microservices.

**Our implementation: Redis Streams for SAGA lifecycle events**

After each SAGA state transition, the orchestrator publishes an event to a Redis Stream:
- `checkout.started`, `stock.reserved`, `payment.charged`, `checkout.completed`, `compensation.triggered`

Consumer groups with `XREADGROUP` provide at-least-once delivery. `XAUTOCLAIM` (Redis 6.2+) automatically reclaims idle messages from crashed consumers, re-delivering them to healthy consumers. Messages exceeding max delivery attempts go to a dead-letter stream.

**Why Redis Streams instead of Kafka or RabbitMQ:**
- Redis is already deployed for SAGA state and data storage — no additional infrastructure
- Built-in consumer groups match the at-least-once delivery requirement
- Fits within the 20-CPU budget (Kafka requires separate broker + ZooKeeper/KRaft processes)

**Important design decision:** Event publishing is fire-and-forget. If the Redis Stream is unavailable, the event is dropped and counted, but the checkout transaction continues. Events are observability artifacts, not part of the consistency guarantee. This means a Redis Streams outage never blocks or fails a checkout.

**Where to find it:**
- Event publisher: `orchestrator/event_publisher.py`
- Event consumer: `orchestrator/event_consumer.py`
- Architecture rationale: `docs/architecture.md` section 4

---

### 6. Architecture Difficulty

**What the assignment asks:** Difficulty of implementation plays a role in evaluation. Synchronous < asynchronous < event-driven.

**Our architecture difficulty level: asynchronous + event-driven**

We implement the most difficult tier by combining:
1. **Async framework** (Quart + Uvicorn) — all I/O is non-blocking
2. **gRPC** for inter-service orchestration — typed contracts with binary serialization
3. **SAGA orchestrator** with persistent state machine — explicit states, Lua CAS transitions, crash recovery
4. **Redis Streams** for event-driven observability — consumer groups, dead-letter handling, auto-claim
5. **Circuit breakers** for cascade failure prevention
6. **Idempotency via Lua scripts** for safe replay after crashes
7. **Redis Cluster** with hash-tagged keys for multi-key atomicity

This is significantly more complex than a synchronous REST-based approach with a single Redis instance.

---

### 7. Benchmark Compatibility

**What the assignment asks:** The wdm-project-benchmark must work on a local machine without changes.

**Our implementation:** The benchmark works unmodified against our system. All external-facing API routes match the original template specification exactly:
- Order: `/orders/create`, `/orders/find`, `/orders/addItem`, `/orders/checkout`
- Stock: `/stock/find`, `/stock/subtract`, `/stock/add`, `/stock/item/create`
- Payment: `/payment/pay`, `/payment/add_funds`, `/payment/create_user`, `/payment/find_user`

Additionally, we added `batch_init` endpoints for the stress test initialization script.

**How to run:**
- Consistency test: `make benchmark`
- Stress test: `make stress-init` then `make stress-test`
- Kill-container test: `make kill-test SERVICE=stock-service` or `make kill-test-all`

---

### 8. Public GitHub Repository

**What the assignment asks:** Public GitHub repository link in the format `{username}/dds26-{team#}`.

**Delivered:** The repository is `dds26-8` and contains all source code, Docker Compose configuration, Kubernetes manifests, architecture documentation, and this deliverables document.

---

### 9. contributions.txt

**What the assignment asks:** A file at the top-level directory where each member describes their contributions.

**Delivered:** `contributions.txt` exists at the repository root. Team members should fill in their individual contributions.

---

## Phase 2 (April 1) — Not Yet Implemented

### Orchestrator Abstraction

**What the assignment asks:** Abstract away the SAGA/2PC protocol into a separate software artifact (Orchestrator). Rewrite the shopping-cart project to use it.

**Current status:** Phase 2 is deferred to the April 1 deadline. However, Phase 1 was designed with Phase 2 in mind:
- The SAGA orchestrator already exists as a dedicated service (`orchestrator/`) with a clean gRPC interface
- The Order service calls the orchestrator via gRPC — it does not implement transaction coordination itself
- The orchestrator's interface (`CheckoutRequest -> CheckoutResponse`) is already abstracted from the shopping-cart domain logic
- Phase 2 will extract the orchestrator into a generic, reusable artifact and provide a new implementation of the shopping-cart that uses it

---

## Summary: Deliverable Checklist

| Deliverable | Status | Location |
|---|---|---|
| SAGA implementation | Done | `orchestrator/saga_orchestrator.py` |
| Fault tolerance (single container failure) | Done | Circuit breakers, recovery scanner, `restart: always` |
| Consistency guarantee | Done | Lua scripts, hash-tagged keys, SAGA compensation |
| Performance optimization | Done | Async Quart, gRPC, hiredis, Redis Cluster |
| Event-driven design (bonus) | Done | Redis Streams with consumer groups |
| Benchmark compatibility | Done | `make benchmark`, `make stress-test` |
| Public GitHub repository | Done | `dds26-8` |
| contributions.txt | Exists | `contributions.txt` (fill in per member) |
| Architecture document | Done | `docs/architecture.md` |
| Phase 2 orchestrator abstraction | Pending | Due April 1 |
