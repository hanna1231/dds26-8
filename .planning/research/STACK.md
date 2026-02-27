# Stack Research — Distributed Microservice Checkout System

**Research Date:** 2026-02-27
**Research Type:** Project Research — Stack dimension
**Milestone:** Subsequent — upgrading Flask+Redis to Quart+Uvicorn+gRPC+Redis Cluster

---

> **Version Verification Note:** Web search and PyPI API access were unavailable during this research session. Versions below are from training data (cutoff August 2025). Before pinning in requirements.txt, verify each version against `pip index versions <package>` or PyPI. Confidence in specific patch versions is lower than confidence in major.minor versions and architectural recommendations.

---

## Summary Recommendation

For a Python-only distributed checkout system on Kubernetes with consistency guarantees and a March 13 deadline, the optimal stack is:

- **Quart 0.20.x + Uvicorn 0.30.x** — async Flask-compatible migration path, minimal code rewrite
- **grpcio 1.65.x + grpcio-tools 1.65.x** — inter-service calls, lower latency than REST
- **redis-py 5.x async client** — already in use; enable async mode, add cluster support
- **Redis Streams** over Kafka — simpler ops, no new infrastructure, Redis already required
- **Redis Cluster** — 3-node minimum with AOF persistence for HA

---

## 1. Web Framework: Quart + Uvicorn

### Chosen: Quart 0.20.x

**Package:** `quart`
**Version:** `>=0.20.0,<0.21` (verify: `pip index versions quart`)
**Confidence:** High (architectural choice) / Medium (exact minor version)

**Why Quart over alternatives:**

| Option | Verdict | Reason |
|--------|---------|--------|
| Quart | **Use** | Drop-in async Flask replacement; Flask 3.x patterns translate directly; existing routes, blueprints, abort() calls migrate with minimal changes |
| FastAPI | Avoid | Pydantic-heavy; more suited to greenfield; migration from Flask is higher friction than Quart; adds complexity for a time-constrained project |
| Starlette (raw) | Avoid | Too low-level; no Flask compatibility; would require full rewrite |
| Flask + gevent/eventlet | Avoid | Fake async via monkey-patching; breaks under gRPC; not true async I/O |
| aiohttp | Avoid | Different API paradigm entirely; not Flask-compatible |

**Migration notes:** `@app.route` becomes `@app.route` (identical). `request`, `jsonify`, `abort()` all work unchanged. The key difference is route handlers become `async def` and Redis calls must be `await`ed.

### Chosen: Uvicorn 0.30.x

**Package:** `uvicorn[standard]`
**Version:** `>=0.30.0,<0.31` (verify: `pip index versions uvicorn`)
**Confidence:** High

**Why Uvicorn over Hypercorn:**

| Option | Verdict | Reason |
|--------|---------|--------|
| Uvicorn | **Use** | Best performance for ASGI; uvloop + httptools acceleration via `[standard]` extra; industry standard for Quart/FastAPI production |
| Hypercorn | Avoid | Quart's "official" server but benchmarks consistently lower throughput than Uvicorn; Quart works with either |
| Gunicorn (WSGI) | Remove | WSGI cannot serve ASGI; must be replaced entirely |

**Gunicorn in K8s:** Gunicorn can be used as a process manager wrapping Uvicorn workers (`gunicorn -k uvicorn.workers.UvicornWorker`), which is a valid production pattern. However for simplicity with K8s HPA and CPU-based autoscaling, running Uvicorn directly with `--workers` is recommended: `uvicorn app:app --workers 4 --host 0.0.0.0 --port 5000`.

**Worker count guidance:** Start at `2 * CPU_cores + 1`. With 1 CPU per pod (current K8s limit), use 3 workers. Adjust after benchmarking.

---

## 2. gRPC: grpcio + grpcio-tools

### Chosen: grpcio 1.65.x + grpcio-tools 1.65.x

**Packages:** `grpcio`, `grpcio-tools`
**Version:** `>=1.65.0,<2.0` (verify: `pip index versions grpcio`)
**Confidence:** High (library is stable, async support mature since 1.32)

