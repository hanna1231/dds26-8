# Pitfalls: Distributed Checkout System

**Research Date:** 2026-02-27
**Scope:** Redis + SAGA + gRPC + Kubernetes distributed checkout system
**Context:** TU Delft DDS course — adding SAGA orchestrator, gRPC, Redis Cluster, message queue, fault tolerance, and Kubernetes scaling to existing Flask+Redis checkout system. Evaluated with container kills and benchmark under 20 CPU limit.

---

## P1 — SAGA Compensation That Also Fails (Double Fault)

**What goes wrong:** The most dangerous SAGA failure mode is a compensating transaction that itself fails. In this system, if stock has been deducted for 3 items and the payment fails, the rollback calls `POST /stock/subtract/{item_id}/{-quantity}` (or an add endpoint) for each. If the stock service crashes or returns 500 during rollback, one item remains deducted while the order is never completed. The system is now inconsistent — stock lost, no order, user not charged. The existing codebase's `rollback_stock()` has exactly this flaw: a failed rollback call is silently ignored.

**Warning signs:**
- Rollback functions that make network calls without retry logic
- Compensating actions that are not themselves idempotent
- No log or record of failed compensation attempts
- Tests only verify the happy path rollback, not rollback-of-rollback

**Prevention strategy:**
- Design compensating transactions to be idempotent (calling rollback twice must be safe)
- Persist SAGA state before executing each step — if a step fails, the persisted state enables re-execution of compensation on restart
- Use an outbox pattern: write compensation intent to Redis before making the network call; a recovery process retries any unacknowledged compensations
- Do not swallow compensation errors — escalate to a "stuck SAGA" state that requires operator attention or an automated dead-letter retry queue
- The SAGA orchestrator must track which compensations completed; incomplete compensations should be retried on orchestrator restart

**Phase:** Phase 1 — SAGA orchestrator design must include compensation failure handling before any other SAGA work.

---

## P2 — Redis WATCH/MULTI/EXEC Limitations Under Concurrent Checkout

**What goes wrong:** The current code does get-then-set without any concurrency control. Two concurrent checkouts of the same order will both read the same state, both attempt deduction, and one will overwrite the other's write. WATCH/MULTI/EXEC addresses this but fails silently — it returns a null bulk reply when the watched key changed, and if the caller doesn't check the return value, it proceeds as if the transaction succeeded.

Additionally, WATCH is connection-scoped in redis-py. Under async Python (Quart + async Redis), if the connection is reused across coroutines before EXEC runs, the WATCH context leaks across coroutines. This causes false watch failures or, worse, no watch at all if the pool returns a different connection for EXEC than for WATCH.

**Warning signs:**
- Using `redis.watch()` with a connection pool without pinning the connection for the full WATCH-MULTI-EXEC sequence
- Not checking the return value of `pipeline.execute()`
- Concurrent checkout tests that pass in isolation but fail under locust load

**Prevention strategy:**
- Use a single pipelined connection for the entire WATCH-MULTI-EXEC block; in redis-py async, use `client.pipeline()` within a `async with client.monitor()` context or use Lua scripts instead
- Prefer Lua scripts over WATCH/MULTI/EXEC for atomic operations on a single Redis node or within a single hash slot in Redis Cluster — Lua scripts are atomic on the server side and eliminate the optimistic concurrency retry loop
- Always check EXEC return value: `None` means the watch fired and the transaction was aborted; implement retry with backoff
- For the specific case of stock subtraction, a Lua script that checks-and-decrements atomically is simpler and safer than WATCH/MULTI/EXEC

**Phase:** Phase 1 — before any concurrent load testing.

---

## P3 — Redis Cluster Cross-Slot Lua Script Failures

**What goes wrong:** In Redis Cluster, Lua scripts (and MULTI/EXEC transactions) are only atomic within a single hash slot. If a Lua script references keys from different slots, Redis Cluster returns `CROSSSLOT Keys in request don't hash to the same slot`. This will silently break during cluster migration if keys that happened to land on the same slot now don't.

