# Phase 1: Async Foundation - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Migrate all three domain services (Order, Stock, Payment) from Flask+Gunicorn to Quart+Uvicorn with async Redis (`redis.asyncio` + hiredis). All existing HTTP routes and response formats must be preserved identically. No new capabilities — pure framework migration.

</domain>

<decisions>
## Implementation Decisions

### Async HTTP client
- Claude's choice on library (httpx, aiohttp, or similar) — this is temporary, replaced by gRPC in Phase 2
- Checkout flow remains sequential (subtract stock → pay → confirm), not concurrent
- Use a shared async HTTP client per service (connection pooling, initialized at startup, closed on shutdown)

### Uvicorn configuration
- Claude's discretion on worker model (single process vs multiple workers) and timeout settings
- Claude's discretion on per-service vs shared requirements.txt — decide based on dependency differences (Order needs async HTTP client, others don't)

### Code structure
- Keep single `app.py` per service — no module splitting (services are small, Phase 2+ adds structure naturally)
- Minimal diff: swap Flask imports for Quart, add `async def` to route handlers, no style changes or type hint additions
- Keep msgspec for serialization (Struct models, msgpack encoding) — no change
- Claude's discretion on Redis client lifecycle (module-level vs Quart before_serving/after_serving hooks)

### Error & response format
- Preserve exact mix of JSON (`jsonify`) and plain text (`Response`) per endpoint — no standardization
- Claude investigates what tests/benchmark actually check and matches error format accordingly
- Keep `__main__` dev mode with Quart/Uvicorn equivalent for local development outside Docker
- Claude's discretion on logging — swap gunicorn logger references for uvicorn equivalents, no format changes

### Claude's Discretion
- Async HTTP client library choice (temporary bridge to Phase 2 gRPC)
- Uvicorn worker model and timeout configuration
- Redis client initialization pattern (module-level vs app lifecycle hooks)
- Requirements.txt organization (shared vs per-service)
- Logging setup (uvicorn equivalent of current gunicorn logger)
- Error response format strictness (based on test suite analysis)

</decisions>

<specifics>
## Specific Ideas

- User noted inter-service calls should eventually use message queues — that's Phase 5 (Redis Streams). Phase 2 handles gRPC migration first.
- Sequential checkout flow is intentional — SAGA orchestrator in Phase 3 will redesign coordination.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-async-foundation*
*Context gathered: 2026-02-28*
