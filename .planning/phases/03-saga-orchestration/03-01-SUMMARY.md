---
phase: 03-saga-orchestration
plan: "01"
subsystem: orchestrator
tags: [grpc, protobuf, saga, state-machine, redis, lua]
dependency_graph:
  requires: []
  provides: [orchestrator-proto, saga-state-machine]
  affects: [orchestrator, order]
tech_stack:
  added: [grpc_tools.protoc]
  patterns: [proto3-repeated-message, lua-cas-transition, hsetnx-idempotency, manual-byte-decoding]
key_files:
  created:
    - protos/orchestrator.proto
    - orchestrator/orchestrator_pb2.py
    - orchestrator/orchestrator_pb2.pyi
    - orchestrator/orchestrator_pb2_grpc.py
    - orchestrator/saga.py
    - order/orchestrator_pb2.py
    - order/orchestrator_pb2.pyi
    - order/orchestrator_pb2_grpc.py
  modified: []
decisions:
  - "orchestrator_pb2 stubs use absolute imports (import orchestrator_pb2) consistent with Phase 2 convention for services run from their own directory"
  - "HSETNX used for atomic SAGA creation — prevents duplicate saga records under concurrent requests"
  - "Lua CAS (TRANSITION_LUA) validates from_state before updating state field — same pattern as Phase 2 IDEMPOTENCY_ACQUIRE_LUA"
  - "Manual byte decoding in get_saga (k.decode()/v.decode()) rather than decode_responses=True — consistent with existing codebase"
  - "set_saga_error exported alongside create/transition/get — provides targeted error field update without state change"
metrics:
  duration: "93 seconds"
  completed_date: "2026-02-28"
  tasks_completed: 2
  files_created: 8
  files_modified: 0
---

# Phase 3 Plan 01: Orchestrator Proto and SAGA State Machine Summary

**One-liner:** Proto3 StartCheckout RPC with repeated LineItem, Lua CAS SAGA state machine (6 states, 7 transitions) persisted to Redis hashes with HSETNX idempotency.

## What Was Built

### Task 1: orchestrator.proto and generated stubs

`protos/orchestrator.proto` defines:
- `service OrchestratorService` with `rpc StartCheckout`
- `message LineItem { string item_id; int32 quantity }` — supports multi-item orders
- `message CheckoutRequest` with `repeated LineItem items` (matches `OrderValue.items: list[tuple[str, int]]`)
- `message CheckoutResponse { bool success; string error_message }`

Python stubs generated via `python3 -m grpc_tools.protoc` and committed to both `orchestrator/` and `order/` so Order service can call StartCheckout without a separate codegen step.

### Task 2: orchestrator/saga.py

SAGA state machine with Redis hash persistence:

**VALID_TRANSITIONS** (4 source states, 7 valid paths):
- STARTED -> {STOCK_RESERVED, COMPENSATING}
- STOCK_RESERVED -> {PAYMENT_CHARGED, COMPENSATING}
- PAYMENT_CHARGED -> {COMPLETED, COMPENSATING}
- COMPENSATING -> {FAILED}

**TRANSITION_LUA** — Lua CAS pattern: reads current `state` from hash, aborts if mismatch, writes new state + `updated_at` atomically. Optional `flag_field`/`flag_value` pair set in same atomic operation (e.g. `stock_reserved=1`).

**Exported functions:**
- `create_saga_record(db, order_id, user_id, items, total_cost) -> bool` — HSETNX on `state` field, sets all fields + 7-day TTL
- `transition_state(db, saga_key, from_state, to_state, flag_field, flag_value) -> bool` — validates transition then calls Lua CAS
- `get_saga(db, order_id) -> dict | None` — HGETALL with manual byte decoding
- `set_saga_error(db, order_id, error_message)` — sets error_message field without state change

**Hash schema** (`saga:{order_id}`):
- state, order_id, user_id, total_cost, items_json, stock_reserved, payment_charged, refund_done, stock_restored, error_message, started_at, updated_at

## Verification Results

All success criteria passed:
- Proto defines StartCheckout with repeated LineItem
- Stubs importable from orchestrator/ and order/
- SAGA record creation atomic via HSETNX (returns False if duplicate)
- State transitions atomic via Lua CAS (returns False if stale state)
- All 6 SAGA states and 7 valid transitions defined

## Deviations from Plan

None — plan executed exactly as written.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 9a56b62 | feat(03-01): define orchestrator.proto and generate Python stubs |
| 2 | 8fc959b | feat(03-01): implement SAGA state machine module (saga.py) |

## Self-Check: PASSED

All 8 created files found on disk. Both commits (9a56b62, 8fc959b) verified in git log.
