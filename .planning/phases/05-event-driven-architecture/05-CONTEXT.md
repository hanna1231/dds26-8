# Phase 5: Event-Driven Architecture - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

SAGA lifecycle events are published to Redis Streams with consumer groups; compensation retries are queued reliably for at-least-once processing. This phase adds event publishing to the existing SAGA orchestrator and creates consumer groups for compensation handling and audit logging. New capabilities (monitoring dashboards, alerting, external integrations) belong in other phases.

</domain>

<decisions>
## Implementation Decisions

### Event Granularity
- Every SAGA state change produces an event: checkout_started, stock_reserved, stock_failed, payment_started, payment_completed, payment_failed, compensation_triggered, compensation_completed, saga_completed, saga_failed
- Rich payloads: saga_id, event_type, timestamp, step details (service, action, result), order context (order_id, user_id, amounts)
- Each event carries a schema version field (e.g. v1) for forward compatibility
- Compensation events include failure context: failed_step, error_type, retry_count

### Stream Topology
- Single stream per saga type (e.g. saga:checkout:events) — all lifecycle events in one stream, consumers filter by event_type
- One consumer group per concern: compensation-handler and audit-logger
- Stream entries trimmed with XADD MAXLEN ~10000 (approximate trimming for performance)

### Retry & Dead Letters
- Exponential backoff with jitter for compensation retries (1s, 2s, 4s, 8s...)
- Max 5 retry attempts before giving up (~31s total)
- Permanently failed compensations moved to a saga:dead-letters stream for manual inspection/replay
- XCLAIM after 30s idle timeout to reclaim unacknowledged messages from crashed consumers

### Non-Blocking Design
- Fire-and-forget XADD during SAGA step transitions — if publish fails, log warning but don't fail checkout
- Consumers run as async background tasks within the FastAPI app (not separate worker processes)
- On Redis unavailability: silent drop with dropped_events metric counter, checkout continues unaffected
- Graceful shutdown: on SIGTERM/app shutdown, finish processing current message, stop reading new ones
- Basic health endpoint exposing consumer lag and dead letter count via existing health check

### Claude's Discretion
- Event serialization format (JSON vs msgpack)
- Exact consumer polling interval and batch size
- Redis connection pooling strategy for stream operations
- Internal event bus abstraction (if any)

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

*Phase: 05-event-driven-architecture*
*Context gathered: 2026-02-28*
