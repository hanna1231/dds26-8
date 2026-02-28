---
phase: 03-saga-orchestration
plan: 04
subsystem: tests
tags: [saga, integration-tests, grpc, redis, idempotency, compensation]
dependency_graph:
  requires: [03-03]
  provides: [test-coverage-saga]
  affects: [orchestrator/saga.py, orchestrator/grpc_server.py, orchestrator/client.py]
tech_stack:
  added: []
  patterns: [pytest-asyncio session-scoped fixtures, grpc.aio manual server creation, unittest.mock patch for grpc error simulation]
key_files:
  created:
    - tests/test_saga.py
  modified:
    - tests/conftest.py
decisions:
  - "orchestrator_db uses Redis db=3 to avoid collision with stock/payment (both db=0 in tests)"
  - "Orchestrator gRPC server started manually via grpc.aio.server() (not serve_grpc which blocks)"
  - "Test 9 patches grpc_server.release_stock + asyncio.sleep to simulate transient failures without real delays"
  - "Test 10 directly replays stock/payment operations with same idempotency keys to prove Lua caching"
  - "clean_orchestrator_db is function-scoped (flushes db=3 before each test); stock/payment use unique IDs per test"
metrics:
  duration: 176s
  completed: "2026-02-28"
  tasks_completed: 2
  files_changed: 2
---

# Phase 3 Plan 4: SAGA Integration Tests Summary

SAGA integration test suite with 10 passing tests covering state machine, checkout flow, compensation, exactly-once semantics, and retry behavior using real Redis and gRPC servers.

## What Was Built

`tests/test_saga.py` — 10 integration tests covering all SAGA requirements:

| Test | Requirement | What it proves |
|------|------------|----------------|
| 1. `test_saga_record_created_before_side_effects` | SAGA-01 | `create_saga_record` atomically creates hash with all flags=0 before any gRPC |
| 2. `test_saga_state_transitions_valid` | SAGA-02 | STARTED→STOCK_RESERVED→PAYMENT_CHARGED→COMPLETED all succeed |
| 3. `test_saga_state_transition_invalid_rejected` | SAGA-02 | Invalid transitions (STARTED→COMPLETED, STARTED→FAILED) raise ValueError |
| 4. `test_checkout_happy_path` | SAGA-03 | Full checkout: COMPLETED, stock decremented, credit decremented |
| 5. `test_checkout_insufficient_stock_compensates` | SAGA-04 | Stock=0 → FAILED, payment never charged |
| 6. `test_checkout_insufficient_credit_compensates` | SAGA-04 | Credit=0 → FAILED, stock restored to original |
| 7. `test_checkout_duplicate_returns_original` | SAGA-06 | Same order_id returns stored result, no double-execution |
| 8. `test_saga_duplicate_creation_prevented` | SAGA-01 | HSETNX prevents duplicate SAGA record creation |
| 9. `test_compensation_retries_until_success` | SAGA-05 | `retry_forever` retries `release_stock` 3x (2 UNAVAILABLE + 1 success) |
| 10. `test_idempotency_keys_prevent_duplicate_side_effects` | IDMP-01/02/03 | Replaying same idempotency keys returns cached result without modifying balances |

`tests/conftest.py` — Added orchestrator fixtures:
- `orchestrator_db`: Redis client on db=3 (session-scoped)
- `orchestrator_grpc_server`: OrchestratorServiceServicer on :50053, depends on `grpc_clients`
- `orchestrator_stub`: OrchestratorServiceStub gRPC channel (session-scoped)
- `clean_orchestrator_db`: function-scoped fixture that flushes orchestrator db before each test

## Verification

```
17 passed in 0.18s
```

All 10 new SAGA tests pass alongside 7 pre-existing gRPC integration tests.

## Decisions Made

1. **Redis db=3 for orchestrator**: Avoids key collision with stock/payment which both use db=0 in test environment.

2. **Manual gRPC server creation in fixtures**: `orchestrator_grpc_server` creates `grpc.aio.server()` directly rather than calling `serve_grpc()` (which blocks on `wait_for_termination()`). Consistent with Phase 2 pattern.

3. **Test 9 patching strategy**: `patch.object(orchestrator_grpc_mod, "release_stock", ...)` patches the name in the grpc_server module's namespace (where it was imported). `asyncio.sleep` patched to `instant_sleep` to avoid multi-second test delays from exponential backoff.

4. **Test 10 replay approach**: Rather than resetting SAGA state (which `run_checkout` would reject as "already in progress"), directly calls `reserve_stock`/`charge_payment` with the same deterministic idempotency keys. This directly validates that Phase 2 Lua caching returns cached results on duplicate keys.

5. **Unique IDs per test**: Each test generates UUID-based item_id/user_id/order_id. Combined with `clean_orchestrator_db` for SAGA records, this provides strong test isolation without requiring session-level cleanup of the stock/payment Redis db.

## Deviations from Plan

None — plan executed exactly as written. All 10 tests pass on first run.

## Self-Check

Files created/modified:
- tests/test_saga.py (created, 257 lines)
- tests/conftest.py (modified, orchestrator fixtures added)

Commits:
- 58c74d6: feat(03-04): add orchestrator fixtures to conftest.py
- 494435c: feat(03-04): write SAGA integration tests (10 tests, all passing)
