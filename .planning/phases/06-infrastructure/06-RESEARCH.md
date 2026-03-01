# Phase 6: Infrastructure - Research

**Researched:** 2026-03-01
**Domain:** Redis Cluster (Bitnami Helm + redis.asyncio.RedisCluster), Kubernetes HPA, Docker Compose local dev
**Confidence:** MEDIUM-HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Redis Cluster topology:**
- Separate Redis Cluster per domain (Order, Stock, Payment) — full isolation, independent failover
- 3 primary + 3 replica nodes per cluster (18 Redis nodes total)
- Hash tags per entity (e.g., `{order:123}`) to keep all keys for one entity on the same slot — required for multi-key Lua scripts
- AOF persistence with `everysec` fsync policy
- `noeviction` memory policy (as specified in roadmap)

**CPU budget allocation:**
- Services get CPU priority over Redis nodes (Redis is mostly memory-bound)
- Both resource requests AND limits set on all components — hard limits, no overcommitting
- Goal: fit under 20 CPUs total, no specific utilization target
- If 18 Redis nodes + services don't fit: Claude has discretion to find the best balance (reduce service replicas or Redis CPU allocation)

**Kubernetes scaling policy:**
- HPA for domain services (Order, Stock, Payment): min 1, max 3 replicas
- Scale-up threshold: CPU > 70%
- Scale-down threshold: CPU < 50% (hysteresis gap to prevent flapping)
- Orchestrator pinned at exactly 1 replica (no HPA) — avoids duplicate SAGA orchestration
- HTTP health endpoints on all services: liveness checks process alive, readiness checks Redis connectivity

### Claude's Discretion
- Exact CPU allocation numbers per component (determined during profiling)
- Tradeoff strategy if CPU budget is tight (reduce replicas vs reduce Redis CPU)
- HPA stabilization window durations
- Health probe intervals and failure thresholds
- Exact Helm chart values for Bitnami Redis Cluster

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INFRA-01 | Redis Cluster configured per service domain for high availability (automatic failover) | Bitnami redis-cluster Helm chart (13.0.4), 3+3 topology, AOF + noeviction via `commonConfiguration`; redis.asyncio.RedisCluster client for service code |
| INFRA-02 | Kubernetes HPA configured for auto-scaling service replicas | autoscaling/v2 HPA with `averageUtilization: 70`, scaleDown behavior block for 50% hysteresis |
| INFRA-03 | System runs within 20 CPU benchmark constraint | CPU budget analysis shows ~18.5 CPU baseline; all services must declare requests+limits |
| INFRA-04 | Docker Compose updated for local development with new architecture | Bitnami redis-cluster Docker image with 6 nodes per cluster; orchestrator service already in compose |
| INFRA-05 | Kubernetes manifests updated for production deployment | New Helm install per domain, new Deployment for orchestrator, updated service Deployments with uvicorn, ingress update for payment service name |
</phase_requirements>

---

## Summary

Phase 6 has three distinct technical sub-problems: (1) switching three Redis standalone instances to per-domain Redis Clusters using the Bitnami Helm chart and updating Python service code to use `redis.asyncio.RedisCluster`; (2) rewriting Kubernetes manifests to add the orchestrator Deployment, three HPA resources, and three new Redis Cluster installs; (3) updating Docker Compose for local development and profiling CPU usage under load to validate the 20-CPU budget.

The biggest technical risk is the Lua script compatibility with cluster mode. All existing services use `db.eval()` for idempotency locks (stock, payment gRPC servers) and SAGA state transitions (orchestrator). In Redis Cluster, `eval` only works if all KEYS used by the script map to the same hash slot. The decision to use hash tags (e.g., `{order:123}`) resolves this at the key design level — but every key referenced in KEYS[] must share the same hash tag. This requires auditing every `eval()` call in the codebase and updating key construction.