For this system: a checkout touches order key (order_{id}), stock key (item_{id}), and payment key (user_{id}). These keys will almost certainly be on different slots in a 3-node cluster, making any cross-entity Lua script impossible.

**Warning signs:**
- Lua scripts that accept more than one key argument
- MULTI/EXEC pipelines that mix keys from different domains (order, stock, payment)
- Works in single-node Redis but fails with `CROSSSLOT` error when cluster is enabled
- Cluster topology changes cause previously working scripts to break

**Prevention strategy:**
- Design Lua scripts to operate on keys within a single service's domain only — never write a Lua script that touches both an order key and a stock key in the same script
- Use Redis hash tags `{user_id}` to force related keys onto the same slot when you need atomic cross-key operations within one service (e.g., a user's balance and a lock key for that user)
- Cross-service consistency must be handled at the SAGA level, not at the Redis transaction level — this is the fundamental reason SAGA is the right pattern here
- Test all Lua scripts against a Redis Cluster (not standalone Redis) from day one

**Phase:** Phase 1 — Redis Cluster configuration must be verified before any Lua scripts are written.

---

## P4 — Kubernetes Pod Kill During SAGA Mid-Execution

**What goes wrong:** The benchmark evaluation explicitly kills containers mid-transaction. If the SAGA orchestrator dies after deducting stock but before issuing the payment command, the system must recover. Without persisted SAGA state, recovery is impossible — the orchestrator restarts with no memory of the in-flight transaction.

The subtler failure: the orchestrator persists the SAGA step as "stock deducted" and then dies before actually calling the stock service. On recovery, it re-executes the stock deduction, double-deducting inventory.

**Warning signs:**
- SAGA state stored only in memory (Python dict or class instance)
- Steps marked as "completed" before the actual service call succeeds
- No distinction between "command sent" and "effect confirmed"
- Recovery logic that assumes any persisted step already succeeded

**Prevention strategy:**
- Persist SAGA state in Redis before each step, using a state machine: `PENDING → STEP_N_EXECUTING → STEP_N_COMPLETE → ...`
- Use at-least-once semantics with idempotency keys: each SAGA step carries a unique idempotency key so re-execution of a step is safe
- Mark a step complete only after receiving a confirmed success response from the target service
- On orchestrator restart, scan Redis for SAGAs in `EXECUTING` state and re-execute from the last confirmed step
- For the stock/payment services, implement idempotent endpoints that accept an idempotency key — if the same key is presented twice, return the original result without re-applying

**Phase:** Phase 1 — SAGA state persistence must be implemented before the fault tolerance requirement can be met.

---

## P5 — gRPC Event Loop Blocking in Async Python

**What goes wrong:** Python's grpcio library uses a background thread pool and is not natively async. When used with Quart (asyncio-based), calling grpcio stubs directly in an async handler blocks the event loop if the call is made synchronously, or requires careful use of `asyncio.get_event_loop().run_in_executor()`. The grpcio-aio (grpc.aio) package provides native async support but has different client lifecycle management — creating a new channel per request causes a massive connection overhead.

**Warning signs:**
- Using `grpcio` stubs with `await` — this will not work as expected without grpc.aio
- Creating a new `grpc.aio.insecure_channel()` per request
- Seeing event loop blocking under load (high p99 latency even with low concurrency)
- gRPC connection storm on service restart when all Quart workers reconnect simultaneously

**Prevention strategy:**
- Use `grpcio-aio` (grpc.aio) — not plain grpcio — for all async Python gRPC code
- Create one gRPC channel per target service at application startup, shared across all requests (channels are thread/coroutine safe)
- Configure `keepalive_time_ms` and `keepalive_timeout_ms` on the channel to detect dead connections before they surface as request failures
- Add a connection health check on startup; fail fast rather than accepting requests with broken upstream connections
- Set `max_receive_message_length` and `max_send_message_length` explicitly — the default (4MB) may be too small or too large depending on payload size

**Phase:** Phase 1 — gRPC client setup must be done correctly from the start; retrofitting channel lifecycle management is error-prone.

---

## P6 — Message Queue Exactly-Once Delivery Illusion

**What goes wrong:** Neither Kafka nor Redis Streams provides true exactly-once delivery to the consumer. Kafka provides exactly-once within a producer-to-topic transaction, but consuming and processing is at-least-once unless the consumer uses transactions to atomically commit the offset with the processing side effect. Redis Streams `XACK` is not atomic with the processing action — if the consumer crashes after processing but before `XACK`, the message is redelivered.

For this checkout system, if a "stock deducted" event is processed twice, stock is deducted twice. If a "payment completed" event is processed twice, the user is charged twice.

**Warning signs:**
- Message handlers that apply state changes before acknowledging
- No idempotency key on events
- Consumer groups with `NOACK` or automatic acknowledgment
- Handlers that don't check "has this event already been applied?"

**Prevention strategy:**
- Treat all message consumption as at-least-once; make every handler idempotent
- Store a `processed_event_ids` set in Redis with TTL; before processing any event, check if the event ID has already been applied
- For Redis Streams: use `XREADGROUP` with explicit `XACK` only after confirmed processing; use `XPENDING` to detect and retry or escalate stalled messages
- For Kafka: use consumer-side idempotency rather than Kafka transactions — simpler and more portable
- The SAGA orchestrator should be the only component emitting state-changing commands; downstream services execute commands and emit events (event sourcing style), not trigger new commands

**Phase:** Phase 1 — message queue integration requires idempotency design upfront.

---

## P7 — Race Condition: Concurrent Checkout of Same Order

**What goes wrong:** The benchmark sends concurrent requests. If two clients simultaneously call `POST /orders/checkout/{order_id}`, both read the same order (paid=False), both proceed through stock deduction and payment, and both mark the order as paid. The result: double stock deduction, double payment, one inconsistent order. This is the most likely consistency failure the benchmark checks for.

**Warning signs:**
- No distributed lock on the order entity during checkout
- Order `paid` flag checked then set non-atomically (get order → check paid → ... → set paid)
- Checkout tests only run one checkout per order at a time

**Prevention strategy:**
- Acquire a distributed lock (Redis SETNX with TTL) on the order_id at the start of checkout; any concurrent checkout attempt on the same order returns 409 immediately
- Use a Lua script to atomically check `paid=False` and set `paid=True` in one operation — this eliminates the TOCTOU window
- The lock TTL must exceed the maximum expected checkout duration (gRPC calls to stock + payment); set it conservatively (e.g., 30s) with a mechanism to extend if needed
- Release the lock explicitly on success or failure; the TTL is the safety net for crashes, not the primary release mechanism
- Mark order as `CHECKOUT_IN_PROGRESS` as a state rather than relying solely on `paid` boolean — this enables better crash recovery and concurrent rejection

**Phase:** Phase 1 — must be addressed before any concurrency testing.

---

## P8 — Redis Cluster Slot Migration During Live Traffic

**What goes wrong:** When Redis Cluster is rebalancing (adding/removing nodes), keys migrate between slots. During migration, a key may temporarily exist on both source and destination nodes. A read against the source gets the old value; a write against the destination gets the new slot. If the Python redis client doesn't handle `MOVED` and `ASK` redirections correctly, requests fail. redis-py's cluster client handles this automatically — but only if it's configured as a cluster client (`RedisCluster`), not a regular `Redis` client pointed at one cluster node.

**Warning signs:**
- Using `redis.Redis(host=one_node)` against a cluster deployment instead of `redis.RedisCluster(startup_nodes=[...])`
- Getting `MOVED` errors in logs
- Requests failing during cluster scaling operations (adding nodes for HPA)
- Lua scripts working on single node but failing after cluster setup

**Prevention strategy:**
- Use `redis.cluster.RedisCluster` from redis-py — it handles MOVED/ASK redirections, retries, and cluster topology refresh automatically
- Configure `retry_on_error` and `retry` on the cluster client for transient slot migration errors
- Test cluster resharding under live traffic in staging before the benchmark
- Avoid storing related data under keys that might end up on different slots unless you use hash tags to co-locate them

**Phase:** Phase 1 — Redis Cluster client must be configured correctly before any cluster deployment.

---

## P9 — gRPC Connection Pool Exhaustion Under K8s HPA Scale-Up

**What goes wrong:** When Kubernetes HPA adds new pods during a load spike, each new pod opens gRPC connections to each other service. If stock-service scales from 2 to 10 pods, order-service now has 10× the gRPC connections. Meanwhile, the old pods' connections are still established. During the transition, gRPC keepalive timers may not have fired yet, and the order-service channel may still send requests to pods that have been terminated, receiving connection reset errors.

**Warning signs:**
- gRPC `UNAVAILABLE` or `DEADLINE_EXCEEDED` errors during scale-up events
- No gRPC server-side `GOAWAY` handling in client
- Fixed gRPC channel with no connection refresh
- K8s readiness probe not gating traffic until gRPC server is fully initialized

**Prevention strategy:**
- Implement K8s readiness probes that verify the gRPC server is accepting connections before routing traffic — use gRPC health checking protocol (`grpc.health.v1`)
- Configure client-side retry policy in gRPC service config JSON: retry on `UNAVAILABLE` with max 3 attempts and exponential backoff
- Set `grpc.max_connection_age_ms` and `grpc.max_connection_age_grace_ms` on the server to periodically cycle connections, forcing clients to reconnect and re-discover the current topology
- Implement a circuit breaker around gRPC calls — after N consecutive failures to a given address, stop sending and wait for health recovery

**Phase:** Phase 1 (gRPC setup) and Phase 2 (K8s HPA scaling).

---

## P10 — Benchmark CPU Limit: Blocking I/O Destroying Throughput

**What goes wrong:** The benchmark runs against 20 CPUs max. With Quart+Uvicorn (async), a single blocking call anywhere in the request path serializes the entire event loop. Common culprits: synchronous Redis calls (the existing codebase uses synchronous `redis.Redis`), msgpack encode/decode if done naively, or grpcio stubs without grpc.aio. Under the 20 CPU constraint, if 4 CPUs are saturated by blocked event loops, effective capacity drops to 16.

**Warning signs:**
- Using `redis.Redis` (synchronous) with Quart instead of `redis.asyncio.Redis`
- Any `time.sleep()` in request handlers
- Profiling shows a single Uvicorn worker at 100% CPU while others idle
- p50 latency acceptable but p99 is 10× higher (event loop jitter from blocking calls)

**Prevention strategy:**
- Use `redis.asyncio.Redis` or `redis.asyncio.cluster.RedisCluster` — the async variants are drop-in replacements with the same API but non-blocking
- Use `grpc.aio` for all gRPC calls — never `run_in_executor` for gRPC unless using plain grpcio
- CPU-bound work (msgpack serialization of large payloads) should be offloaded to a thread pool with `asyncio.to_thread()` if it exceeds ~1ms
- Profile with `asyncio` debug mode enabled during load testing to detect event loop blocking (logs warnings for coroutines that block for >100ms)
- Set Uvicorn workers to `(2 × CPUs) + 1` within the 20 CPU budget; over-provisioning workers with blocking code will degrade, not improve, performance

**Phase:** Phase 1 — async migration must be complete before any performance testing.

---

## P11 — Container Kill Recovery: In-Flight gRPC Calls

**What goes wrong:** The evaluator kills one container while transactions are in progress. If the stock-service pod is killed while an order-service is waiting on a gRPC `subtract_stock` call, the gRPC call returns `UNAVAILABLE`. The order-service must decide: was the stock actually deducted before the kill? The answer is unknown. If the order-service compensates (assumes deducted, issues rollback), but the stock was not deducted, it now adds stock that was never removed — inconsistent in the other direction.

**Warning signs:**
- No idempotency keys on stock/payment operations
- SAGA step marked as "succeeded" only on explicit 200 response, with no handling for "unknown" state
- Rollback triggered on any gRPC error, including timeouts where the operation may have completed

**Prevention strategy:**
- Every state-changing gRPC call must carry an idempotency key (SAGA step ID + operation ID)
- Target services must store idempotency keys with their result and return the cached result for duplicate calls
- The SAGA orchestrator must distinguish three outcomes: `SUCCESS`, `FAILURE`, `UNKNOWN` (timeout/crash)
- On `UNKNOWN`, the orchestrator retries with the same idempotency key — if the operation completed, the idempotency key returns the original result; if not, it executes
- Never trigger compensation on `UNKNOWN` — retry until you get a definitive answer or exhaust a retry budget, then escalate to `STUCK_SAGA` state requiring manual or automated intervention
- This is the only correct approach — "assume failed and compensate" creates inconsistency in the opposite direction

**Phase:** Phase 1 — fundamental to the fault tolerance requirement.

---

## P12 — SAGA Orchestrator as Single Point of Failure

**What goes wrong:** A SAGA orchestrator coordinating all transactions becomes a SPOF. If it crashes, all in-flight SAGAs are lost (without persistence). If it is the only process that knows how to compensate, recovery is impossible. Worse: if two orchestrator replicas run simultaneously (K8s restarts old pod before new one is ready), they may both attempt to drive the same SAGA, issuing duplicate commands.

**Warning signs:**
- Orchestrator stores SAGA state only in memory
- No leader election or SAGA ownership mechanism
- K8s deployment allows `maxSurge > 0` without SAGA deduplication logic
- No mechanism to detect that another orchestrator instance is already handling a SAGA

**Prevention strategy:**
- Store all SAGA state in Redis with the SAGA ID as the key; the orchestrator is stateless between steps
- Use Redis SETNX on a SAGA lock key to claim exclusive ownership of a SAGA; only the owner may drive it forward
- Set lock TTL to heartbeat interval × N; orchestrator refreshes the lock while working; if it crashes, another instance picks up after TTL expiry
- For K8s deployment, use `RollingUpdate` with `maxSurge=0` for the orchestrator, or implement SAGA ownership in Redis so two instances can coexist safely
- The orchestrator's recovery loop on startup reads all non-terminal SAGAs from Redis and resumes them

**Phase:** Phase 1 — orchestrator design must account for this from the start.

---

## P13 — msgpack Schema Evolution Breaking Existing Data

**What goes wrong:** Migrating from Flask to Quart, or adding SAGA state fields to OrderValue, PayloadValue, etc., requires schema changes. msgspec's `Struct` enforces strict schema at deserialization time — an `OrderValue` serialized with the old schema will fail to deserialize with a new `Struct` definition if fields were added, removed, or reordered. This is the existing codebase's "Fragile Area" for serialization (noted in CONCERNS.md).

**Warning signs:**
- Adding or removing fields from `OrderValue`, `UserValue`, or `StockValue` Struct definitions without migration plan
- Deployment that starts new pods before draining traffic from old pods (mixed schema versions)
- No test that deserializes data written by the previous schema version

**Prevention strategy:**
- Add fields with default values — msgspec supports `Optional` and default-value fields; existing data without the field will use the default on deserialization
- Never remove fields; instead deprecate them (set to Optional with default None) and migrate in a separate step
- For major schema changes, write a migration script that reads all keys, transforms, and rewrites before deploying new code
- Use a schema version field in the Struct to enable forward-compatible deserialization logic
- Test deserialization of old-format data in CI before each deployment

**Phase:** Phase 1 — schema changes happen during migration from Flask to Quart; handle before deployment.

---

## P14 — Redis Cluster + Connection Failover Causing Checkout Loops

**What goes wrong:** When a Redis Cluster primary fails over to a replica, redis-py's cluster client will retry with exponential backoff and discover the new topology. However, during the failover window (typically 10–30 seconds), operations return `ClusterDownError` or `ConnectionError`. If the SAGA orchestrator retries blindly during this window without tracking that the step was submitted, it may submit the same idempotent operation multiple times, exhausting the retry budget and marking the SAGA as failed — even though the Redis failover was transient.

**Warning signs:**
- SAGA timeout budget shorter than Redis failover window
- No distinction between transient infrastructure errors and definitive business logic failures
- Cluster failover in staging causes many SAGAs to enter failed/compensation state

**Prevention strategy:**
- Set SAGA step timeout to be longer than the Redis Cluster failover window (allow 60s minimum for Redis failover + retries)
- Classify errors: `ClusterDownError`, `ConnectionError`, `TimeoutError` are retryable infrastructure errors; business logic rejections (insufficient stock, insufficient credit) are not retryable and trigger compensation immediately
- The SAGA step retry loop must not run in the event loop itself — use a dedicated coroutine with asyncio.sleep between retries to avoid blocking other requests
- Test Redis Cluster failover under load explicitly before the benchmark

**Phase:** Phase 1 — must be considered during Redis Cluster and SAGA timeout configuration.

---

## P15 — Benchmark-Specific: 20 CPU Budget Misallocation

**What goes wrong:** With 20 CPUs total, naive allocation (e.g., 4 CPUs per service × 3 services + 4 for Redis Cluster + 4 for orchestrator) may leave insufficient headroom. The benchmark runs locust load generators externally, so no CPU is spent there. However, Redis Cluster itself is CPU-intensive under high throughput. Allocating too little CPU to Redis causes client-side timeouts even when services have idle CPUs. Conversely, the Python GIL means a single Uvicorn process is limited to ~1 CPU effectively; without multiple workers, CPUs are wasted.

**Warning signs:**
- Redis Cluster CPU at 100% while service CPUs at 30%
- Uvicorn running with 1 worker (single process, GIL-bound)
- Total CPU requests in K8s manifests exceed 20 (will cause pod scheduling failures)
- CPUs allocated to services not tuned against benchmark profiling data

**Prevention strategy:**
- Profile CPU usage under benchmark-representative load before finalizing K8s resource allocations
- Run Uvicorn with `--workers` set to at least 2 per service (each worker is a separate process, bypassing the GIL for concurrency)
- Allocate dedicated CPU for Redis Cluster nodes — at minimum 1 CPU per primary node; Redis is single-threaded per shard but benefits from dedicated cores
- Leave 10–15% CPU headroom for K8s system pods, kubelet, and metric collection
- Use K8s resource `requests` (for scheduling) and `limits` (for enforcement) separately — set `requests` accurately and `limits` slightly higher to allow burst

**Phase:** Phase 2 (Kubernetes scaling), but CPU budget planning must inform Phase 1 architecture decisions.

---

## Summary Table

| # | Pitfall | Severity | Phase |
|---|---------|----------|-------|
| P1 | Compensation transactions that also fail | Critical | Phase 1 |
| P2 | WATCH/MULTI/EXEC misuse in async Python | Critical | Phase 1 |
| P3 | Cross-slot Lua scripts in Redis Cluster | High | Phase 1 |
| P4 | Pod kill with no SAGA state persistence | Critical | Phase 1 |
| P5 | gRPC blocking event loop in Quart | High | Phase 1 |
| P6 | Message queue exactly-once illusion | High | Phase 1 |
| P7 | Concurrent checkout of same order (race) | Critical | Phase 1 |
| P8 | Wrong Redis client for cluster deployment | High | Phase 1 |
| P9 | gRPC connection exhaustion on HPA scale-up | Medium | Phase 1+2 |
| P10 | Blocking I/O under 20 CPU limit | High | Phase 1 |
| P11 | Unknown outcome on mid-flight pod kill | Critical | Phase 1 |
| P12 | SAGA orchestrator as single point of failure | High | Phase 1 |
| P13 | msgpack schema breaking on migration | Medium | Phase 1 |
| P14 | Redis Cluster failover causing SAGA timeouts | Medium | Phase 1 |
| P15 | 20 CPU budget misallocation | Medium | Phase 2 |

---

*Research: 2026-02-27*
