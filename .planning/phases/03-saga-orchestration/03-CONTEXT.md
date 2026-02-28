# Phase 3: SAGA Orchestration - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

A dedicated SAGA orchestrator coordinates checkout with Redis-persisted state, idempotent service operations, and retry-until-success compensation. The orchestrator is a separate service that Stock and Payment interact with via gRPC. Crash recovery and circuit breakers are Phase 4; event streaming is Phase 5.

</domain>

<decisions>
## Implementation Decisions

### Checkout response model
- Synchronous: checkout endpoint blocks until SAGA reaches COMPLETED or FAILED
- On failure/compensation: return error with reason (e.g., "insufficient stock", "payment declined") and that compensation completed
- Response contains final outcome only — no SAGA state transitions exposed to caller
- Order service's existing /checkout HTTP endpoint stays; internally proxies to orchestrator via gRPC

### Compensation behavior
- Compensation persists and retries on recovery — never silently dropped, never gives up
- Per-step tracking within the SAGA record (e.g., refund_done=true, stock_restored=false) to enable resuming partial compensation
- Only undo steps that actually completed — if payment was never charged, don't attempt refund
- Compensation runs in reverse order: refund payment → restore stock → mark failed

### Orchestrator boundary
- Separate service with its own container and gRPC port
- Order service proxies /checkout to orchestrator via gRPC (external HTTP API unchanged)
- Dedicated Redis instance for the orchestrator (not shared with domain services)
- Positions well for Phase 6 Redis Cluster per-domain work

### Claude's Discretion
- Duplicate checkout handling: return original result vs 409 Conflict (pick what fits existing API patterns)
- Compensation retry backoff parameters (timing, ceiling, total budget)
- SAGA record TTL for completed/failed records
- Staleness timeout for in-progress SAGAs (timeout-based vs startup-only recovery)
- Timestamp granularity in SAGA records (per-transition vs start/end only)
- Redis key structure (single hash vs namespaced keys)
- Orchestrator gRPC API surface beyond StartCheckout (whether to include GetSagaStatus)

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-saga-orchestration*
*Context gathered: 2026-02-28*