The CPU budget is tight. With 18 Redis nodes (3 clusters × 6 nodes) plus 4 service types (Order, Stock, Payment, Orchestrator) potentially scaling to 3 replicas each, the total theoretical CPU baseline exceeds 20 CPUs if nodes are allocated generously. Research shows Redis is primarily memory-bound; 100m–200m CPU request per Redis node is realistic. Services need enough CPU for uvicorn workers. A conservative but workable allocation is documented in the Architecture Patterns section.

**Primary recommendation:** Implement in three sequential plans: (1) redis-cluster client migration + Lua key audit, (2) Kubernetes manifest update, (3) Docker Compose update + CPU profiling. Plan 1 is the highest-risk work (code changes); Plans 2 and 3 are configuration changes.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `redis[hiredis]` | 5.0.3 (already installed) | `redis.asyncio.RedisCluster` client | Already in requirements; `RedisCluster` class is in the same package as `Redis` |
| bitnami/redis-cluster Helm chart | 13.0.4 | Kubernetes Redis Cluster deployment | Official Bitnami chart; same vendor as existing `bitnami/redis` usage in project |
| bitnami/redis-cluster Docker image | 8.6 (latest stable) | Docker Compose Redis Cluster local dev | Matches Helm chart; official image with cluster bootstrapping support |
| autoscaling/v2 HPA | Kubernetes 1.23+ | Horizontal Pod Autoscaler | `autoscaling/v2` is stable API; supports `behavior` block for scale-down hysteresis |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `redis.asyncio.cluster.ClusterNode` | included in redis-py 5.0.3 | Specifying startup nodes for cluster client | Required parameter for `RedisCluster(startup_nodes=[...])` |
| `redis.asyncio.cluster.RedisCluster` | included in redis-py 5.0.3 | Async cluster client replacing `redis.asyncio.Redis` | All service code that currently uses `redis.Redis` talking to a standalone instance |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| bitnami/redis-cluster Helm | Custom StatefulSet YAML | bitnami chart handles cluster initialization, slot assignment, failover config automatically; not worth hand-rolling |
| redis.asyncio.RedisCluster | redis.asyncio.Redis with cluster-mode-enabled standalone | Standalone client will fail when redirected (MOVED errors); RedisCluster handles slot routing |
| autoscaling/v2 HPA | KEDA (Kubernetes Event-Driven Autoscaling) | KEDA supports Redis queue depth as metric (ORCH-02 deferred); overkill for v1 CPU-based HPA |

**Installation:** No new packages needed. `redis[hiredis]==5.0.3` already in all service `requirements.txt`. The `RedisCluster` class is available in the same package.

---

## Architecture Patterns

### Recommended File Structure Changes

```
helm-config/
├── redis-helm-values.yaml          # REMOVE (old single-replica redis chart)
├── order-redis-cluster-values.yaml  # NEW: order domain cluster config
├── stock-redis-cluster-values.yaml  # NEW: stock domain cluster config
├── payment-redis-cluster-values.yaml # NEW: payment domain cluster config
└── nginx-helm-values.yaml           # unchanged

k8s/
├── ingress-service.yaml             # update: payment route to payment-service (was user-service)
├── order-app.yaml                   # update: uvicorn cmd, env vars, resources, no REDIS_HOST (cluster)
├── stock-app.yaml                   # update: uvicorn cmd, env vars, resources
├── user-app.yaml → payment-app.yaml # rename + update
└── orchestrator-app.yaml            # NEW: orchestrator Deployment + Service

deploy-charts-cluster.sh             # update: install 3 redis-cluster releases + nginx

env/
├── order_redis.env                  # update: REDIS_NODES (cluster startup nodes)
├── stock_redis.env                  # update
├── payment_redis.env                # update
└── orchestrator_redis.env           # update

docker-compose.yml                   # update: replace 4 standalone redis with 3×6-node clusters
Makefile                             # NEW or update: dev-up, dev-cluster, dev-down targets
```

### Pattern 1: RedisCluster Client Initialization (async)

