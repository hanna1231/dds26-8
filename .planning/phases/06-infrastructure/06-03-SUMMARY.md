---
phase: 06-infrastructure
plan: 03
subsystem: infra
tags: [docker-compose, redis-cluster, bitnami, makefile, local-dev]

# Dependency graph
requires:
  - phase: 06-01
    provides: RedisCluster client migration with REDIS_NODE_HOST env var pattern

provides:
  - docker-compose.yml with per-domain Bitnami Redis Clusters (full profile) and shared cluster (simple profile)
  - Makefile with dev-up (simple), dev-cluster (full), dev-down, dev-clean, dev-logs, dev-build, dev-status, test targets
  - Local development environment mirroring production Kubernetes topology

affects: [deployment, local-dev, testing]

# Tech tracking
tech-stack:
  added: [bitnami/redis-cluster:8.0, Docker Compose profiles]
  patterns:
    - Per-domain Redis Clusters with 6-node Bitnami containers (3 primary + 3 replica via REDIS_CLUSTER_REPLICAS=1)
    - Cluster creator pattern: node-5 has REDIS_CLUSTER_CREATOR=yes + depends_on service_healthy nodes 0-4
    - Docker Compose profiles for topology switching (simple vs full)
    - REDIS_NODE_HOST env var with defaults for profile-agnostic service configuration

key-files:
  created:
    - Makefile
  modified:
    - docker-compose.yml
    - env/order_redis.env
    - env/stock_redis.env
    - env/payment_redis.env
    - env/orchestrator_redis.env

key-decisions:
  - "All per-domain Redis nodes (18) use profiles: full; all shared Redis nodes (6) use profiles: simple — prevents profile overlap"
  - "Application services have NO profile (always start) and rely on restart: always + RedisCluster retry for Redis availability"
  - "Orchestrator shares payment Redis cluster with keys isolated by {saga:} hash tag"
  - "dev-up (simple mode) overrides all *_REDIS_HOST env vars to shared-redis-0 via Makefile; dev-cluster uses per-domain defaults"
  - "REDISCLI_AUTH=redis required on node-5 for redis-cli --cluster create to authenticate against password-protected nodes"

patterns-established:
  - "Bitnami redis-cluster pattern: 6 nodes per cluster, node-5 is CLUSTER_CREATOR with depends_on service_healthy on nodes 0-4"
  - "Makefile profile switching: pass *_REDIS_HOST env vars + --profile flag to docker compose"

requirements-completed: [INFRA-03, INFRA-04]

# Metrics
duration: 5min
completed: 2026-03-01
---

# Phase 6 Plan 03: Docker Compose Redis Cluster and Makefile Summary

**Replaced 4 standalone Redis containers with 18 Bitnami redis-cluster:8.0 nodes across 3 per-domain clusters plus 6 shared-cluster nodes, with Makefile targets for profile-based topology switching.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-01T08:12:55Z
- **Completed:** 2026-03-01T08:18:00Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Rewrote docker-compose.yml replacing 4 standalone redis:7.2 containers with 18 Bitnami redis-cluster:8.0 nodes (6 per domain across order/stock/payment) tagged with `profiles: full`, plus 6 shared-redis nodes tagged with `profiles: simple`
- Each cluster's node-5 is the CLUSTER_CREATOR with `REDIS_CLUSTER_CREATOR=yes`, `REDISCLI_AUTH=redis`, and `depends_on: service_healthy` on nodes 0-4 to ensure cluster formation before services start
- Application services use `REDIS_NODE_HOST` with env var defaults (e.g., `${ORDER_REDIS_HOST:-order-redis-0}`) enabling Makefile-driven profile switching without code changes
- Created Makefile with 8 targets: `dev-up` (simple 6-node shared cluster), `dev-cluster` (full 18-node 3-cluster topology), `dev-down`, `dev-clean`, `dev-logs`, `dev-build`, `dev-status`, `test`

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite docker-compose.yml with per-domain Bitnami Redis Clusters** - `f5dc5f9` (feat)
2. **Task 2: Create Makefile with developer workflow targets** - `229e001` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `docker-compose.yml` - Rewritten: 18 per-domain Bitnami redis-cluster nodes (full profile) + 6 shared nodes (simple profile) + 5 application services + 24 named volumes
- `Makefile` - New: dev-up, dev-cluster, dev-down, dev-clean, dev-logs, dev-build, dev-status, test targets
- `env/order_redis.env` - Updated REDIS_NODE_HOST from order-db to order-redis-0
- `env/stock_redis.env` - Updated REDIS_NODE_HOST from stock-db to stock-redis-0
- `env/payment_redis.env` - Updated REDIS_NODE_HOST from payment-db to payment-redis-0
- `env/orchestrator_redis.env` - Updated REDIS_NODE_HOST from orchestrator-db to payment-redis-0

## Decisions Made

- **No profiles on application services**: Services start regardless of profile. `restart: always` + RedisCluster client retry handles Redis availability. This avoids complex conditional `depends_on` across profiles.
- **Orchestrator shares payment cluster**: Keys isolated by `{saga:}` hash tag so orchestrator SAGA state and payment user balances don't collide.
- **Profile isolation**: `profiles: full` on all 18 per-domain nodes prevents them from starting in `simple` mode. `profiles: simple` on shared nodes prevents them from running in `full` mode.
- **REDISCLI_AUTH on node-5**: Required for the `redis-cli --cluster create` command to authenticate when cluster formation is triggered.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Local development environment is complete. Run `make dev-up` for fast iteration (shared cluster) or `make dev-cluster` to mirror production topology.
- Phase 6 complete: Redis Cluster client migration (06-01), Kubernetes manifests (06-02), and Docker Compose local dev (06-03) are all done.
- System is ready for performance benchmarking or production deployment.

---
*Phase: 06-infrastructure*
*Completed: 2026-03-01*
