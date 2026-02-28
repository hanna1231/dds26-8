---
phase: 04-fault-tolerance
verified: 2026-02-28T15:30:00Z
status: passed
score: 20/20 must-haves verified
gaps: []
human_verification:
  - test: "Kill the orchestrator container mid-checkout (after stock reserved, before payment charged) and verify SAGA resumes to COMPLETED or FAILED after restart"
    expected: "SAGA driven to terminal state on next orchestrator startup; no phantom stock deduction"
    why_human: "Requires Docker Compose running with live containers; can't simulate real process kill programmatically in unit tests"
  - test: "Kill the stock-service container while a checkout is active and observe circuit breaker behavior"
    expected: "After 5 consecutive failures, CircuitBreakerError is raised; checkout compensates; payment service continues to accept calls"
    why_human: "Requires Docker container kill to test real network-level failure path, not in-process mock"
---

# Phase 4: Fault Tolerance Verification Report

**Phase Goal:** The system remains consistent when any single container is killed mid-transaction; incomplete SAGAs resume on orchestrator restart; cascade failures are contained
**Verified:** 2026-02-28T15:30:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Circuit breaker opens after 5 consecutive gRPC failures and stops further calls | VERIFIED | `circuit.py` has `failure_threshold=5`; `test_circuit_breaker_trips_after_threshold` passes (12/12 tests pass) |
| 2 | Circuit breaker enters half-open after 30s cooldown and closes on successful probe | VERIFIED | `recovery_timeout=30` in `circuit.py`; `test_circuit_breaker_half_open_recovery` passes |
| 3 | Stock and Payment have independent circuit breakers (Stock outage does not block Payment) | VERIFIED | `stock_breaker` and `payment_breaker` are separate module-level instances; `test_independent_breakers` passes |
| 4 | Forward SAGA steps retry max 3 times before triggering compensation | VERIFIED | `retry_forward(fn, max_attempts=3)` in `grpc_server.py` lines 68-103; `test_retry_forward_exhaustion` and `test_retry_forward_succeeds_on_retry` pass |
| 5 | Compensation steps still retry indefinitely (retry_forever unchanged for compensation) | VERIFIED | `retry_forever` unchanged in `grpc_server.py` lines 37-61; only called from `run_compensation` |
| 6 | CircuitBreakerError propagates immediately out of retry loops (never retried) | VERIFIED | `retry_forward` line 95-96 raises immediately; `test_retry_forward_propagates_circuit_breaker_error` confirms call_count==1 |
| 7 | On orchestrator startup, all stale non-terminal SAGAs are scanned and driven to terminal state before serving | VERIFIED | `app.py` line 23: `await recover_incomplete_sagas(db)` called before `app.add_background_task(serve_grpc, db)` |
| 8 | Recovery scanner resumes forward for STARTED/STOCK_RESERVED/PAYMENT_CHARGED states | VERIFIED | `recovery.py` `resume_saga` lines 40-79; `test_recovery_resolves_stale_started_saga` and `test_no_sagas_stranded_after_recovery` pass |
| 9 | Recovery scanner drives COMPENSATING SAGAs to FAILED via run_compensation | VERIFIED | `recovery.py` lines 32-35; `test_recovery_resolves_stale_compensating_saga` passes |
| 10 | SAGAs younger than 5 minutes are skipped during recovery (not stale) | VERIFIED | `STALENESS_THRESHOLD_SECONDS=300` in `recovery.py`; `test_recovery_skips_fresh_sagas` passes |
| 11 | Circuit breaker trips trigger COMPENSATING transition in run_checkout | VERIFIED | `grpc_server.py` lines 266-275 catch `CircuitBreakerError` and run compensation; `test_run_checkout_compensates_on_circuit_breaker` confirms SAGA reaches FAILED |
| 12 | After recovery, no SAGA remains in a non-terminal state | VERIFIED | `test_no_sagas_stranded_after_recovery` scans all `saga:*` keys and asserts terminal state for all |
| 13 | Circuit breaker tests properly reset breaker state (no cross-test pollution) | VERIFIED | `_reset_breaker(breaker)` calls `breaker.reset()` in `finally` blocks in all circuit breaker tests |
| 14 | Compensation still retries indefinitely when circuit breaker is open during compensation | VERIFIED | `run_compensation` calls `retry_forever` not `retry_forward`; compensation bypass of circuit breaker error handling confirmed in `grpc_server.py` |
| 15 | circuitbreaker==2.1.3 added to orchestrator/requirements.txt | VERIFIED | Line 7 of `orchestrator/requirements.txt` |
| 16 | All services in docker-compose.yml have restart: always | VERIFIED | 9 occurrences of `restart: always` in `docker-compose.yml` (all 5 app services + 4 db services) |
| 17 | Recovery skips terminal SAGAs (COMPLETED/FAILED) | VERIFIED | `recovery.py` line 112 `continue` for non-`NON_TERMINAL_STATES`; `test_recovery_skips_terminal_sagas` passes |
| 18 | All 6 gRPC client functions wrapped with appropriate circuit breaker | VERIFIED | `client.py`: `@stock_breaker` on `reserve_stock/release_stock/check_stock`; `@payment_breaker` on `charge_payment/refund_payment/check_payment` |
| 19 | CircuitBreakerError re-exported from client.py for callers | VERIFIED | `client.py` line 4: `from circuitbreaker import CircuitBreakerError  # re-exported for callers` |
| 20 | All existing test_saga.py tests pass without regression | VERIFIED | `pytest tests/test_saga.py -x`: 10/10 passed |