**Why gRPC:**
- Binary Protocol Buffers serialization is ~5-10x smaller than JSON for the same payload
- HTTP/2 multiplexing means multiple in-flight RPCs over one connection vs. one request per connection (HTTP/1.1)
- Strongly typed `.proto` contracts eliminate the "what does this endpoint accept?" ambiguity between services
- Streaming support available for future SAGA event flows
- The alternative (httpx async REST) loses the type safety and stays at JSON overhead

**What NOT to use:**
- `grpc.aio` is the correct async module — do NOT use the legacy synchronous `grpc` stub in async handlers (blocks event loop)
- Do not use `grpclib` (third-party) — grpcio's native `grpc.aio` is now mature and better supported
- Do not mix sync and async stubs in the same service

### Proto File Structure for Order/Stock/Payment Services

Create a shared `proto/` directory at the repo root, compiled once:

```
proto/
  checkout.proto       # SAGA orchestration messages
  order_service.proto  # Order service RPCs
  stock_service.proto  # Stock service RPCs
  payment_service.proto # Payment service RPCs
```

**stock_service.proto** (example — most called service during checkout):
```protobuf
syntax = "proto3";

package checkout;

service StockService {
  rpc FindItem (FindItemRequest) returns (ItemResponse);
  rpc SubtractStock (AdjustStockRequest) returns (OperationResponse);
  rpc AddStock (AdjustStockRequest) returns (OperationResponse);
}

message FindItemRequest {
  string item_id = 1;
}

message ItemResponse {
  string item_id = 1;
  int64 stock = 2;
  int64 price = 3;
  bool found = 4;
}

message AdjustStockRequest {
  string item_id = 1;
  int64 quantity = 2;
  string idempotency_key = 3;  // Required for SAGA compensation safety
}

message OperationResponse {
  bool success = 1;
  string error_message = 2;
  string idempotency_key = 3;
}
```

**payment_service.proto**:
```protobuf
syntax = "proto3";

package checkout;

service PaymentService {
  rpc FindUser (FindUserRequest) returns (UserResponse);
  rpc Pay (PayRequest) returns (OperationResponse);
  rpc AddFunds (AddFundsRequest) returns (OperationResponse);
}

message FindUserRequest {
  string user_id = 1;
}

message UserResponse {
  string user_id = 1;
  int64 credit = 2;
  bool found = 3;
}

message PayRequest {
  string user_id = 1;
  int64 amount = 2;
  string idempotency_key = 3;
}

message AddFundsRequest {
  string user_id = 1;
  int64 amount = 2;
}

message OperationResponse {
  bool success = 1;
  string error_message = 2;
  string idempotency_key = 3;
}
```

**checkout.proto** (SAGA coordination — used by orchestrator):
```protobuf
syntax = "proto3";

package checkout;

service SagaOrchestrator {
  rpc StartCheckout (CheckoutRequest) returns (CheckoutResponse);
  rpc GetSagaStatus (SagaStatusRequest) returns (SagaStatusResponse);
}

message CheckoutRequest {
  string order_id = 1;
  string saga_id = 2;   // UUID, idempotency at orchestrator level
}

message CheckoutResponse {
  bool success = 1;
  string saga_id = 2;
  string error_message = 3;
}

message SagaStatusRequest {
  string saga_id = 1;
}

message SagaStatusResponse {
  string saga_id = 1;
  string state = 2;  // PENDING, PROCESSING, COMPLETED, COMPENSATING, FAILED
  bool is_terminal = 3;
}
```

**idempotency_key** field is critical in all mutation RPCs. Without it, a compensating transaction that retries cannot distinguish "already compensated" from "not yet compensated," leading to double-compensation bugs.

**Code generation:**
```bash
python -m grpc_tools.protoc \
  -I./proto \
  --python_out=./generated \
  --grpc_python_out=./generated \
  proto/*.proto
```

Run this in Dockerfile build step or as a Makefile target. Generated files should be committed to the repo to avoid build-time protoc dependency in containers.

**Async server setup pattern (per service):**
```python
import grpc
from grpc import aio as grpc_aio

async def serve():
    server = grpc_aio.server()
    stock_service_pb2_grpc.add_StockServiceServicer_to_server(StockServicer(), server)
    server.add_insecure_port('[::]:50051')
    await server.start()
    await server.wait_for_termination()
```

Each service runs both the Quart HTTP server (for external API, port 5000) and gRPC server (for inter-service, port 50051) in the same process using `asyncio.gather()`.

