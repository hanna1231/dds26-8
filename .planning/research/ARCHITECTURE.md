# Architecture Research: SAGA-Based Distributed Checkout System

**Research Date:** 2026-02-27
**Research Type:** Project Research — Architecture dimension
**Milestone:** Subsequent milestone — SAGA orchestrator, gRPC, message queue, Redis Cluster, Kubernetes scaling
**Question:** How should a SAGA-based distributed checkout system be structured?

---

## Summary

The target architecture extends the existing three-service checkout system (Order, Stock, Payment) with a dedicated SAGA orchestrator service that owns all distributed transaction logic. External clients communicate via HTTP through Nginx. Internal service-to-service communication uses gRPC. The message queue (Redis Streams) carries events between the orchestrator and domain services for async coordination. Each service stores its domain data in Redis Cluster. Kubernetes HPA scales the stateless services under load.

The SAGA orchestrator is the architectural centerpiece. It replaces the ad-hoc rollback logic currently embedded in the Order service and makes distributed transactions recoverable, observable, and correct under partial failure.

---

## Component Boundaries

### External API Layer (Nginx Gateway)

- Accepts all HTTP requests from clients and the benchmark
- Routes by URL prefix: `/orders/*` to Order service, `/stock/*` to Stock service, `/payment/*` to Payment service
- The external API contract (routes, request/response shapes) is frozen — cannot change
- Does not communicate with the SAGA orchestrator directly
- Remains unchanged in scope; only the backends it points to are upgraded

### Order Service

- Owns order data: create, find, addItem
- On `POST /orders/checkout/{order_id}`: reads the order, then delegates to the SAGA orchestrator via gRPC to start a checkout saga
- Returns success/failure to the caller after the orchestrator resolves the saga
- Does not contain rollback logic — that responsibility moves to the orchestrator
- Stores OrderValue structs in its own Redis Cluster shard
- Scales horizontally; stateless per request

### Stock Service

- Owns inventory data: item create, find, add, subtract
- Exposes gRPC endpoints for: subtract stock (with idempotency key), add stock (compensation), find item price
- Still exposes HTTP for the external API routes the gateway hits directly
- Does not initiate or coordinate transactions — it only responds to instructions
- Stores StockValue structs in its own Redis Cluster shard
- Scales horizontally; stateless per request

### Payment Service

- Owns user credit data: create user, find user, add funds, pay
- Exposes gRPC endpoints for: charge user (with idempotency key), refund user (compensation), find user balance
- Still exposes HTTP for the external API routes the gateway hits directly
- Does not initiate or coordinate transactions
- Stores UserValue structs in its own Redis Cluster shard
- Scales horizontally; stateless per request

### SAGA Orchestrator Service

- New service. Central coordinator for all checkout transactions.
- Receives `StartCheckout(order_id, user_id, items, total_cost)` via gRPC from the Order service
- Runs the SAGA state machine: sequences steps, handles failures, triggers compensations
- Persists saga state to its own Redis instance after every state transition (durable log)
- On startup, scans for in-progress sagas and resumes them (crash recovery)
- Publishes events to the message queue for async notification (saga started, saga completed, saga failed)
- Does NOT expose external HTTP routes — internal only
- Is the only service allowed to call Stock and Payment gRPC endpoints for transactional operations
- Single replica in steady state (state machine correctness); can run multiple replicas with saga ID-based partitioning if needed

### Message Queue (Redis Streams)

- Event bus for async coordination and observability
- The orchestrator publishes saga lifecycle events; domain services and external consumers can subscribe
- Also used as the durable inbox for compensating transaction retries if a downstream service is temporarily unavailable
- Topology described below

### Redis Cluster

- Three separate Redis Cluster deployments (one per domain service: order-db, stock-db, payment-db)
- The orchestrator uses a fourth Redis instance for saga state storage (single-node or small cluster)
- Provides high availability via replica sets and automatic failover
- Data sharding handled by Redis Cluster slot assignment

### Kubernetes (Scaling Layer)

- All services run as Kubernetes Deployments
- HPA scales Order, Stock, and Payment services based on CPU/request load
- The orchestrator runs as a single replica or with leader election if multiple replicas needed
- Redis Cluster is deployed via Helm (Bitnami Redis Cluster chart)
- Nginx Ingress replaces the docker-compose Nginx gateway

---

## Data Flow: Checkout Transaction Through SAGA Orchestrator