**Score:** 20/20 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `orchestrator/circuit.py` | Per-service circuit breaker instances | VERIFIED | 27 lines; `stock_breaker` and `payment_breaker` as module-level `CircuitBreaker` instances with `failure_threshold=5`, `recovery_timeout=30`, `expected_exception=grpc.aio.AioRpcError` |
| `orchestrator/recovery.py` | Startup SAGA scanner and resume_saga function | VERIFIED | 133 lines; `recover_incomplete_sagas` with `scan_iter`, staleness check, and `resume_saga` with forward-first recovery and `CircuitBreakerError` handling |
| `orchestrator/client.py` | gRPC client functions wrapped with circuit breakers | VERIFIED | All 6 functions decorated; `CircuitBreakerError` re-exported; imports `stock_breaker` and `payment_breaker` from `circuit` |
| `orchestrator/grpc_server.py` | Bounded forward retry and CircuitBreakerError handling in run_checkout | VERIFIED | `retry_forward` at lines 68-103; `run_checkout` uses `retry_forward` for both stock reservation and payment; `except CircuitBreakerError` at line 266 |
| `orchestrator/app.py` | Startup recovery hook blocking new requests until scan completes | VERIFIED | `await recover_incomplete_sagas(db)` at line 23, between `init_grpc_clients()` and `add_background_task(serve_grpc, db)` |
| `tests/test_fault_tolerance.py` | 12 fault tolerance tests | VERIFIED | 386 lines; 12 tests all passing in 0.11s |
| `tests/conftest.py` | requires_docker marker registration | VERIFIED | `pytest_configure` at lines 184-188 registers `requires_docker` marker |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `orchestrator/circuit.py` | `orchestrator/client.py` | `stock_breaker` and `payment_breaker` decorators imported and applied | WIRED | `client.py` line 6: `from circuit import stock_breaker, payment_breaker`; applied at lines 54, 63, 72, 90, 99, 108 |
| `orchestrator/recovery.py` | `orchestrator/app.py` | `recover_incomplete_sagas` called in before_serving hook | WIRED | `app.py` line 7: `from recovery import recover_incomplete_sagas`; called at line 23 inside `startup()` |
| `orchestrator/grpc_server.py` | `orchestrator/client.py` | `CircuitBreakerError` caught in `run_checkout` to trigger compensation | WIRED | `grpc_server.py` line 195 imports `CircuitBreakerError` locally; caught at line 266; compensation runs at line 274 |
| `tests/test_fault_tolerance.py` | `orchestrator/circuit.py` | Tests import and manipulate breaker state | WIRED | Line 18: `from circuit import stock_breaker, payment_breaker`; state manipulation in every circuit breaker test |
| `tests/test_fault_tolerance.py` | `orchestrator/recovery.py` | Tests call `recover_incomplete_sagas` against seeded SAGA records | WIRED | Line 23: `from recovery import recover_incomplete_sagas, resume_saga, STALENESS_THRESHOLD_SECONDS`; called in 5 recovery tests |
| `tests/test_fault_tolerance.py` | `orchestrator/grpc_server.py` | Tests call `retry_forward` and `run_checkout` | WIRED | Line 22: `from grpc_server import retry_forward, run_checkout, run_compensation`; used in 4 tests |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| FAULT-01 | 04-01, 04-02 | System recovers when any single container (service or database) is killed | SATISFIED | `restart: always` on all 9 containers; startup recovery scanner drives stale SAGAs to terminal state; `test_retry_forward_exhaustion` and `test_retry_forward_succeeds_on_retry` pass |
| FAULT-02 | 04-01, 04-02 | On orchestrator startup, incomplete SAGAs are scanned and resolved (complete or compensate) | SATISFIED | `recover_incomplete_sagas` scans `saga:*` with staleness threshold; recovery tests for all non-terminal states pass |
| FAULT-03 | 04-01, 04-02 | System remains consistent after container kill + recovery cycle | SATISFIED | Forward-first idempotent recovery reuses original idempotency keys; `test_no_sagas_stranded_after_recovery` confirms all SAGAs reach terminal state; `test_run_checkout_compensates_on_circuit_breaker` confirms SAGA reaches FAILED |
| FAULT-04 | 04-01, 04-02 | Circuit breaker prevents cascade failures when downstream services are unavailable | SATISFIED | Independent `stock_breaker` and `payment_breaker` with threshold=5, timeout=30; `test_circuit_breaker_trips_after_threshold`, `test_circuit_breaker_half_open_recovery`, `test_independent_breakers` all pass |

