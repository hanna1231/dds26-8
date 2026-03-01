---
phase: 06-infrastructure
plan: 01
subsystem: redis-cluster-client
tags: [redis-cluster, hash-tags, infrastructure, migration]
dependency_graph:
  requires: []
  provides: [redis-cluster-client, hash-tagged-keys, health-endpoints]
  affects: [stock-service, payment-service, orchestrator-service, order-service]
tech_stack:
  added: [redis.asyncio.cluster.RedisCluster, redis.asyncio.cluster.ClusterNode]
  patterns: [hash-tag-slot-colocaton, cluster-startup-nodes, await-db-initialize]
key_files:
  created: []
  modified:
    - order/app.py
    - stock/app.py
    - payment/app.py
    - orchestrator/app.py
    - stock/grpc_server.py
    - payment/grpc_server.py
    - orchestrator/saga.py
    - orchestrator/grpc_server.py
    - orchestrator/events.py
    - orchestrator/recovery.py
    - env/order_redis.env
    - env/stock_redis.env
    - env/payment_redis.env
    - env/orchestrator_redis.env
    - tests/conftest.py
    - tests/test_saga.py
    - tests/test_fault_tolerance.py
decisions:
  - RedisCluster startup_nodes uses REDIS_NODE_HOST env var with default port 6379; REDIS_NODE_PORT optional override
  - decode_responses=False preserved on all clients (existing byte-handling code unchanged)
  - Hash tag pattern {item:<id>} for stock, {user:<id>} for payment, {saga:<id>} for orchestrator
  - Stream names use shared {saga:events} hash tag so checkout and dead-letter streams co-locate on same slot
  - Recovery scanner SCAN pattern changed to {saga:* to match new hash-tagged key format
  - Test fixtures (seed_item, seed_user, seed_saga, get_item_stock, get_user_credit) updated to use hash-tagged keys
  - Idempotency key format in grpc_server now prefixed with entity hash tag (e.g., {item:<id>}:idempotency:<key>)
metrics:
  duration: 675s
  completed: "2026-03-01"
  tasks_completed: 2
  files_modified: 17
---

# Phase 06 Plan 01: Redis Cluster Client Migration Summary

**One-liner:** Redis Cluster client migration using RedisCluster with REDIS_NODE_HOST startup_nodes and hash-tagged keys ({item:}, {user:}, {saga:}) for guaranteed slot co-location.

## What Was Built

Migrated all four microservices (order, stock, payment, orchestrator) from standalone `redis.asyncio.Redis` to `redis.asyncio.cluster.RedisCluster`, and added hash tag prefixes to all Redis keys to ensure Lua scripts operate within a single hash slot (required by Redis Cluster).

### Task 1: RedisCluster Client Migration

All four services now initialize with:

```python
from redis.asyncio.cluster import RedisCluster, ClusterNode

db = RedisCluster(
    startup_nodes=[ClusterNode(node_host, node_port)],
    password=os.environ['REDIS_PASSWORD'],
    decode_responses=False,
    require_full_coverage=True,
)
await db.initialize()
```

Key changes:
- Removed `db=int(os.environ['REDIS_DB'])` parameter (Redis Cluster only supports db=0)
- Added `await db.initialize()` in every `before_serving` hook
- Updated env files: removed `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`; added `REDIS_NODE_HOST`
- Added `GET /health` endpoints to order, stock, payment (all return 503 on Redis ping failure)
- Updated orchestrator `/health` to fail fast with 503 if `db.ping()` fails, then proceed with lag/dead-letter info

### Task 2: Hash-Tagged Redis Keys

| Service | Pattern | Keys affected |
|---------|---------|---------------|
| Stock | `{item:<item_id>}` | data keys + idempotency keys |
| Payment | `{user:<user_id>}` | data keys + idempotency keys |
| Orchestrator | `{saga:<order_id>}` | saga hash, step idempotency keys, publish_event saga_id |
| Orchestrator streams | `{saga:events}` | STREAM_NAME, DEAD_LETTERS_STREAM |

Idempotency key format:
- Stock: `{item:<item_id>}:idempotency:<key>` — guarantees same slot as item data key
- Payment: `{user:<user_id>}:idempotency:<key>` — guarantees same slot as user data key
- SAGA steps: `{saga:<order_id>}:step:reserve|release|charge|refund` — same slot as saga hash

Lua scripts (IDEMPOTENCY_ACQUIRE_LUA, TRANSITION_LUA) are unchanged — each operates on a single KEYS[1], so there are no cross-slot issues.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test fixtures used old key format after hash-tag migration**
- **Found during:** Task 2 verification
- **Issue:** Test helpers (`seed_item`, `seed_user`, `seed_saga`, `get_item_stock`, `get_user_credit`) used bare item_id/user_id as Redis keys; conftest.py seeded data without hash tags; recovery test scan pattern used `saga:*`
- **Fix:** Updated all test helpers to use `{item:<id>}`, `{user:<id>}`, `{saga:<id>}` key formats; updated scan pattern to `{saga:*`; updated idempotency replay test to use new `{saga:<id>}:step:*` key format
- **Files modified:** `tests/test_saga.py`, `tests/test_fault_tolerance.py`, `tests/conftest.py`
- **Commit:** 588624f

## Verification Results

All 37 tests passing after migration.

```
37 passed in 1.24s
```

Verification checks:
1. All four env files contain only REDIS_NODE_HOST and REDIS_PASSWORD
2. All four app.py files import and use RedisCluster with REDIS_NODE_HOST
3. All four services have GET /health with Redis ping
4. Stock keys use {item:} hash tag in both grpc_server.py and app.py
5. Payment keys use {user:} hash tag in both grpc_server.py and app.py
6. Orchestrator saga keys use {saga:} hash tag throughout
7. Stream names use {saga:events} hash tag for co-location
8. Recovery scanner SCAN pattern updated for new key format

## Self-Check: PASSED

- SUMMARY.md exists at `.planning/phases/06-infrastructure/06-01-SUMMARY.md`
- Task 1 commit 3fea9a6 exists
- Task 2 commit 588624f exists
