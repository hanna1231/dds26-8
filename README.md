# DDS26-8: Distributed Checkout System

A fault-tolerant distributed checkout system built with the SAGA orchestration pattern, gRPC communication, Redis Cluster, and event-driven architecture.

See [docs/architecture.md](docs/architecture.md) for the full architecture design document.

## Project Structure

| Directory | Description |
|-----------|-------------|
| `order/` | Order service (Quart+Uvicorn, async Redis, gRPC client to orchestrator) |
| `stock/` | Stock service (Quart+Uvicorn, async Redis, gRPC server on :50051) |
| `payment/` | Payment service (Quart+Uvicorn, async Redis, gRPC server on :50051) |
| `orchestrator/` | SAGA orchestrator (Quart+Uvicorn, gRPC server on :50053, Redis Streams) |
| `protos/` | Protocol Buffer definitions for Stock, Payment, and Orchestrator services |
| `scripts/` | Kill-container consistency test scripts |
| `tests/` | Integration tests (gRPC, SAGA, fault tolerance, events) |
| `k8s/` | Kubernetes Deployments, Services, HPAs, and Ingress |
| `helm-config/` | Helm chart values for per-domain Redis Cluster and nginx ingress |
| `env/` | Redis environment variables for docker-compose |
| `docs/` | Architecture design document |

## Quick Start

**Requirements:** Docker and Docker Compose

### Local Development (simple mode)

Uses a single shared 6-node Redis Cluster for all services:

```bash
make dev-up
```

API available at http://localhost:8000

### Full Topology (mirrors production)

Uses 3 independent 6-node Redis Clusters (18 nodes total), one per service domain:

```bash
make dev-cluster
```

### Important: Do NOT run `docker compose up` directly

The services default to per-domain Redis hostnames (`order-redis-0`, `stock-redis-0`, etc.) which only exist in the full profile. The Makefile targets set the correct environment variables for each mode. Always use `make dev-up` or `make dev-cluster`.

### Other Commands

```bash
make dev-logs      # Follow service logs
make dev-status    # Show container status
make dev-down      # Stop containers (data preserved)
make dev-clean     # Stop and remove everything (clean slate)
make dev-build     # Rebuild service images
```

## Running Tests

### Integration Tests

```bash
make test
```

Runs 37 integration tests covering gRPC communication, SAGA orchestration, fault tolerance, and event-driven architecture. Requires Redis on localhost:6379.

### Benchmark (consistency test)

```bash
make dev-up        # start the cluster first
make benchmark
```

Clones and runs the [wdm-project-benchmark](https://github.com/delftdata/wdm-project-benchmark) consistency test against the running cluster.

### Kill-Container Test

```bash
make dev-up
make kill-test SERVICE=stock-service    # test a single service
make kill-test-all                      # test all services sequentially
```

Verifies that the system remains consistent (no lost money or stock) after killing and restarting a service mid-transaction.

## Kubernetes Deployment

```bash
# Install Redis Clusters and nginx ingress
./deploy-charts-cluster.sh

# Deploy application services
kubectl apply -f k8s/
```

HPA auto-scales order, stock, and payment services (CPU > 70%, max 3 replicas). The orchestrator runs as a single replica. Total CPU budget fits within 20 CPUs at max scale.
