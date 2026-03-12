# Feature Research

**Domain:** 2PC transaction coordination and Redis Streams request/reply messaging for distributed checkout
**Researched:** 2026-03-12
**Confidence:** HIGH (2PC is a well-understood protocol; Redis Streams request/reply is application-level but well-documented)

## Context: What Already Exists (v1.0)

All v1.0 features are shipped and validated with 0 consistency violations. This research covers ONLY the new v2.0 features:
- **2PC** as an alternative to SAGA (env var switchable)
- **Redis Streams message queues** as default inter-service communication (replacing gRPC as default, gRPC kept as fallback)

Existing v1.0 components this builds on: SAGA orchestrator, gRPC Stock/Payment calls with idempotency, Redis Streams events with consumer groups, circuit breakers, Lua atomic operations, startup recovery, kill-test consistency.

---

## Feature Landscape

### Table Stakes (Required for v2.0 Milestone)

Features the milestone explicitly requires. Missing any = incomplete milestone.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| 2PC state machine with Lua CAS | Core protocol: PREPARING, PREPARED, COMMITTING, COMMITTED, ABORTING, ABORTED. Without explicit states, cannot reason about crash recovery. | MEDIUM | Parallels `saga.py`. Reuse Lua CAS transition pattern. New module `twopc.py`. |
| 2PC coordinator flow in orchestrator | Orchestrator drives prepare-all-then-commit-or-abort. PROJECT.md requires "Orchestrator as 2PC coordinator." | MEDIUM | New `run_2pc_checkout()` alongside existing `run_checkout()`. Same retry/compensation infrastructure. |
| 2PC participant logic in Stock/Payment | Participants must handle PREPARE (tentatively lock resources), COMMIT (finalize), ABORT (release). Currently Stock/Payment do immediate reserve/charge in one step. 2PC splits this into two phases. | HIGH | Hardest feature. Current Lua scripts do atomic reserve-in-one-step. 2PC needs: (1) PREPARE = write tentative reservation with flag, (2) COMMIT = mark tentative as final, (3) ABORT = delete tentative record. New Lua scripts. |
| 2PC transaction log persistence | Coordinator must persist decision before sending phase-2 messages. Classic 2PC requirement: if coordinator crashes after deciding COMMIT but before notifying participants, recovery replays the decision. | MEDIUM | Redis hash `{2pc:<order_id>}` storing state, participants, votes, decision. Same pattern as `{saga:<order_id>}`. |
| 2PC timeout and abort on failure | If any participant votes NO or times out during prepare, coordinator ABORTs all. Fundamental 2PC safety guarantee. | MEDIUM | Reuse `retry_forward` bounded retry. On failure, send ABORT to all participants that received PREPARE. |
| 2PC recovery scanner | On startup, scan for incomplete 2PC transactions and drive to terminal state. Coordinator crash between PREPARED and COMMITTED is THE classic 2PC failure. | MEDIUM | Extend `recovery.py` pattern. If decision was COMMIT, replay commit. If no decision logged, ABORT (presumed-abort). |
| 2PC idempotency | Duplicate PREPARE/COMMIT/ABORT must be safe. Coordinator retries must not cause double-reservations. | MEDIUM | Reuse idempotency key pattern. `{2pc:<order_id>}:prepare:<service>`, `{2pc:<order_id>}:commit:<service>`. |
| Redis Streams request/reply messaging | Replace synchronous gRPC with async message-based communication. Orchestrator publishes request to service's request stream, service reads and publishes reply to reply stream. | HIGH | No built-in request/reply in Redis Streams. Must implement: correlation IDs, reply routing, timeout handling. |
| Correlation ID for request/reply | Each request needs unique correlation ID. Reply includes same ID so caller matches response to request. Without this, concurrent requests are indistinguishable. | MEDIUM | Use `order_id:step` as correlation ID (same pattern as idempotency keys). Caller blocks on XREAD with correlation ID filter. |
| Reply timeout handling | Caller must not block forever waiting for reply. Configurable timeout per request. | LOW | XREAD with BLOCK timeout. If timeout expires, treat as failure. Integrate with circuit breaker. |
| `TRANSACTION_MODE` env var toggle | PROJECT.md: "env var toggle alongside SAGA." Must switch between SAGA and 2PC. | LOW | `TRANSACTION_MODE=saga` (default) or `TRANSACTION_MODE=2pc`. Orchestrator checkout dispatches accordingly. |
| `COMM_MODE` env var toggle | PROJECT.md: "gRPC kept as fallback communication path (env var toggle)." | LOW | `COMM_MODE=grpc` (fallback) or `COMM_MODE=queue` (default). `client.py` switches transport. |
| Consumer group per service for requests | Each Stock/Payment instance joins consumer group on its request stream. Load-balances requests across replicas. | LOW | Existing `consumers.py` pattern applies directly. XREADGROUP with `>`, XAUTOCLAIM for stuck messages. |