Replace `redis.Redis(host=..., port=...)` with `redis.asyncio.RedisCluster(startup_nodes=[...])` in every service's `before_serving` hook.

```python
# Source: redis-py 5.0.3 / redis.asyncio.cluster module
import redis.asyncio as redis
from redis.asyncio.cluster import ClusterNode

async def startup():
    global db
    # REDIS_NODES env var: "order-redis-cluster-0:6379,order-redis-cluster-1:6379"
    # Parse from env; only one node needed to bootstrap (client discovers rest)
    node_host = os.environ['REDIS_NODE_HOST']  # e.g. "order-redis-cluster"
    node_port = int(os.environ.get('REDIS_NODE_PORT', '6379'))
    db = redis.asyncio.cluster.RedisCluster(
        startup_nodes=[ClusterNode(node_host, node_port)],
        password=os.environ['REDIS_PASSWORD'],
        decode_responses=False,   # keep existing behavior
        require_full_coverage=True,
    )
    # RedisCluster is awaitable — initializes cluster topology
    await db.initialize()
```

**Shutdown pattern:**
```python
async def shutdown():
    await db.aclose()
```

**Context manager alternative (preferred for clarity):**
```python
async with redis.asyncio.cluster.RedisCluster(startup_nodes=[...]) as db:
    pass
```

### Pattern 2: Lua Scripts with Hash Tags in Cluster Mode

In Redis Cluster, `eval` routes to the node owning the first KEYS[] slot. All KEYS must be in the same slot. The existing Lua scripts each use exactly one key (`ikey` or `saga_key`), so there is no CROSSSLOT issue per call — but `ikey` and the data key (e.g., `request.item_id`) are accessed in the same script body using separate Redis calls. Only the KEYS[] argument determines routing; additional keys accessed inside the Lua body via `redis.call()` that are not in KEYS[] bypass slot checking.

**Important finding (MEDIUM confidence):** Redis 7.0+ without a `#!lua` shebang allows cross-slot access inside Lua script body. The existing scripts do NOT use a shebang and access two distinct keys (`ikey` and `item_id`) in the body. This pattern should continue to work in cluster mode — but only if both keys reside on the same node by coincidence (same slot) OR if the script only declares one key in KEYS[] and accesses the other directly (which Redis Cluster permits for scripts without shebang in some versions).

**Safest approach: use hash tags on ALL keys** so idempotency key and data key for the same entity co-locate:

```python
# stock/grpc_server.py — ReserveStock example
# OLD:
ikey = f"idempotency:{request.idempotency_key}"
entry = await self.db.get(request.item_id)

# NEW with hash tags (idempotency_key contains item_id or order_id):
# Use {item_id} as hash tag so ikey and item_id land on same slot
ikey = f"{{item:{request.item_id}}}:idempotency:{request.idempotency_key}"
item_key = f"{{item:{request.item_id}}}"  # replaces bare request.item_id storage

# Lua script KEYS[1] = ikey — both ikey and item_key share same slot because {item:X}
```

For the orchestrator SAGA keys:
```python
# orchestrator/saga.py
# OLD:
saga_key = f"saga:{order_id}"

# NEW with hash tag:
saga_key = f"{{saga:{order_id}}}"  # hash tag isolates per-order keys to one slot
```

**Eval call unchanged:**
```python
result = await db.eval(IDEMPOTENCY_ACQUIRE_LUA, 1, ikey, 30)
# numkeys=1 → routes to the slot of ikey
```

### Pattern 3: HPA with Scale-Down Hysteresis

The user decision specifies CPU > 70% scale-up, CPU < 50% scale-down. In Kubernetes HPA `autoscaling/v2`, there is no direct "scale-down threshold" parameter; instead, use a `behavior.scaleDown.stabilizationWindowSeconds` to prevent flapping, combined with the default 70% target (which naturally creates hysteresis because scale-down only happens when utilization drops well below the target).

