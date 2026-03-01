---
phase: 07
plan: 01
subsystem: validation
tags: [testing, benchmark, consistency, grpc, cas, redis-cluster]
dependency-graph:
  requires: [05-01, 05-02, 06-01, 06-02]
  provides: [validated-cluster, benchmark-target]
  affects: [stock-service, payment-service, orchestrator-service]
tech-stack:
  added: [wdm-project-benchmark, redis:7.2-bookworm custom image]
  patterns: [compare-and-swap lua, atomic idempotency+cas, cas retry loop]
key-files:
  created:
    - docker/redis-cluster/Dockerfile
    - docker/redis-cluster/entrypoint.sh
    - .planning/phases/07-validation-and-delivery/07-01-SUMMARY.md
  modified:
    - Makefile
    - .gitignore
    - docker-compose.yml
    - stock/grpc_server.py
    - payment/grpc_server.py
    - orchestrator/recovery.py
    - stock/requirements.txt
    - payment/requirements.txt
    - order/requirements.txt
    - orchestrator/requirements.txt
decisions:
  - CAS retry loop instead of locking: atomic Lua compare-and-swap eliminates race window without distributed lock overhead; RETRY signal drives Python-side retry until CAS succeeds
  - Custom redis:7.2 image replaces bitnami: bitnami/redis-cluster:8.0 not available on Docker Hub; custom entrypoint.sh replicates env var API exactly so docker-compose.yml semantics unchanged
  - python3 not python in Makefile: macOS and Linux container base images do not ship a `python` symlink; benchmark target uses python3 for portability
metrics:
  duration-minutes: 90
  completed-date: "2026-03-01"
  tasks-completed: 2
  tasks-total: 2
  files-changed: 12
---

# Phase 7 Plan 01: Integration Tests and Benchmark Summary

**One-liner:** Atomic CAS Lua scripts eliminate stock/payment oversell race condition; all 37 integration tests green and wdm-project-benchmark passes with 0 consistency violations.

## Objective

Prove the system is functionally correct before delivery by: (1) running all integration tests, (2) adding a `make benchmark` target, and (3) running the wdm-project-benchmark consistency test against the live Docker Compose cluster with 0 inconsistencies.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Integration tests green | fc235eb | tests/ (37 tests pass) |
| 2 | Benchmark target + consistency fixes | e65aa19 | Makefile, docker-compose.yml, docker/, stock/grpc_server.py, payment/grpc_server.py, orchestrator/recovery.py, requirements.txt x4 |

## Verification Results

### Task 1: Integration Tests
```
============================== 37 passed in 1.99s ==============================
```
All 37 tests pass across test_fault_tolerance, test_grpc_integration, and test_saga suites. No code changes required.

### Task 2: Benchmark
```
INFO - verify - Stock service inconsistencies in the logs: 0
INFO - verify - Stock service inconsistencies in the database: 0
INFO - verify - Payment service inconsistencies in the logs: 0
INFO - verify - Payment service inconsistencies in the database: 0
```
wdm-project-benchmark consistency test: 1000 concurrent checkouts against 100-stock item, 0 inconsistencies.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] bitnami/redis-cluster:8.0 image not available**
- **Found during:** Task 2 (docker compose build)
- **Issue:** `manifest for bitnami/redis-cluster:8.0 not found` — Bitnami moved images off Docker Hub; version 8.0 does not exist at any accessible registry
- **Fix:** Created `docker/redis-cluster/Dockerfile` using `redis:7.2-bookworm` base + `entrypoint.sh` that replicates bitnami's env var API (`REDIS_PASSWORD`, `REDIS_NODES`, `REDIS_CLUSTER_CREATOR`, `REDIS_CLUSTER_REPLICAS`, `REDIS_PORT_NUMBER`). Updated docker-compose.yml to use `build:` for cluster-creator nodes and `image: redis-cluster:local` for remaining nodes.
- **Files modified:** `docker/redis-cluster/Dockerfile`, `docker/redis-cluster/entrypoint.sh`, `docker-compose.yml`
- **Commit:** e65aa19