---

## 3. Redis: Async Client + Cluster

### Chosen: redis-py 5.x async client

**Package:** `redis`
**Version:** `>=5.0.0,<6.0` (already installed at 5.0.3; verify: `pip index versions redis`)
**Confidence:** High — already in use; async mode is built-in since 4.2

**Key change from current usage:** The existing code uses `redis.Redis` (synchronous). Switch to `redis.asyncio.Redis` (or `redis.asyncio.RedisCluster` for cluster). This is the same package, different import path — no version change needed.

```python
# Current (synchronous — blocks event loop in Quart):
import redis
db = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)

# Target (async — correct for Quart):
import redis.asyncio as aioredis
db = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)

# For Redis Cluster:
db = aioredis.RedisCluster(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=False,  # Keep binary for msgspec
    skip_full_coverage_check=True,  # Safe for non-production clusters
)
```

**What NOT to use:**
- `aioredis` (standalone package) — deprecated and merged into redis-py 4.2+; do not install separately
- Synchronous `redis.Redis` inside async handlers — will block the event loop, defeating Quart's async benefit
- `redis.StrictRedis` — legacy alias, use `redis.asyncio.Redis` directly

**Connection pool configuration:**
```python
pool = aioredis.ConnectionPool.from_url(
    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
    max_connections=50,  # Per worker; tune based on load test results
    decode_responses=False,
)
db = aioredis.Redis(connection_pool=pool)
```

### Redis Cluster Configuration

**Minimum cluster topology:** 3 primary nodes, 3 replica nodes (1 replica per primary). Redis Cluster requires a minimum of 3 primaries to function.

**For the benchmark (20 CPU constraint):** Each Redis Cluster node is a separate container. Keep cluster nodes at 3 primaries + 3 replicas = 6 Redis containers. With the 3 app services + nginx + SAGA orchestrator + 6 Redis = 10 containers minimum.

**Recommended cluster layout:**

```yaml
# One cluster per service domain (Order, Stock, Payment each get their own cluster)
# Each cluster: 3 primaries + 3 replicas

order-redis-0: primary, 6379
order-redis-1: primary, 6380
order-redis-2: primary, 6381
order-redis-3: replica of 0
order-redis-4: replica of 1
order-redis-5: replica of 2
```

**Alternative (simpler for time constraints):** Single Redis Cluster shared across services, with keyspace prefixes to isolate data. Simpler to operate but couples service data. Given the course's consistency focus, per-service clusters are architecturally cleaner.

**Redis Cluster config (redis.conf per node):**
```conf
cluster-enabled yes
cluster-config-file nodes.conf
cluster-node-timeout 5000
appendonly yes                    # AOF persistence — required for crash recovery
appendfsync everysec              # Balance between durability and performance
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
maxmemory-policy noeviction       # Never evict; crash rather than lose data silently
```

**Critical:** `maxmemory-policy noeviction` is non-negotiable for a financial system. Default `allkeys-lru` would silently delete order/payment records under memory pressure.

**SAGA state persistence:** SAGA state (what step each transaction is at) must also persist in Redis with AOF. Use a dedicated key namespace: `saga:{saga_id}:state`. If the orchestrator crashes, on restart it reads all `saga:*:state` keys, finds incomplete SAGAs, and resumes compensation.

---

## 4. Message Queue: Kafka vs Redis Streams

### Decision: Redis Streams

**Confidence:** High for this specific use case

### Comparison Matrix

| Dimension | Kafka | Redis Streams |
|-----------|-------|---------------|
| Infrastructure overhead | High — needs ZooKeeper/KRaft + brokers + topics + consumer groups | None — Redis already required; Streams is a Redis data type |
| Ops complexity | High — separate cluster, JVM tuning, partition management | Low — same Redis you already operate |
| CPU/Memory budget | High — Kafka broker alone: 2-4 GB RAM, 1-2 CPUs | None additional — Streams data lives in existing Redis |
| Throughput ceiling | Millions of msg/sec | ~100k-1M msg/sec (sufficient for checkout workload) |
| Message ordering | Partition-level ordering | Per-stream ordering (global per stream) |
| Consumer groups | Yes | Yes (since Redis 5.0) — identical semantic model |
| Exactly-once delivery | With transactions + idempotent producers | At-least-once; idempotency must be in application |
| Schema registry | Confluent Schema Registry (separate service) | Not built-in; use msgspec/protobuf at application layer |
| Persistence | Highly durable (replicated log) | Redis persistence (AOF) provides durability |
| Learning curve | High | Low — Redis XADD/XREAD/XACK are simple |
| Python client maturity | aiokafka (async), confluent-kafka (sync) | redis-py 5.x built-in — already installed |
| Time to integrate | 3-5 days (new infra + learning) | 0.5-1 day (same client, new API calls) |
| Relevance to grade | Event-driven architecture scores same regardless of broker | Same architectural category |