To enforce approximately 50% scale-down, the workaround is to not set an explicit lower threshold (HPA doesn't have one) — instead use a long stabilization window. This prevents premature scale-down after transient load drops.

```yaml
# Source: kubernetes.io/docs autoscaling/v2 spec
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: order-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: order-deployment
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300   # 5 min — Claude's discretion
      policies:
      - type: Pods
        value: 1
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Pods
        value: 1
        periodSeconds: 60
```

### Pattern 4: Bitnami Redis Cluster Helm Values

```yaml
# helm-config/order-redis-cluster-values.yaml
cluster:
  nodes: 6           # 3 primary + 3 replica (1 replica per primary)
  replicas: 1

usePassword: true
password: "redis"    # match existing env files

# AOF persistence + noeviction
commonConfiguration: |-
  appendonly yes
  appendfsync everysec
  maxmemory-policy noeviction

redis:
  useAOFPersistence: "yes"   # enables AOF in bitnami container entrypoint
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 200m
      memory: 512Mi

persistence:
  enabled: true
  size: 1Gi

# Statefulset pod management must be Parallel for cluster join to complete
podManagementPolicy: Parallel
```

### Pattern 5: Docker Compose Redis Cluster (Bitnami)

Bitnami's official docker-compose approach uses 6 containers per cluster. For 3 domains (order, stock, payment) that would be 18 containers — impractical for local dev. The `<specifics>` section from CONTEXT.md directs to a configurable compose: default simplified (3-node single cluster), option for full 18-node.

**Simplified dev mode (default):** Use a single 6-node Bitnami cluster for all services, shared for development. Override `REDIS_NODE_HOST` per service to point to the shared cluster.

**Full topology mode:** Three independent 6-node clusters (18 containers total), matching production exactly.

```yaml
# docker-compose.yml excerpt — shared-cluster dev mode (compose profile: dev)
  order-redis-0:
    image: docker.io/bitnami/redis-cluster:8.6
    environment:
      - REDIS_PASSWORD=redis
      - REDIS_NODES=order-redis-0 order-redis-1 order-redis-2 order-redis-3 order-redis-4 order-redis-5
    volumes:
      - order-redis-data-0:/bitnami/redis/data

  # ... nodes 1-4 same pattern (no REDIS_CLUSTER_CREATOR)

  order-redis-5:
    image: docker.io/bitnami/redis-cluster:8.6
    depends_on:
      - order-redis-0
      - order-redis-1
      - order-redis-2
      - order-redis-3
      - order-redis-4
    environment:
      - REDIS_PASSWORD=redis
      - REDISCLI_AUTH=redis
      - REDIS_NODES=order-redis-0 order-redis-1 order-redis-2 order-redis-3 order-redis-4 order-redis-5
      - REDIS_CLUSTER_REPLICAS=1
      - REDIS_CLUSTER_CREATOR=yes
```

### Pattern 6: Health Probes for Quart/Uvicorn Services

The orchestrator already exposes `GET /health`. All services need `GET /health` endpoints. Liveness = process alive (can respond to HTTP). Readiness = Redis cluster reachable.

```yaml
# k8s Deployment container spec
livenessProbe:
  httpGet:
    path: /health
    port: 5000
  initialDelaySeconds: 15
  periodSeconds: 20
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /health
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3
```

**Readiness check in app code:** `GET /health` should attempt a `db.ping()` and return 503 on failure (so Kubernetes removes the pod from service endpoints during Redis cluster failover).

### Anti-Patterns to Avoid

- **Sharing a single RedisCluster client instance across processes:** The `redis.asyncio.RedisCluster` client is per-event-loop. With uvicorn `--workers 2`, each worker process needs its own client initialized in `before_serving`. This is the existing pattern — do not change it.
- **Using `db=` (database index) with RedisCluster:** Redis Cluster only supports `db=0`. The orchestrator uses `db=3` to avoid test collisions. With cluster mode, this must change — use key prefixes instead (e.g., `orchestrator:{key}`) to namespace keys.
- **Deploying HPA without resource requests on the Deployment:** HPA computes CPU utilization as `actual_cpu / requested_cpu`. If `requests.cpu` is not set, HPA cannot compute utilization and will not scale.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Redis Cluster bootstrapping (slot assignment, node discovery) | Manual redis-cli cluster create scripts | bitnami/redis-cluster image (REDIS_CLUSTER_CREATOR=yes) | Bitnami handles initialization, retry, and health checks automatically |
| Cluster slot routing | Custom sharding logic | redis.asyncio.RedisCluster | Library handles MOVED/ASK redirects, failover, node topology refresh |
| HPA policy tuning | Custom metrics server | autoscaling/v2 behavior block | Native Kubernetes stabilization windows prevent flapping without additional infrastructure |

**Key insight:** The Bitnami redis-cluster image's `REDIS_CLUSTER_CREATOR=yes` on node-5 fully automates the multi-step `redis-cli --cluster create` procedure. Without this, cluster creation requires an init container or manual intervention.

---

## Common Pitfalls

### Pitfall 1: `db=` Index Not Supported in Cluster Mode

**What goes wrong:** `redis.asyncio.RedisCluster(db=3)` raises an error or silently falls back to `db=0`. Redis Cluster protocol does not support SELECT commands or non-zero database indices.

**Why it happens:** Regular Redis supports 16 databases; Redis Cluster is a single-keyspace system. The orchestrator currently sets `db=3` in its Redis connection to avoid collisions during tests.

**How to avoid:** Replace `db=3` with a key prefix. The orchestrator's SAGA keys already use `saga:{order_id}` and stream keys like `saga:checkout:events`. In cluster mode, no `db=` parameter should be passed (or pass `db=0` explicitly).

**Warning signs:** `redis.exceptions.DataError: DB value is not an integer` or `redis.exceptions.ResponseError: ERR SELECT is not allowed in cluster mode` at startup.

### Pitfall 2: CROSSSLOT Error from Lua Scripts Accessing Two Different-Slot Keys

**What goes wrong:** `redis.exceptions.ResponseError: CROSSSLOT Keys in request don't hash to the same slot` when `eval()` is called with KEYS[] containing keys from different slots.

**Why it happens:** The existing Lua scripts in `stock/grpc_server.py` and `payment/grpc_server.py` pass `ikey` as KEYS[1], but the script body also calls `redis.call('GET', ...)` and `redis.call('SET', ...)` on `request.item_id` (a different key). Without a shebang, Redis 7.0+ permits this, but only if the data key is NOT in KEYS[]. Since scripts only declare `ikey` in KEYS[], the cluster routes based on `ikey`'s slot. The `item_id` key is accessed directly in the body — this works only if `item_id` hashes to the same slot as `ikey`.

**How to avoid:** Use hash tags to force co-location. Pattern: `{item:<item_id>}:idempotency:<idemkey>` and `{item:<item_id>}` for the stock value. Similarly for SAGA keys in the orchestrator: `{saga:<order_id>}` and `{saga:<order_id>}:idempotency:<idemkey>`.

**Warning signs:** Intermittent `CROSSSLOT` errors under load (slot collision is probabilistic without hash tags, so some requests succeed and others fail).

### Pitfall 3: RedisCluster `initialize()` Must Be Awaited Before First Use

**What goes wrong:** Service starts, receives a request before cluster topology is discovered, and the first `await db.get(key)` raises `ClusterError: RedisCluster not yet initialized`.

**Why it happens:** Unlike `redis.asyncio.Redis`, the `RedisCluster` client requires an explicit `await db.initialize()` call (or use as async context manager) before the first command.

**How to avoid:** Call `await db.initialize()` in `before_serving` after constructing the client, or use `await RedisCluster(...)` (awaitable form).

### Pitfall 4: Bitnami `usePassword` Must Match `password` Env Var

**What goes wrong:** Redis Cluster nodes start but `redis-cli` AUTH fails during cluster creation; cluster never forms.

**Why it happens:** The Bitnami image uses `REDIS_PASSWORD` env var. The Helm chart's `password` field maps to this. If the Helm values use `usePassword: true` but the `password` field is empty, a random password is generated — and the services' env files don't know it.

**How to avoid:** Always set `password: "redis"` explicitly in the Helm values to match the existing `REDIS_PASSWORD=redis` in service env files.

### Pitfall 5: HPA Requires CPU Requests on Deployment

**What goes wrong:** HPA is created but `kubectl describe hpa` shows `<unknown>/70%` and no scaling ever occurs.

**Why it happens:** HPA CPU utilization = (actual_cpu) / (requested_cpu × replicas). If `requests.cpu` is not set on the Deployment's container spec, the denominator is undefined.

**How to avoid:** Every service Deployment must have `resources.requests.cpu` set. The existing `order-app.yaml`, `stock-app.yaml`, and `user-app.yaml` already have `cpu: "1"` — but this should be tuned down now that services use async (uvicorn is more efficient than gunicorn).

### Pitfall 6: Docker Compose Cluster Needs Dependency Chain

**What goes wrong:** `order-redis-5` (CLUSTER_CREATOR) starts before other nodes are ready, `redis-cli --cluster create` fails, cluster never initializes.

**Why it happens:** The creator node tries to connect to all `REDIS_NODES` immediately on container start. If they're not up yet, initialization fails.

**How to avoid:** Node-5 must `depends_on` nodes 0-4 in Docker Compose. The Bitnami official `docker-compose.yml` already implements this pattern — copy it.

---

## Code Examples

Verified patterns from research:

### RedisCluster Async Client Initialization

```python
# Source: redis-py 5.0.3 redis.asyncio.cluster module
import redis.asyncio as redis
from redis.asyncio.cluster import RedisCluster, ClusterNode

# In before_serving hook:
startup_nodes = [ClusterNode(os.environ['REDIS_NODE_HOST'],
                              int(os.environ.get('REDIS_NODE_PORT', '6379')))]
db = RedisCluster(
    startup_nodes=startup_nodes,
    password=os.environ['REDIS_PASSWORD'],
    decode_responses=False,
    require_full_coverage=True,
    health_check_interval=30,
)
await db.initialize()
```

### Hash Tag Key Construction

```python
# stock/grpc_server.py — idempotency key with hash tag
item_key = f"{{item:{request.item_id}}}"                          # data key
ikey = f"{{item:{request.item_id}}}:idempotency:{request.idempotency_key}"  # idempotency key
# Both keys share hash tag {item:<item_id>} → guaranteed same slot

# orchestrator/saga.py — SAGA key with hash tag
saga_key = f"{{saga:{order_id}}}"                                 # hash key
# ikey for orchestrator ops:
ikey = f"{{saga:{order_id}}}:idempotency:{idempotency_key}"
```

### Bitnami Redis Cluster Helm Values (complete example)

```yaml
# helm-config/order-redis-cluster-values.yaml
cluster:
  nodes: 6
  replicas: 1

usePassword: true
password: "redis"

commonConfiguration: |-
  appendonly yes
  appendfsync everysec
  maxmemory-policy noeviction

redis:
  useAOFPersistence: "yes"
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 200m
      memory: 512Mi

persistence:
  enabled: true
  size: 1Gi
```

### HPA Manifest (autoscaling/v2)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: order-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: order-deployment
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Pods
        value: 1
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Pods
        value: 1
        periodSeconds: 30
```

### Updated Kubernetes Deployment (Order, example)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-deployment
spec:
  replicas: 1
  selector:
    matchLabels:
      component: order
  template:
    metadata:
      labels:
        component: order
    spec:
      containers:
        - name: order
          image: order:latest
          command: ["uvicorn"]
          args: ["app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "2"]
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
          env:
            - name: REDIS_NODE_HOST
              value: "order-redis-cluster"    # Bitnami redis-cluster Service name
            - name: REDIS_NODE_PORT
              value: "6379"
            - name: REDIS_PASSWORD
              value: "redis"
            - name: GATEWAY_URL
              value: "http://gateway:80"
            - name: ORCHESTRATOR_GRPC_ADDR
              value: "orchestrator-service:50053"
          livenessProbe:
            httpGet:
              path: /health
              port: 5000
            initialDelaySeconds: 15
            periodSeconds: 20
          readinessProbe:
            httpGet:
              path: /health
              port: 5000
            initialDelaySeconds: 10
            periodSeconds: 10
```

---

## CPU Budget Analysis

**Constraint:** 20 CPUs total across all services and Redis nodes.

**Components and proposed allocations:**

| Component | Count (max) | CPU Request | CPU Limit | Total Request | Total Limit |
|-----------|-------------|-------------|-----------|---------------|-------------|
| order-service | 3 (HPA max) | 500m | 1000m | 1500m | 3000m |
| stock-service | 3 (HPA max) | 500m | 1000m | 1500m | 3000m |
| payment-service | 3 (HPA max) | 500m | 1000m | 1500m | 3000m |
| orchestrator | 1 (fixed) | 500m | 1000m | 500m | 1000m |
| nginx gateway | 1 | 500m | 500m | 500m | 500m |
| order Redis nodes | 6 | 100m | 200m | 600m | 1200m |
| stock Redis nodes | 6 | 100m | 200m | 600m | 1200m |
| payment Redis nodes | 6 | 100m | 200m | 600m | 1200m |
| **TOTAL** | | | | **7300m (7.3 CPU)** | **17100m (17.1 CPU)** |

At maximum scale (all HPAs at max 3), total CPU requests = 7.3 CPUs, total limits = 17.1 CPUs. This fits within 20 CPUs with headroom. The 20 CPU constraint refers to actual usage under benchmark load, not sum of limits.

**If budget is tight** (Claude's discretion): Reduce Redis CPU limit to 150m, or reduce service limits to 750m. Redis is memory-bound; 100m request is realistic for a lightly-loaded cluster node.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `autoscaling/v1` HPA (CPU only, no behavior) | `autoscaling/v2` with behavior block | K8s 1.23 stable | Enables scale-down hysteresis without custom metrics |
| redis-py-cluster (separate package) | `redis.asyncio.RedisCluster` in `redis` package | redis-py 4.1.0 | No separate install; same package already in requirements.txt |
| gunicorn in Deployment commands | uvicorn (already migrated in Phase 1) | Phase 1 complete | Existing k8s manifests still have `gunicorn` — must be updated |
| `bitnami/redis` Helm chart (master-replica) | `bitnami/redis-cluster` Helm chart (sharded cluster) | Different chart | Separate chart install; existing `deploy-charts-cluster.sh` installs wrong chart |

**Deprecated/outdated in this project:**
- `gunicorn` in `k8s/*.yaml` command fields: All manifests have `gunicorn` still. Must be replaced with `uvicorn` as Phase 1 migrated all service code.
- `bitnami/redis` in `deploy-charts-cluster.sh`: Must be replaced with `bitnami/redis-cluster` for each domain.
- `REDIS_HOST` / `REDIS_PORT` env vars: These point to standalone Redis. With cluster mode, replace with `REDIS_NODE_HOST` (single discovery node) or a comma-separated `REDIS_NODES` list.
- `user-service` in ingress: `k8s/ingress-service.yaml` routes `/payment/` to `user-service`. The deployment was renamed `payment-service` in Phase 1 (orchestrator env var `PAYMENT_GRPC_ADDR=payment-service:50051`). Ingress must be updated.

---

## Open Questions

1. **How does the orchestrator `db=3` (database index) conflict with cluster mode?**
   - What we know: Redis Cluster only supports `db=0`. The orchestrator uses `db=3` to avoid test-time key collisions with stock/payment (which use `db=0`).
   - What's unclear: Whether tests still pass if orchestrator switches to `db=0` with key prefixes (SAGA keys already have `saga:` prefix so collision is unlikely in practice).
   - Recommendation: Remove `db=3` from orchestrator, add key prefix documentation, update conftest if needed.

2. **Do the `tests/` unit tests need updating for RedisCluster client?**
   - What we know: The existing tests in `tests/conftest.py` use `redis.asyncio.Redis` (standalone). After the code change to `redis.asyncio.RedisCluster`, test fixtures either need a real local cluster or mock the client.
   - What's unclear: Whether tests should use `fakeredis` with cluster support or spin up a Docker-based cluster.
   - Recommendation: Tests should remain focused on standalone Redis via monkeypatching. Use `unittest.mock.AsyncMock` or `fakeredis.aioredis.FakeRedis` to mock `db`. The RedisCluster client should be tested with integration tests against a running cluster (separate from unit tests).

3. **Does `REDIS_CLUSTER_CREATOR=yes` on node-5 in Docker Compose require all nodes to be healthy first?**
   - What we know: Bitnami's official compose uses `depends_on` node-5 → nodes 0-4. The creator runs `redis-cli --cluster create`.
   - What's unclear: Whether Docker Compose `depends_on` is sufficient (it only waits for container start, not Redis readiness) and if a healthcheck + `condition: service_healthy` is needed.
   - Recommendation: Add a healthcheck on each node (`redis-cli -a redis ping`) and use `depends_on: condition: service_healthy` on node-5. This adds reliability for CI/dev.

---

## Sources

### Primary (HIGH confidence)
- redis-py 5.0.3 / `redis.asyncio.cluster` module — RedisCluster constructor signature verified from source module docs
- [Clustering — redis-py 7.2.1 docs](https://redis.readthedocs.io/en/stable/clustering.html) — startup_nodes, multi-key operations, Lua limitations
- [bitnami/charts redis-cluster values.yaml (main)](https://github.com/bitnami/charts/blob/main/bitnami/redis-cluster/values.yaml) — cluster.nodes, cluster.replicas, commonConfiguration, useAOFPersistence
- [bitnami/containers redis-cluster docker-compose.yml](https://github.com/bitnami/containers/blob/main/bitnami/redis-cluster/docker-compose.yml) — 6-node compose pattern, REDIS_CLUSTER_CREATOR, REDIS_NODES env vars
- [Kubernetes autoscaling/v2 HPA walkthrough](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/) — behavior block, stabilizationWindowSeconds

### Secondary (MEDIUM confidence)
- [redis-py asyncio cluster module source](https://redis-py-uglide.readthedocs.io/en/latest/_modules/redis/asyncio/cluster.html) — confirmed `await db.initialize()` pattern and async context manager support
- [Redis Cluster Lua script cross-slot behavior](https://redis.io/docs/latest/develop/programmability/eval-intro/) — confirmed scripts without shebang can access non-KEYS[] keys in cluster mode
- [bitnami/charts redis-cluster GitHub issues #14510, #36235](https://github.com/bitnami/charts/issues/14510) — `commonConfiguration` field syntax for maxmemory-policy and appendonly confirmed

### Tertiary (LOW confidence)
- CPU budget numbers (100m Redis, 500m services) — derived from reasoning about memory-bound Redis workloads; needs validation under actual benchmark load
- Scale-down hysteresis via stabilizationWindowSeconds as proxy for "CPU < 50%" threshold — Kubernetes HPA does not have a separate scale-down metric threshold; this is a behavioral approximation

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — redis-py 5.0.3 already installed; RedisCluster is in the same package; Bitnami chart version confirmed
- Architecture: MEDIUM — key patterns verified from official docs; CPU numbers are estimates requiring profiling
- Pitfalls: MEDIUM-HIGH — Lua cross-slot and db=0 restriction are documented Redis Cluster behaviors; Docker Compose timing is a known Bitnami issue

**Research date:** 2026-03-01
**Valid until:** 2026-04-01 (stable infrastructure tooling; Bitnami chart version may increment)
