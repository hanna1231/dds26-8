---
phase: 06-infrastructure
plan: 02
subsystem: infra
tags: [kubernetes, helm, redis-cluster, hpa, autoscaling, uvicorn, ingress]

# Dependency graph
requires:
  - phase: 06-01
    provides: Redis Cluster client migration (RedisCluster class, REDIS_NODE_HOST env var pattern)
  - phase: 03-saga-orchestration
    provides: Orchestrator gRPC server on port 50053, SAGA coordination
provides:
  - Per-domain Bitnami redis-cluster Helm values (order, stock, payment) with 6 nodes, AOF, noeviction
  - Updated deploy-charts-cluster.sh installing 3 redis-cluster releases + nginx
  - Updated k8s Deployments using uvicorn with CPU resources and health probes
  - Orchestrator Deployment + Service (HTTP 5000, gRPC 50053, replicas: 1)
  - Three HPA manifests (order, stock, payment) with CPU 70%, min 1 / max 3
  - Fixed ingress routing /payment/ to payment-service
affects: [deployment, kubernetes-operations, load-testing]

# Tech tracking
tech-stack:
  added: [bitnami/redis-cluster helm chart, autoscaling/v2 HPA API]
  patterns:
    - Per-domain Redis Cluster isolation with {domain}-redis-cluster-redis-cluster service naming
    - HPA with scale-down stabilization (300s) to prevent thrashing
    - Orchestrator pinned to replicas: 1, no HPA (split-brain prevention)
    - uvicorn workers=2 for domain services, workers=1 for orchestrator

key-files:
  created:
    - helm-config/order-redis-cluster-values.yaml
    - helm-config/stock-redis-cluster-values.yaml
    - helm-config/payment-redis-cluster-values.yaml
    - k8s/orchestrator-app.yaml
    - k8s/order-hpa.yaml
    - k8s/stock-hpa.yaml
    - k8s/payment-hpa.yaml
  modified:
    - deploy-charts-cluster.sh
    - k8s/order-app.yaml
    - k8s/stock-app.yaml
    - k8s/user-app.yaml
    - k8s/ingress-service.yaml

key-decisions:
  - "Orchestrator shares payment-redis-cluster (not a 4th cluster) — {saga:} hash tag prefix isolates keys, avoids 6 more Redis nodes (600m more CPU)"
  - "user-app.yaml kept as filename but service renamed to payment-service and component to payment for git history preservation"
  - "Bitnami redis-cluster creates service <release>-redis-cluster, so REDIS_NODE_HOST uses order-redis-cluster-redis-cluster pattern"
  - "HPA scale-down stabilizationWindowSeconds: 300 prevents premature scale-down during bursty traffic"
  - "Old redis-helm-values.yaml removed — single shared Redis replaced by per-domain clusters"

patterns-established:
  - "Redis Cluster service naming: <release-name>-redis-cluster where release is {domain}-redis-cluster"
  - "All domain service Deployments: 500m/1000m CPU, 512Mi/1Gi memory, uvicorn workers=2"
  - "Health probes: initialDelaySeconds 10/15, periodSeconds 10/20, failureThreshold 3"

requirements-completed: [INFRA-01, INFRA-02, INFRA-03, INFRA-05]

# Metrics
duration: 15min
completed: 2026-03-01
---

# Phase 6 Plan 02: Kubernetes Infrastructure Manifests Summary

**Bitnami redis-cluster Helm values (6 nodes, AOF, noeviction) per domain, uvicorn Deployments with CPU-based HPA (70%, min 1/max 3), orchestrator pinned at replicas: 1, ingress fixed to payment-service — total 7.3 CPU requests / 14.1 CPU limits at max scale within 20 CPU budget**

## Performance

- **Duration:** 15 min
- **Started:** 2026-03-01T08:15:00Z
- **Completed:** 2026-03-01T08:30:00Z
- **Tasks:** 2
- **Files modified:** 12

## Accomplishments