### Why Redis Streams wins for this project:

1. **Zero new infrastructure.** The benchmark runs against 20 CPUs. Every CPU used by Kafka is a CPU not available for checkout throughput. Redis Streams uses CPUs already allocated to Redis.

2. **Already have the client.** redis-py 5.x includes full Streams support (`XADD`, `XREAD`, `XREADGROUP`, `XACK`, `XPENDING`). No new dependency, no version conflicts.

3. **Consumer groups exist.** Redis Streams consumer groups give exactly the same at-least-once delivery semantics as Kafka consumer groups. The programming model is nearly identical.

4. **Deadline pressure.** March 13 is 2 weeks away. Kafka would consume 3-5 days of setup and debugging. Redis Streams can be integrated in under a day for someone already familiar with Redis.

5. **SAGA state is already in Redis.** Having the event stream co-located with the SAGA state allows atomic `MULTI/EXEC` operations like "append event AND update SAGA state" — impossible across Redis + Kafka.

### When Kafka would win instead:
- If the project required event replay across months of history
- If multiple external consumers needed to subscribe (analytics, audit, external systems)
- If throughput exceeded 500k events/second
- If the team had existing Kafka expertise

None of these apply here.

### Redis Streams implementation pattern for SAGA:

```python
# Publish SAGA event (from orchestrator):
await db.xadd(
    "saga:events",
    {
        "saga_id": saga_id,
        "event_type": "STOCK_SUBTRACTED",
        "order_id": order_id,
        "item_id": item_id,
        "quantity": str(quantity),
        "timestamp": str(time.time()),
    },
    maxlen=10000,  # Cap stream length; older events are trimmed
)

# Consume events (in SAGA orchestrator):
messages = await db.xreadgroup(
    groupname="saga-orchestrator",
    consumername="orchestrator-1",
    streams={"saga:events": ">"},  # ">" means new messages only
    count=10,
    block=1000,  # Block 1 second if no messages
)

for stream, events in messages:
    for event_id, fields in events:
        await process_event(fields)
        await db.xack("saga:events", "saga-orchestrator", event_id)
```

**If Kafka is required** (e.g., instructor mandates it): Use `aiokafka>=0.11.0` (async) over `confluent-kafka` (synchronous C extension, harder to use with asyncio). Verify: `pip index versions aiokafka`.

---

## 5. Supporting Libraries

### Keep: msgspec 0.18.x

**Package:** `msgspec`
**Version:** `>=0.18.6` (already installed)
**Confidence:** High

Keep msgspec for Redis serialization. It is faster than pydantic for encode/decode and already in use. The Struct definitions (`OrderValue`, `UserValue`, `StockValue`) require no changes for the async migration.

### Add: structlog (optional but recommended)

**Package:** `structlog`
**Version:** `>=24.0.0`
**Confidence:** Medium

Structured JSON logging is critical for debugging distributed SAGA failures across services. `structlog` integrates with Quart and outputs machine-parseable logs. Without it, debugging "payment crashed mid-SAGA" means parsing unstructured log strings across 6 containers.

```python
import structlog
log = structlog.get_logger()

await log.ainfo("saga_step_complete",
    saga_id=saga_id,
    step="payment",
    order_id=order_id,
    duration_ms=elapsed,
)
```

### Add: tenacity (retry logic)

**Package:** `tenacity`
**Version:** `>=8.0.0`
**Confidence:** High

The fault tolerance requirement ("system recovers when any single container dies mid-transaction") requires retry logic with exponential backoff on gRPC calls. Hand-rolling retry decorators is error-prone. Tenacity integrates cleanly with async code:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.1, max=2),
    retry=retry_if_exception_type(grpc.aio.AioRpcError),
)
async def subtract_stock_with_retry(stub, request):
    return await stub.SubtractStock(request)