### Differentiators (Grade Boosters)

Beyond minimum requirements; demonstrate deeper understanding.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Unified test suite (SAGA/2PC x gRPC/queue) | Run same benchmark and consistency tests in all 4 mode combinations. Proves both patterns achieve 0 consistency violations. Very high grade value. | LOW | Parameterize existing 37 integration tests + kill-tests with env vars. Minimal code, maximum proof. |
| 2PC lifecycle events | Publish 2PC events to Redis Streams audit trail. Same pattern as SAGA events in `events.py`. | LOW | New event types: `2pc_prepare_sent`, `2pc_vote_yes`, `2pc_vote_no`, `2pc_committed`, `2pc_aborted`. |
| Presumed-abort optimization | If coordinator has no logged COMMIT decision for a transaction, presume ABORT. Participants that timeout waiting for phase-2 can safely abort. Reduces Redis writes. | LOW | Standard 2PC optimization. Simplifies recovery: absence of commit record = abort. |
| Dead letter queue for failed requests | Request messages that fail processing N times move to dead-letter stream. Prevents infinite retry loops. | LOW | Copy existing dead-letter pattern from `consumers.py`. |
| Graceful communication mode switching | Allow runtime switching between gRPC and queue modes without restart (hot-swap). | MEDIUM | Probably overkill for course, but would demonstrate deep understanding. Not recommended unless time permits. |

### Anti-Features (Do Not Build)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Distributed locks (Redlock) for 2PC prepare | "Need to lock resources during prepare phase" | Redlock adds latency and known-flawed consensus. Redis Lua scripts already provide atomicity. Tentative reservation via Lua CAS is simpler and faster. | Lua scripts with "tentative" flag for prepared-but-not-committed resources. |
| Full XA transaction protocol | "2PC should use proper XA" | Redis does not support XA. Application-level 2PC with Redis-persisted coordinator state is the correct approach for this stack. | Application-level 2PC with Lua-atomic participant operations. |
| Separate message broker (RabbitMQ/Kafka) | "Redis Streams is not a real message queue" | Adds infra, CPU budget, operational complexity, and new client library. Redis Streams with consumer groups already provides needed semantics. Course constraint is Redis. | Redis Streams with consumer groups, XAUTOCLAIM, dead-letter handling. |
| Blocking resource locks during 2PC | "Participants should hold locks until commit/abort" | Redis has no database-level locks. Simulating blocking locks via key TTL risks deadlocks and stale locks on crashes. | Tentative reservation pattern: PREPARE writes tentative record, COMMIT finalizes, ABORT deletes. No blocking needed. |
| Two-way streaming for message queue | "Bidirectional channels for richer communication" | Overengineered for checkout. Hard to reason about ordering and backpressure. | Simple request stream + reply stream with correlation IDs. |
| 2PC with blocking participants | "True 2PC blocks participants until coordinator decides" | In a Redis-backed system, blocking means resource starvation. Tentative-write + async commit achieves same safety without blocking threads. | Tentative writes: PREPARE writes data marked as "tentative", reads skip tentative data, COMMIT removes the flag, ABORT deletes. |

---

## Feature Dependencies

```
[TRANSACTION_MODE env var toggle]
    └──requires──> [2PC coordinator flow]
                       └──requires──> [2PC state machine]
                       └──requires──> [2PC participant logic (Stock + Payment)]
                       └──requires──> [2PC transaction log persistence]
                       └──requires──> [2PC timeout/abort handling]
                       └──requires──> [2PC idempotency]

[2PC recovery scanner]
    └──requires──> [2PC transaction log persistence]
    └──requires──> [2PC participant logic]

[COMM_MODE env var toggle]
    └──requires──> [Redis Streams request/reply messaging]
                       └──requires──> [Correlation ID matching]
                       └──requires──> [Reply timeout handling]
                       └──requires──> [Consumer group per service]

[Unified test suite]
    └──requires──> [TRANSACTION_MODE toggle]
    └──requires──> [COMM_MODE toggle]
```

### Dependency Notes

- **2PC coordinator requires participant logic:** Cannot complete PREPARE without participants that understand prepare/commit/abort. Both sides must be built together.
- **Message queue requires correlation IDs:** Without correlation, concurrent requests produce unroutable replies. This is the foundational primitive.
- **Env var toggles require both implementations:** The toggle is trivial (if/else dispatch), but both code paths must exist and be tested.
- **Recovery scanner requires persistence:** Cannot recover what was not persisted. 2PC log must be written before recovery can read it.
- **Test suites require toggles:** Parameterized tests exercise both modes through the same toggle mechanism.