- Three per-domain Redis Cluster Helm values files with 6 nodes (3 primary + 3 replica), AOF persistence, noeviction policy, 100m/200m CPU per node
- All k8s Deployments updated from gunicorn to uvicorn with correct CPU resource limits (500m request / 1000m limit) enabling HPA to function
- Three HPA manifests (order, stock, payment) targeting 70% CPU with scale-down stabilization window of 300s to prevent thrashing
- Orchestrator Deployment created with replicas: 1 locked (no HPA), Service exposing both HTTP (5000) and gRPC (50053) ports
- Ingress /payment/ route fixed from user-service to payment-service (was broken in original manifests)
- Old shared redis-helm-values.yaml removed, deploy-charts-cluster.sh installs 3 redis-cluster releases

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Helm values and deploy script for per-domain Redis Clusters** - `a31d34c` (feat)
2. **Task 2: Update Kubernetes Deployment manifests, create HPA resources, and fix ingress** - `b6e6a3c` (feat)

**Plan metadata:** *(docs commit following)*

## Files Created/Modified

- `helm-config/order-redis-cluster-values.yaml` - Bitnami redis-cluster values: 6 nodes, AOF, noeviction, 100m/200m CPU
- `helm-config/stock-redis-cluster-values.yaml` - Same as order, for stock domain
- `helm-config/payment-redis-cluster-values.yaml` - Same as order, for payment domain
- `helm-config/redis-helm-values.yaml` - DELETED (replaced by per-domain cluster values)
- `deploy-charts-cluster.sh` - Rewritten: installs order/stock/payment-redis-cluster + nginx
- `k8s/order-app.yaml` - uvicorn command, 500m/1000m CPU, REDIS_NODE_HOST, health probes
- `k8s/stock-app.yaml` - Same as order, adds gRPC containerPort 50051
- `k8s/user-app.yaml` - Renamed service to payment-service/payment-deployment, keeps image user:latest
- `k8s/orchestrator-app.yaml` - NEW: orchestrator Service (HTTP+gRPC) + Deployment (replicas: 1)
- `k8s/order-hpa.yaml` - NEW: HPA targeting order-deployment, CPU 70%, min 1 / max 3
- `k8s/stock-hpa.yaml` - NEW: HPA targeting stock-deployment, CPU 70%, min 1 / max 3
- `k8s/payment-hpa.yaml` - NEW: HPA targeting payment-deployment, CPU 70%, min 1 / max 3
- `k8s/ingress-service.yaml` - Fixed /payment/ backend from user-service to payment-service

## Decisions Made

- **Orchestrator shares payment-redis-cluster:** The `{saga:}` hash tag prefix isolates all orchestrator keys from payment domain keys within the same cluster. Creating a 4th Redis cluster would add 6 more nodes and 600m more CPU request — unnecessary given key isolation. Total stays within 20 CPU budget.
- **user-app.yaml filename preserved:** Service renamed internally to payment-service/payment-deployment but file kept as user-app.yaml for git history continuity. The Docker image remains `user:latest`.
- **Bitnami service naming:** The Bitnami redis-cluster chart creates a service named `<release-name>-redis-cluster`. With release name `order-redis-cluster`, the service is `order-redis-cluster-redis-cluster`. This is used in REDIS_NODE_HOST env vars across all Deployments.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. All Kubernetes manifests are declarative YAML ready for `kubectl apply`.

## Next Phase Readiness

- All Kubernetes manifests production-ready for cluster deployment
- deploy-charts-cluster.sh ready to run against a Kubernetes cluster with Helm installed
- Phase 6 Plan 03 (final infrastructure plan) can proceed
- CPU budget verified: 7.3 CPU requests / 14.1 CPU limits at max HPA scale — safely within 20 CPU constraint

## Self-Check: PASSED

All files verified present:
- helm-config/order-redis-cluster-values.yaml FOUND
- helm-config/stock-redis-cluster-values.yaml FOUND
- helm-config/payment-redis-cluster-values.yaml FOUND
- deploy-charts-cluster.sh FOUND
- k8s/order-app.yaml FOUND
- k8s/stock-app.yaml FOUND
- k8s/user-app.yaml FOUND
- k8s/orchestrator-app.yaml FOUND
- k8s/order-hpa.yaml FOUND
- k8s/stock-hpa.yaml FOUND
- k8s/payment-hpa.yaml FOUND
- k8s/ingress-service.yaml FOUND
- .planning/phases/06-infrastructure/06-02-SUMMARY.md FOUND

Commits verified:
- a31d34c FOUND (Task 1: Helm values + deploy script)
- b6e6a3c FOUND (Task 2: k8s manifests + HPA + ingress fix)

---
*Phase: 06-infrastructure*
*Completed: 2026-03-01*