```

### Remove: requests 2.31.0

**Package:** `requests`
**Version:** Remove entirely
**Reason:** Synchronous HTTP library. Once inter-service calls move to gRPC, `requests` has no role. Keeping it risks accidental synchronous calls blocking the event loop in Quart handlers.

---

## 6. Complete requirements.txt for Target Stack

```
# Web framework (async)
quart>=0.20.0,<0.21
uvicorn[standard]>=0.30.0,<0.31

# gRPC
grpcio>=1.65.0,<2.0
grpcio-tools>=1.65.0,<2.0

# Database
redis[hiredis]>=5.0.3,<6.0  # hiredis extra for faster parsing

# Serialization (keep existing)
msgspec>=0.18.6,<0.19

# Fault tolerance
tenacity>=8.0.0,<9.0

# Structured logging (recommended)
structlog>=24.0.0,<25.0
```

**Note:** `redis[hiredis]` adds the `hiredis` C extension for faster Redis protocol parsing. Safe to add since redis-py falls back to pure Python if hiredis is unavailable. Measurably improves throughput under load.

---

## 7. What NOT to Use (Explicit Exclusions)

| Package | Why Not |
|---------|---------|
| `FastAPI` | Pydantic overhead, greenfield API, higher migration cost vs. Quart for Flask codebase |
| `asyncpg` / `sqlalchemy` | Not Redis; course requires Redis |
| `aioredis` (standalone) | Deprecated; merged into redis-py 4.2+; installing it creates import conflicts |
| `confluent-kafka` | Synchronous C extension; poor asyncio integration; requires Kafka infra |
| `kafka-python` | Unmaintained; use aiokafka if Kafka is required |
| `celery` | WSGI-first task queue; not designed for SAGA patterns; adds broker complexity |
| `dramatiq` | Same concerns as celery; not appropriate for distributed transaction coordination |
| `gevent` / `eventlet` | Monkey-patching breaks gRPC; incompatible with true async |
| `hypercorn` | Lower throughput than Uvicorn in benchmarks for Quart |
| `grpclib` | Third-party gRPC; grpcio native async (`grpc.aio`) is mature and better supported |
| `httpx` | Useful async HTTP client but redundant once gRPC handles inter-service calls |
| `requests` | Synchronous; blocks Quart event loop; must be removed |

---

## 8. Confidence Summary

| Decision | Confidence | Primary Risk |
|----------|------------|--------------|
| Quart over FastAPI | High | Quart is less popular; fewer StackOverflow answers for edge cases |
| Uvicorn over Hypercorn | High | Minimal — both work; Uvicorn is industry default |
| grpcio for inter-service | High | Proto compilation step adds build complexity |
| redis-py async (same package) | High | None — already installed, just change import |
| Redis Streams over Kafka | High | Instructor might expect Kafka for "event-driven" architecture points; clarify with TA |
| Redis Cluster (6 nodes) | Medium | CPU budget constraint; verify 6 Redis containers fit within 20 CPU budget alongside app services |
| Specific library versions | Medium | Training data cutoff August 2025; patch versions may have changed; verify before pinning |
| idempotency_key in protos | High | Omitting this leads to double-compensation bugs that are hard to reproduce |

---

## 9. Migration Sequencing Recommendation

Given March 13 deadline (approximately 2 weeks):

**Week 1 (Days 1-5): Foundation**
1. Add grpcio + generate protos — test gRPC stubs work between services
2. Migrate Flask → Quart + Uvicorn — validate all existing routes still respond
3. Switch redis.Redis → redis.asyncio.Redis — validate reads/writes work async

**Week 2 (Days 6-10): SAGA + Fault Tolerance**
4. Implement SAGA orchestrator with Redis Streams events
5. Add Redis Cluster (can use single-node cluster mode locally for testing)
6. Add tenacity retry on gRPC calls
7. Add idempotency keys to all mutation operations
8. Run benchmark + fault injection tests (kill one container, verify recovery)

Do not attempt Redis Cluster and SAGA simultaneously — debug one layer at a time.

---

*Research complete: 2026-02-27*
*Versions require verification against PyPI before pinning (web search unavailable during this session)*
