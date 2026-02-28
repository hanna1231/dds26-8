# Phase 4: Fault Tolerance - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

The system remains consistent when any single container is killed mid-transaction. Incomplete SAGAs resume on orchestrator restart. Cascade failures are contained by circuit breakers. Covers FAULT-01 through FAULT-04.

This phase does NOT add new capabilities (events, infrastructure scaling, etc.) — it hardens the existing SAGA orchestration from Phase 3.

</domain>

<decisions>
## Implementation Decisions

### SAGA recovery on restart
- Resume forward first: on orchestrator startup, attempt to complete incomplete SAGAs from where they left off; only compensate if forward progress fails
- Startup scan blocks new checkouts until all stale SAGAs are resolved (no background recovery)
- Detailed logging for each recovered SAGA: ID, state found, action taken (resumed/compensated), outcome

### Circuit breaker policy
- Per-service circuit breakers: Stock and Payment each have independent breakers
- When tripped, return 503 Service Unavailable to the caller
- Half-open probe recovery: after cooldown, allow one test request through; close breaker on success
- SAGA in progress when breaker trips: compensate the SAGA (don't leave it hanging)

### Kill-recovery behavior
- Services (Stock, Payment) are stateless — container restarts and immediately serves requests; orchestrator handles SAGA recovery
- Docker restart policy (`restart: always` or `on-failure`) for automatic container restart
- Consistency verification lives in integration tests, not built into the system
- Fault tolerance tests use real Docker container kills (`docker kill`), not application-level simulation

### Retry & backoff strategy
- Forward SAGA steps (reserve stock, charge payment): max 3 retries before giving up and compensating
- Compensation steps (refund payment, restore stock): retry indefinitely until success (per SAGA-05 requirement)
- Backoff curve: exponential with random jitter to avoid thundering herd
- Max backoff cap: 30 seconds

### Claude's Discretion
- SAGA staleness timeout threshold (how old before compensate instead of resume)
- Circuit breaker failure threshold (number of consecutive failures to trip)
- Circuit breaker cooldown duration
- Exact jitter algorithm and initial backoff interval
- Internal implementation patterns (where circuit breaker state lives, retry loop structure)

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. The success criteria from the roadmap are the key benchmark: kill any container, recover, and verify no money or stock is lost.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-fault-tolerance*
*Context gathered: 2026-02-28*