```
Client
  |
  | POST /orders/checkout/{order_id}  [HTTP]
  v
Nginx Gateway
  |
  | routes to Order Service  [HTTP internal]
  v
Order Service
  |
  | 1. Read order from order-db (Redis Cluster)
  | 2. gRPC: StartCheckout(saga_id, order_id, user_id, items, total_cost)
  v
SAGA Orchestrator
  |
  | 3. Persist saga state: STARTED  --> orchestrator-db (Redis)
  | 4. Publish event: saga.started  --> Redis Streams
  |
  | Step 1: Reserve Stock
  | 5. gRPC: SubtractStock(item_id, qty, idempotency_key=saga_id+step)
  v
Stock Service
  |  [executes subtract, persists to stock-db]
  |
  v
SAGA Orchestrator (receives gRPC response)
  |
  | 6. Persist saga state: STOCK_RESERVED
  |
  | Step 2: Charge Payment
  | 7. gRPC: ChargeUser(user_id, amount, idempotency_key=saga_id+step)
  v
Payment Service
  |  [executes charge, persists to payment-db]
  |
  v
SAGA Orchestrator (receives gRPC response)
  |
  | 8. Persist saga state: PAYMENT_CHARGED
  | 9. gRPC: MarkOrderPaid(order_id)
  v
Order Service
  |  [sets order.paid = True in order-db]
  |
  v
SAGA Orchestrator
  |
  | 10. Persist saga state: COMPLETED
  | 11. Publish event: saga.completed  --> Redis Streams
  | 12. Return success to caller via gRPC
  v
Order Service
  |
  | 13. Return HTTP 200 to Nginx
  v
Client  <-- HTTP 200 OK
```

**Failure path (payment fails after stock reserved):**

```
SAGA Orchestrator (payment gRPC returns error)
  |
  | Persist saga state: COMPENSATING
  |
  | Compensation Step 1: Restore Stock
  | gRPC: AddStock(item_id, qty, idempotency_key=saga_id+comp_step)
  v
Stock Service  [restores stock in stock-db]
  |
  v
SAGA Orchestrator
  |
  | Persist saga state: FAILED
  | Publish event: saga.failed  --> Redis Streams
  | Return failure to Order Service via gRPC
  v
Order Service --> HTTP 400 to client
```

---

## SAGA State Machine Design

### States

```
STARTED
  --> STOCK_RESERVED      (stock subtracted successfully)
  --> FAILED              (stock subtraction failed, no compensation needed)

STOCK_RESERVED
  --> PAYMENT_CHARGED     (payment charged successfully)
  --> COMPENSATING        (payment failed, begin rollback)

PAYMENT_CHARGED
  --> ORDER_MARKED_PAID   (order.paid set to True)
  --> COMPENSATING        (order update failed — rare, treat as partial failure)

ORDER_MARKED_PAID
  --> COMPLETED

COMPENSATING
  --> STOCK_RESTORED      (stock add succeeded)
  --> COMPENSATION_FAILED (stock add failed after retries)

STOCK_RESTORED
  --> FAILED

FAILED                    (terminal — transaction did not complete)
COMPLETED                 (terminal — transaction committed)
COMPENSATION_FAILED       (terminal — requires operator intervention / manual reconciliation)
```

### Steps and Compensations

| Step | Forward Action | Compensation Action |
|------|---------------|-------------------|
| 1    | SubtractStock(item_id, qty, idempotency_key) | AddStock(item_id, qty, idempotency_key) |
| 2    | ChargeUser(user_id, amount, idempotency_key) | RefundUser(user_id, amount, idempotency_key) |
| 3    | MarkOrderPaid(order_id) | MarkOrderUnpaid(order_id) — rarely needed |

Compensations run in reverse order of forward steps (compensation for step N before compensation for step N-1).

### Idempotency Keys

Every gRPC call from the orchestrator to a domain service carries an idempotency key constructed as `{saga_id}:{step_name}:{direction}`. Domain services must deduplicate on this key using a Redis SET NX pattern before applying mutations. This ensures retries after network failures do not double-charge or double-subtract.

### Timeouts

- Per-step timeout: 5 seconds. If a gRPC call to a domain service does not respond within 5 seconds, the orchestrator treats it as a failure and triggers compensation.
- Saga-level timeout: 30 seconds. If a saga has not reached a terminal state within 30 seconds of creation, a background sweep marks it COMPENSATING and begins rollback.
- Compensation retry: Exponential backoff starting at 1 second, max 5 retries before entering COMPENSATION_FAILED state.

### Crash Recovery

On orchestrator startup:
1. Scan orchestrator-db for all sagas not in terminal state (COMPLETED, FAILED, COMPENSATION_FAILED)
2. For each: resume from the current state using idempotent gRPC calls (safe to replay because of idempotency keys)
3. Sagas in COMPENSATING state: resume compensation from the last completed compensation step