---

## How 2PC Works (Expected Behavior)

### Protocol Phases

**Phase 1 -- Prepare:**
1. Coordinator creates 2PC transaction record in Redis (state: PREPARING)
2. Coordinator sends PREPARE to all participants (Stock, Payment) with transaction details
3. Each participant tentatively executes the operation (reserve stock, hold payment) WITHOUT finalizing
4. Each participant persists its prepared state and votes YES or NO
5. Coordinator collects all votes

**Phase 2 -- Commit or Abort:**
- If ALL votes are YES: coordinator logs COMMITTING decision, sends COMMIT to all participants, participants finalize tentative operations, coordinator logs COMMITTED
- If ANY vote is NO (or timeout): coordinator logs ABORTING, sends ABORT to all participants, participants release tentative operations, coordinator logs ABORTED

### Key Difference from SAGA

| Aspect | SAGA (v1.0) | 2PC (v2.0) |
|--------|-------------|------------|
| Execution | Sequential: reserve stock, then charge payment | Parallel prepare: prepare stock AND prepare payment simultaneously |
| Failure handling | Compensating transactions (undo what was done) | Abort (nothing was finalized, just release tentative holds) |
| Atomicity guarantee | Eventual consistency via compensation | Strong consistency via all-or-nothing commit |
| Blocking | Non-blocking (compensations run async) | Potentially blocking (participants wait for phase-2) |
| Recovery complexity | Resume from any step, compensate backward | Simple: if COMMIT logged, replay commits; if not, abort |

### Tentative Reservation Pattern for Redis

Since Redis has no native prepare/commit, 2PC participants must implement tentative operations:

**Stock PREPARE:** Write `{item:<id>}:tentative:{tx_id}` with reserved quantity. Do NOT decrement actual stock yet. Lua script atomically checks available stock (actual minus sum of tentative) and writes tentative record.

**Stock COMMIT:** Atomically decrement actual stock AND delete tentative record. Lua script does both in one EVAL.

**Stock ABORT:** Delete tentative record. Lua script removes the key.

**Payment PREPARE/COMMIT/ABORT:** Same pattern with `{user:<id>}:tentative:{tx_id}`.

---

## How Redis Streams Request/Reply Works (Expected Behavior)

### Message Flow

```
Orchestrator                    Stock Service
    |                               |
    |-- XADD {stock:requests} -->   |
    |   {correlation_id, action,    |
    |    item_id, quantity, ...}     |
    |                               |-- XREADGROUP (consumer group)
    |                               |-- process request
    |                               |-- XADD {stock:replies} -->
    |                               |   {correlation_id, success, ...}
    |<-- XREAD {stock:replies} --   |
    |   (filter by correlation_id)  |
```

### Key Design Decisions

**Request streams:** One per service type: `{stock:requests}`, `{payment:requests}`. Consumer groups distribute requests across service replicas.

**Reply streams:** One per service type: `{stock:replies}`, `{payment:replies}`. Orchestrator polls with XREAD BLOCK, filtering by correlation ID.

**Correlation ID:** `{order_id}:{step}` -- natural, unique, matches existing idempotency key pattern.

**Timeout:** XREAD BLOCK with configurable timeout (e.g., 5 seconds, matching current `RPC_TIMEOUT`). Timeout = same handling as gRPC timeout.

**Idempotency:** Same idempotency_key mechanism as gRPC path. Participant checks idempotency before processing, returns cached result if already processed.

### Integration with `client.py`

Current `client.py` has functions like `reserve_stock(item_id, quantity, idempotency_key)`. The interface stays identical. Under the hood:
- `COMM_MODE=grpc`: call gRPC stub (current behavior)
- `COMM_MODE=queue`: XADD to request stream, XREAD reply stream with correlation ID, parse response

This keeps the SAGA and 2PC coordinators transport-agnostic.

---

## Existing v1.0 Components: Reuse Analysis

| v1.0 Component | Reuse for 2PC | Reuse for Message Queues |
|----------------|---------------|--------------------------|
| `saga.py` Lua CAS transitions | YES -- same Lua pattern, different state names | NO |
| `client.py` gRPC wrappers | PARTIAL -- need new RPCs for prepare/commit/abort | YES -- abstract same interface, swap transport |
| `events.py` fire-and-forget | YES -- publish 2PC lifecycle events | NO |
| `consumers.py` consumer groups | NO | YES -- same XREADGROUP/XAUTOCLAIM for request processing |
| `recovery.py` startup scanner | YES -- extend for 2PC record scanning | NO |
| `circuit.py` circuit breakers | YES -- wrap 2PC calls | YES -- wrap queue timeouts |
| Stock/Payment Lua scripts | PARTIAL -- idempotency pattern reusable, need new tentative scripts | NO direct reuse |
| Stock/Payment gRPC servicers | NO -- need new RPCs | YES -- handlers reusable, called from queue consumer |
| Redis Cluster hash tags | YES -- `{2pc:<order_id>}` | YES -- `{stock:requests}` |