No orphaned requirements found. All four FAULT-0x requirements are claimed by both plans (04-01 and 04-02) and verified by test evidence.

### Anti-Patterns Found

None detected. Scanned `orchestrator/circuit.py`, `orchestrator/recovery.py`, `orchestrator/client.py`, `orchestrator/grpc_server.py`, `orchestrator/app.py`, `tests/test_fault_tolerance.py` for TODO/FIXME/placeholder/empty implementations. Zero matches.

### Human Verification Required

#### 1. Live Container Kill Mid-Checkout

**Test:** Run `docker compose up`, trigger a checkout, kill the orchestrator container (`docker kill <orchestrator>`) after `STOCK_RESERVED` but before `PAYMENT_CHARGED`. Restart it with `docker compose start orchestrator-service`.
**Expected:** After restart, the SAGA resumes forward (charges payment, marks COMPLETED) or compensates (releases stock, marks FAILED). No phantom stock deduction or orphaned SAGA in a partial state.
**Why human:** Requires Docker Compose with live containers and precise kill timing. The startup recovery blocks serving until scan completes, but the real kill scenario cannot be reproduced with in-process mocks.

#### 2. Stock Service Container Kill - Circuit Breaker Behavior

**Test:** With Docker Compose running, kill `stock-service` (`docker kill <stock-service>`). Submit 6+ checkout requests.
**Expected:** First 5 fail with `AioRpcError`. The 6th raises `CircuitBreakerError`, returns `service unavailable`, and the SAGA is compensated. Payment service calls continue to work normally (independent breaker). After 30s, the half-open probe fires when stock-service is restarted.
**Why human:** Requires real container process kill; network-level gRPC failures are fundamentally different from in-process `AioRpcError` mocks. The half-open recovery also needs real 30s elapsed time.

### Gaps Summary

No gaps. All automated checks pass:
- 12/12 fault tolerance tests pass in 0.11s
- 10/10 existing SAGA regression tests pass
- All 5 artifacts exist and are substantive (not stubs)
- All 6 key links are wired (imports present, functions called)
- All 4 FAULT requirements are satisfied with test evidence
- 9 `restart: always` policies in docker-compose.yml
- `circuitbreaker==2.1.3` in requirements.txt
- Zero anti-patterns found in modified files
- All 3 implementation commits (bf509de, 33e1c6e, 1a78053) verified in git log

Phase 4 goal achieved: the system remains consistent when containers are killed mid-transaction (recovery scanner + idempotent replay), incomplete SAGAs resume on orchestrator restart (startup hook blocks until stale SAGAs are driven to terminal state), and cascade failures are contained (independent circuit breakers per service, bounded forward retry, immediate CircuitBreakerError propagation).

---
_Verified: 2026-02-28T15:30:00Z_
_Verifier: Claude (gsd-verifier)_