---

## gRPC Layer Design

### How gRPC Fits Alongside HTTP

- External API: HTTP only, via Nginx. Client-facing routes are unchanged.
- Internal API: gRPC only, for service-to-service calls initiated by the orchestrator and by the Order service when starting a saga.
- Each domain service runs two servers on different ports: HTTP on port 5000 (existing, for gateway routing) and gRPC on port 50051 (new, for internal calls).
- The orchestrator runs only a gRPC server (no HTTP).

### Proto Definitions (conceptual)

```proto
// saga_orchestrator.proto
service SagaOrchestrator {
  rpc StartCheckout(CheckoutRequest) returns (CheckoutResponse);
}

// stock_service.proto
service StockService {
  rpc SubtractStock(StockMutationRequest) returns (StockMutationResponse);
  rpc AddStock(StockMutationRequest) returns (StockMutationResponse);
  rpc FindItem(FindItemRequest) returns (FindItemResponse);
}

// payment_service.proto
service PaymentService {
  rpc ChargeUser(PaymentRequest) returns (PaymentResponse);
  rpc RefundUser(PaymentRequest) returns (PaymentResponse);
  rpc FindUser(FindUserRequest) returns (FindUserResponse);
}

// order_service.proto
service OrderService {
  rpc MarkOrderPaid(MarkOrderPaidRequest) returns (MarkOrderPaidResponse);
}
```

### Python gRPC Libraries

- `grpcio` + `grpcio-tools` for stub generation
- Async gRPC via `grpcio` with `asyncio` (compatible with Quart+Uvicorn async model)
- One gRPC channel per downstream service, reused across requests (connection pooling built into gRPC)

---

## Message Queue Topology

### Technology Decision: Redis Streams over Kafka

Redis Streams is the recommended choice for this project because:
- Redis is already a hard dependency (course requirement); no new infrastructure needed
- Redis Streams supports consumer groups, message acknowledgment, and replay — all required features
- Kafka introduces significant operational overhead (Zookeeper/KRaft, broker management) that is not justified for three topics under benchmark load
- Redis Streams integrates with the same Redis Cluster used for data, simplifying deployment

### Stream Topology

| Stream Name | Producers | Consumers | Purpose |
|-------------|-----------|-----------|---------|
| `saga.events` | SAGA Orchestrator | Monitoring, future consumers | Saga lifecycle events (started, completed, failed) |
| `saga.compensation.retry` | SAGA Orchestrator | SAGA Orchestrator (retry worker) | Failed compensation steps that need retry |

### Consumer Groups

- `saga.events` stream: consumer group `monitoring` for observability consumers
- `saga.compensation.retry` stream: consumer group `orchestrator-retry` with one consumer per orchestrator replica

### Message Schema

Each message is a Redis Stream entry (field-value pairs). Example saga lifecycle event:

```
XADD saga.events * saga_id <uuid> event_type saga.completed order_id <uuid> user_id <uuid> timestamp <unix_ms>
```

### Delivery Guarantees

- At-least-once delivery via consumer group acknowledgment (XACK after processing)
- Idempotent consumers (saga state transitions are idempotent via state machine guards)
- Dead letter: after N retries without ACK, message moved to `saga.dlq` stream for inspection

---

## Kubernetes Deployment Topology

### Service Deployments

| Service | Min Replicas | Max Replicas | HPA Trigger | Notes |
|---------|-------------|-------------|-------------|-------|
| Order Service | 2 | 8 | CPU > 70% | Stateless; scales freely |
| Stock Service | 2 | 8 | CPU > 70% | Stateless; scales freely |
| Payment Service | 2 | 8 | CPU > 70% | Stateless; scales freely |
| SAGA Orchestrator | 1 | 1 | Manual scaling only | State machine; single replica safe initially |
| Nginx Ingress | 1 | 3 | CPU > 60% | Managed by ingress controller |

### Redis Cluster Deployments

Each domain gets its own Redis Cluster (via Bitnami Helm chart):
- `order-redis-cluster`: 3 primary + 3 replica nodes
- `stock-redis-cluster`: 3 primary + 3 replica nodes
- `payment-redis-cluster`: 3 primary + 3 replica nodes
- `orchestrator-redis`: Single Redis instance (or 1 primary + 1 replica for HA) for saga state

### Resource Limits (per pod, approximate)

- Order/Stock/Payment services: 0.5 CPU request, 1 CPU limit, 256Mi memory
- SAGA Orchestrator: 0.5 CPU request, 1 CPU limit, 512Mi memory (holds saga state in memory during processing)
- Redis Cluster nodes: 0.5 CPU, 1Gi memory per node

