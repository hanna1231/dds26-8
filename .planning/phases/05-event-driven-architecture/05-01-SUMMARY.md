---
phase: 05-event-driven-architecture
plan: 01
subsystem: orchestrator
tags: [redis-streams, event-driven, consumer-groups, saga, audit-logging, compensation]
dependency_graph:
  requires: [orchestrator/grpc_server.py, orchestrator/app.py, orchestrator/saga.py]
  provides: [orchestrator/events.py, orchestrator/consumers.py]
  affects: [orchestrator/grpc_server.py, orchestrator/app.py]
tech_stack:
  added: [redis-streams, msgspec.json]
  patterns: [fire-and-forget publishing, consumer-group processing, XAUTOCLAIM, XREADGROUP, dead-letter queue]
key_files:
  created:
    - orchestrator/events.py
    - orchestrator/consumers.py
  modified:
    - orchestrator/grpc_server.py
    - orchestrator/app.py
decisions:
  - "publish_event() is fire-and-forget — never raises, drops and counts on Redis failure so checkout is never blocked"
  - "XAUTOCLAIM used for idle message reclaim (Redis 6.2+ modern approach, not XCLAIM+XPENDING)"
  - "Lazy imports for run_compensation and get_saga in _handle_compensation_message prevent circular imports"
  - "g['name'] from xinfo_groups returns decoded strings (redis-py parse_list_of_dicts uses decode_keys=True)"
  - "XPENDING_RANGE key is 'times_delivered' (verified from redis-py source, not 'delivery_count')"
metrics:
  duration: "374 seconds"
  completed_date: "2026-02-28"
  tasks_completed: 2
  files_modified: 4
---

# Phase 5 Plan 01: Redis Streams Event Publishing and Consumer Groups Summary

**One-liner:** Fire-and-forget XADD publishing on every SAGA transition with XAUTOCLAIM+XREADGROUP consumer groups for compensation retry and audit logging, dead-lettering after 5 delivery attempts.

## What Was Built

### orchestrator/events.py

`publish_event()` wraps `db.xadd()` in try/except and never raises. On any failure it increments a module-level `_dropped_events` counter and logs a warning. The checkout path is never blocked.

`_build_event()` constructs rich payloads with `schema_version=v1`, `event_type`, `saga_id`, `order_id`, `user_id`, `timestamp`, plus arbitrary extra fields. Non-string extras are JSON-encoded via `msgspec.json`.

Stream uses `MAXLEN ~10000` with approximate trimming for performance.

### orchestrator/consumers.py

Two consumer groups:
- **compensation-handler**: XAUTOCLAIM reclaims messages idle >30s from crashed consumers. XREADGROUP reads new messages. `_handle_compensation_message` filters for `compensation_triggered` events, checks delivery count via XPENDING_RANGE, dead-letters to `saga:dead-letters` stream after 5 attempts, otherwise invokes `run_compensation` for SAGAs still in COMPENSATING state.
- **audit-logger**: XREADGROUP reads all events, logs `SAGA_EVENT {type} order={id}`, ACKs immediately (best-effort).

Both consumers:
- Block 2000ms on XREADGROUP to avoid CPU spin
- Re-raise `asyncio.CancelledError` for clean Quart shutdown
- Stop when `_stop_event` is set

### orchestrator/grpc_server.py (modified)

11 `publish_event` calls covering the full SAGA lifecycle:

| Event | Trigger |
|-------|---------|
| checkout_started | After create_saga_record succeeds |
| stock_reserved | After STARTED→STOCK_RESERVED transition |
| stock_failed | When stock reservation fails |
| compensation_triggered | Before run_compensation (stock fail path) |
| payment_completed | After STOCK_RESERVED→PAYMENT_CHARGED transition |
| payment_failed | When payment charge fails |
| compensation_triggered | Before run_compensation (payment fail path) |
| compensation_triggered | In CircuitBreakerError handler |
| saga_completed | After PAYMENT_CHARGED→COMPLETED transition |
| compensation_completed | After COMPENSATING→FAILED transition in run_compensation |
| saga_failed | After COMPENSATING→FAILED transition in run_compensation |

Compensation events carry `failed_step`, `error_type`, `retry_count` in their payloads.

### orchestrator/app.py (modified)

Startup sequence (in correct order):
1. Initialize Redis connection
2. Initialize gRPC clients
3. Run recovery scan
4. Set up consumer groups (idempotent, BUSYGROUP-safe)
5. Initialize stop event
6. Start gRPC server background task
7. Start compensation_consumer background task
8. Start audit_consumer background task

Shutdown signals `_stop_event` before stopping gRPC server.

Health endpoint now returns `consumer_lag` (per consumer group), `dead_letters` (count from saga:dead-letters), and `dropped_events`.

## Deviations from Plan

None — plan executed exactly as written.

## Commits

| Task | Hash | Description |
|------|------|-------------|
| Task 1 | fd36122 | feat(05-01): add Redis Streams event publishing and consumer group modules |
| Task 2 | d070305 | feat(05-01): wire event publishing into SAGA transitions and consumer lifecycle |

## Self-Check: PASSED

- orchestrator/events.py: FOUND
- orchestrator/consumers.py: FOUND
- Commit fd36122: FOUND
- Commit d070305: FOUND
