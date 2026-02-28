# Phase 2: gRPC Communication - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Add gRPC servers to Stock and Payment services alongside existing HTTP; define proto contracts with idempotency keys for all inter-service mutation calls. The SAGA orchestrator itself is Phase 3 — this phase delivers the gRPC infrastructure and a thin client layer it will use.

</domain>

<decisions>
## Implementation Decisions

### Proto contract scope
- SAGA-only RPCs: define only operations the orchestrator needs (Stock: reserve, release, check; Payment: charge, refund, check)
- One proto file per service: `stock.proto` and `payment.proto`
- Proto files live in a top-level `protos/` directory at the repo root; generated Python stubs go into each service
- Minimal message fields: business data + idempotency_key only; tracing/timestamps go in gRPC metadata headers, not proto fields

### Idempotency key design
- Composite key format: `saga:{saga_id}:step:{step_name}` — deterministic, debuggable, tied to SAGA lifecycle
- Orchestrator generates all idempotency keys; services only receive and deduplicate
- Idempotency records stored in Redis with TTL (e.g., 24h) — auto-expires, no cleanup needed
- Deduplication happens at the Lua script level: atomically check idempotency_key + execute operation in a single Redis call (no TOCTOU race)

### gRPC error handling
- Business errors communicated via response status fields (success bool + error_message string), not gRPC status codes
- gRPC status codes reserved for transport/system errors only
- On duplicate idempotency key, return the stored result transparently — caller can't distinguish retry from first call
- Minimal error info: just success/fail + error message, no internal state leakage (stock levels, balances)
- Simple per-call deadline (e.g., 5s) for timeouts; no client-side retry logic (Phase 4 adds circuit breakers)

### Orchestrator scope in this phase
- Create a thin gRPC client layer: async wrapper functions (reserve_stock, charge_payment, etc.) in an `orchestrator/` directory
- No orchestrator service in this phase — just the client module that Phase 3 imports
- All existing HTTP endpoints on Stock and Payment remain fully functional; gRPC is inter-service only
- Include basic integration tests (client → gRPC server → Redis) to prove wiring works

### Claude's Discretion
- Exact proto field types and naming conventions
- gRPC server startup integration with Quart (asyncio event loop sharing)
- Generated stub output directory structure
- Test fixture design and setup/teardown approach
- Specific deadline value (5s suggested, Claude can adjust)

</decisions>

<specifics>
## Specific Ideas

- Dual-server on each service: HTTP on :5000, gRPC on :50051
- Proto stubs generated from shared `protos/` dir — single source of truth for contracts
- Integration tests should spin up the gRPC server and make real calls, not just mock

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-grpc-communication*
*Context gathered: 2026-02-28*