---

## MVP Definition

### Build First: Core 2PC

Minimum to demonstrate 2PC as alternative transaction pattern.

- [ ] `twopc.py` -- 2PC state machine with Lua CAS transitions
- [ ] Stock participant: PrepareReserve/CommitReserve/AbortReserve (Lua scripts for tentative reservation)
- [ ] Payment participant: PrepareCharge/CommitCharge/AbortCharge (Lua scripts for tentative hold)
- [ ] `run_2pc_checkout()` in orchestrator -- prepare all, then commit or abort
- [ ] 2PC transaction log in Redis hash -- crash safety
- [ ] `TRANSACTION_MODE` env var toggle
- [ ] 2PC recovery scanner

### Build Second: Message Queues

Replace gRPC with Redis Streams request/reply.

- [ ] Request/reply transport layer in `client.py` -- same function signatures, queue transport
- [ ] Request stream per service (`{stock:requests}`, `{payment:requests}`)
- [ ] Reply stream with correlation IDs (`{stock:replies}`, `{payment:replies}`)
- [ ] Consumer loop in Stock/Payment reading request stream, dispatching to existing handlers
- [ ] `COMM_MODE` env var toggle
- [ ] Timeout and circuit breaker integration

### Build Third: Validation

Prove everything works under benchmark.

- [ ] Parameterized integration tests (SAGA/2PC x gRPC/queue)
- [ ] Kill-test in 2PC + queue mode
- [ ] Benchmark with 0 consistency violations in all 4 mode combinations

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| 2PC state machine + Lua CAS | HIGH | MEDIUM | P1 |
| 2PC participant logic (Stock + Payment) | HIGH | HIGH | P1 |
| 2PC coordinator flow | HIGH | MEDIUM | P1 |
| 2PC transaction log persistence | HIGH | LOW | P1 |
| 2PC recovery scanner | HIGH | MEDIUM | P1 |
| TRANSACTION_MODE env var toggle | HIGH | LOW | P1 |
| Redis Streams request/reply | HIGH | HIGH | P1 |
| Correlation ID matching | HIGH | MEDIUM | P1 |
| COMM_MODE env var toggle | HIGH | LOW | P1 |
| Reply timeout handling | MEDIUM | LOW | P1 |
| Consumer group per service | MEDIUM | LOW | P2 |
| Unified test suite (4 combinations) | HIGH | LOW | P2 |
| 2PC event publishing | LOW | LOW | P2 |
| Presumed-abort optimization | LOW | LOW | P3 |
| Dead letter queue for requests | LOW | LOW | P3 |

**Priority key:**
- P1: Must have for v2.0 milestone
- P2: Should have, add when core is working
- P3: Nice to have, defer if time is short

---

## Sources

- [Martin Fowler - Two-Phase Commit](https://martinfowler.com/articles/patterns-of-distributed-systems/two-phase-commit.html) -- authoritative pattern description
- [Two-Phase Commit Wikipedia](https://en.wikipedia.org/wiki/Two-phase_commit_protocol) -- protocol specification
- [2PC in Microservices - DEV Community](https://dev.to/ovichowdhury/demystifying-two-phase-commit-2pc-for-distributed-transaction-in-microservices-5ca7) -- application-level implementation
- [SAGA vs 2PC - GeeksforGeeks](https://www.geeksforgeeks.org/system-design/difference-between-saga-pattern-and-2-phase-commit-in-microservices/) -- pattern comparison
- [Redis Streams Docs](https://redis.io/docs/latest/develop/data-types/streams/) -- official stream documentation
- [Redis Blog: Sync and Async Communication](https://redis.io/blog/what-to-choose-for-your-synchronous-and-asynchronous-communication-needs-redis-streams-redis-pub-sub-kafka-etc-best-approaches-synchronous-asynchronous-communication/) -- Redis messaging patterns
- [redis-py Async Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) -- async Redis Streams usage
- [Redis Streams Wrong Assumptions](https://redis.io/blog/youre-probably-thinking-about-redis-streams-wrong/) -- stream entry structure clarification

---
*Feature research for: 2PC and Redis Streams message queues for distributed checkout*
*Researched: 2026-03-12*