**2. [Rule 3 - Blocking] quart==0.20.1 does not exist on PyPI**
- **Found during:** Task 2 (docker compose build)
- **Issue:** `No matching distribution found for quart==0.20.1` — latest available version is 0.20.0
- **Fix:** Updated all 4 service requirements.txt files: `quart==0.20.1` → `quart==0.20.0`
- **Files modified:** `stock/requirements.txt`, `payment/requirements.txt`, `order/requirements.txt`, `orchestrator/requirements.txt`
- **Commit:** e65aa19

**3. [Rule 1 - Bug] Race condition causing benchmark inconsistencies**
- **Found during:** Task 2 (benchmark run showed 143 completed SAGAs vs 100 stock items)
- **Issue:** Non-atomic GET-then-SET in `ReserveStock` and `ChargePayment`: concurrent gRPC calls all read the same stock/credit value before any write completes; all compute `value - cost >= 0` and all succeed, over-reserving stock and over-charging payment
- **Fix:** Replaced non-atomic read-modify-write with atomic CAS (compare-and-swap) Lua scripts:
  - `RESERVE_STOCK_ATOMIC_LUA`: atomically checks idempotency, reads current bytes, returns RETRY if bytes changed since Python read, otherwise writes new value
  - `CHARGE_PAYMENT_ATOMIC_LUA`: same pattern for payment
  - Both methods wrap the eval in a `while True:` CAS retry loop; loops until CAS succeeds or resource is actually insufficient
- **Files modified:** `stock/grpc_server.py`, `payment/grpc_server.py`
- **Commit:** e65aa19

**4. [Rule 1 - Bug] WRONGTYPE error crashing orchestrator on startup**
- **Found during:** Task 2 (orchestrator crash loop on startup)
- **Issue:** `recovery.py` scans `{saga:*}` keys; Redis Stream `{saga:events}:checkout` lives in same hash slot as SAGA hashes; `hgetall` on a stream key raises `WRONGTYPE Operation against a key holding the wrong kind of value`
- **Fix:** Wrapped `db.hgetall(key)` in `try/except Exception: continue` to skip non-hash keys
- **Files modified:** `orchestrator/recovery.py`
- **Commit:** e65aa19

**5. [Rule 3 - Blocking] `python` command not found in macOS Makefile**
- **Found during:** Task 2 (`make benchmark` failed with `/bin/sh: python: command not found`)
- **Issue:** macOS and standard Linux images don't ship a `python` symlink
- **Fix:** Changed `python run_consistency_test.py` to `python3 run_consistency_test.py` in Makefile
- **Files modified:** `Makefile`
- **Commit:** e65aa19

## Decisions Made

1. **CAS retry loop instead of distributed locking**: Atomic Lua compare-and-swap avoids the complexity and overhead of distributed locks. The RETRY signal from Lua drives a Python-side retry loop; in practice very few retries occur since the CAS window is microseconds.

2. **Custom redis:7.2 image instead of finding alternative bitnami tag**: The bitnami env var API (`REDIS_NODES`, `REDIS_CLUSTER_CREATOR`) was already used throughout docker-compose.yml; creating a compatible entrypoint required fewer changes than redesigning the cluster initialization approach.

3. **Idempotency key CAS combination**: The CAS Lua scripts atomically combine idempotency check AND the CAS update in a single EVAL. This means a RETRY also clears the `__PROCESSING__` idempotency marker, allowing the same idempotency key to retry cleanly on the next loop iteration.

## Self-Check: PASSED

- `docker/redis-cluster/Dockerfile` - EXISTS
- `docker/redis-cluster/entrypoint.sh` - EXISTS
- Commit fc235eb - FOUND
- Commit e65aa19 - FOUND
- 37 tests pass
- Benchmark: 0 inconsistencies