### Total CPU Budget

Under the 20 CPU benchmark limit:
- 3 domain services x 8 pods x 0.5 CPU request = 12 CPU
- 1 orchestrator x 1 pod x 0.5 CPU = 0.5 CPU
- Redis Cluster nodes: 4 clusters x 3 primary nodes x 0.5 CPU = 6 CPU
- Buffer: 1.5 CPU for Nginx and system overhead

Note: Limits must be tuned during benchmarking. HPA max replicas may need reduction depending on Redis CPU draw.

### Network Topology

- All inter-service gRPC calls use Kubernetes ClusterIP services (internal DNS: `stock-service:50051`)
- External traffic enters via Nginx Ingress on LoadBalancer service
- Redis Clusters use headless services for cluster node discovery

---

## Suggested Build Order

The build order respects dependency chains: each phase can only start when its prerequisites are built and tested.

### Phase 1: Foundation

1. **Migrate to Quart+Uvicorn** (all three domain services)
   - No external dependencies; validates async compatibility before adding complexity
   - Order, Stock, Payment services become async; HTTP API unchanged

2. **Add gRPC servers to domain services** (Stock, Payment, Order)
   - Requires Quart+Uvicorn running (async gRPC server shares event loop)
   - Define proto files; generate stubs; implement gRPC endpoints for transactional operations
   - Test each service's gRPC interface in isolation

3. **Build SAGA Orchestrator service**
   - Depends on gRPC stubs from step 2
   - Implement state machine, Redis state persistence, idempotency key handling
   - Wire the Order service `checkout` endpoint to call the orchestrator via gRPC instead of calling Stock/Payment directly
   - Test the happy path end-to-end

4. **Implement compensating transactions and timeout/retry logic**
   - Depends on orchestrator state machine existing
   - Add compensation steps to state machine, timeout enforcement, compensation retry

5. **Add Redis Streams integration**
   - Depends on orchestrator existing (it produces events)
   - Add XADD calls after each terminal state transition
   - Add retry consumer that reads `saga.compensation.retry` stream

### Phase 2: Infrastructure Upgrade

6. **Configure Redis Cluster for each domain service**
   - Depends on services being stable (do not refactor data layer during logic changes)
   - Update Redis connection code to use redis-py cluster client
   - Test data persistence under cluster failover

7. **Update Kubernetes manifests**
   - Depends on all services being containerized and Redis Cluster configured
   - Add orchestrator Deployment and Service
   - Add Redis Cluster Helm values
   - Configure HPA for domain services
   - Add Redis Streams to orchestrator Redis or separate stream Redis instance

8. **Benchmark and tune**
   - Run locust benchmark; identify bottleneck (likely connection pool sizes or Redis Cluster routing)
   - Adjust HPA thresholds, replica counts, Redis connection pool sizes

---

## Key Architectural Constraints and Tradeoffs

**SAGA vs 2PC:** Redis does not support XA transactions, ruling out 2PC. SAGAs with compensating transactions are the correct pattern. The tradeoff is eventual consistency during the compensation window — a brief period where stock is deducted but payment has not yet been confirmed. Idempotency keys and the orchestrator's durable state log mitigate this.

**Orchestrator as single replica:** Running one orchestrator replica eliminates split-brain risk in the state machine. If orchestrator pod dies, Kubernetes restarts it and it resumes in-progress sagas from Redis state. The recovery window is the pod restart time (~5-10 seconds), which is acceptable.

**gRPC for internal, HTTP for external:** The external benchmark hits HTTP endpoints. gRPC is added as a second server within each service process. This maintains the required API contract while gaining gRPC performance for internal coordination.

**Redis Streams over Kafka:** Operational simplicity wins at this scale. The benchmark runs against 20 CPUs; Kafka brokers would consume a meaningful fraction of that budget with no clear performance benefit over Redis Streams for three event topics.

---

## Build Order Implications for Roadmap Phases

| Build Step | Phase | Blocks |
|-----------|-------|--------|
| Quart+Uvicorn migration | Phase 1 | All subsequent steps |
| gRPC stubs + domain service gRPC servers | Phase 1 | Orchestrator build |
| SAGA Orchestrator (state machine + persistence) | Phase 1 | Compensation logic, Redis Streams |
| Compensating transactions + timeouts | Phase 1 | Correct fault tolerance |
| Redis Streams integration | Phase 1 | Async event coordination |
| Redis Cluster configuration | Phase 2 | K8s infrastructure |
| Kubernetes HPA + orchestrator deployment | Phase 2 | Benchmark readiness |
| Benchmark tuning | Phase 2 | Final submission |

---

*Research authored: 2026-02-27*
