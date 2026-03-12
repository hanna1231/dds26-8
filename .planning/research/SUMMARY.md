# Project Research Summary

**Project:** DDS26-8 v2.0 -- 2PC & Message Queues
**Domain:** Distributed transaction coordination and async inter-service messaging for microservices checkout
**Researched:** 2026-03-12
**Confidence:** HIGH

## Executive Summary

DDS26-8 v2.0 adds two orthogonal capabilities to an already-proven distributed checkout system: Two-Phase Commit (2PC) as an alternative to the existing SAGA transaction pattern, and Redis Streams request/reply messaging as a replacement for gRPC inter-service calls. The critical insight from research is that **zero new dependencies are needed** -- both features are application-level patterns built on the same redis-py, msgspec, and Lua CAS primitives already proven in v1.0. This is purely an application-code effort on existing infrastructure.

The recommended approach is to treat these as two independent axes controlled by environment variables (`TRANSACTION_PATTERN=saga|2pc` and `COMM_MODE=grpc|queue`), producing four valid combinations that must all pass the consistency benchmark. The architecture demands a clean layering: extract business logic from gRPC servicers into transport-agnostic `operations.py` modules, build the queue transport layer, then build the 2PC coordinator on top of the transport abstraction. This ordering is non-negotiable because both queue consumers and 2PC participant handlers need to call the same extracted business logic.

The top risks are: (1) 2PC coordinator crash between prepare and commit leaving participants holding resources indefinitely -- mitigated by writing the coordinator decision to Redis BEFORE sending phase-2 messages and extending the recovery scanner; (2) building request/reply correlation over Redis Streams, which is inherently one-directional -- mitigated by using a shared reply stream with asyncio.Future-based correlation rather than per-transaction reply streams; (3) the four-combination test matrix creating blind spots if not explicitly tested -- mitigated by running all existing integration tests against all four mode combinations.

## Key Findings

### Recommended Stack

No changes to the technology stack. All v2.0 features use existing libraries at their current versions. See [STACK.md](STACK.md) for full analysis.

**Core technologies (unchanged):**
- **redis[hiredis] 5.0.3**: 2PC coordinator state via Redis hashes + Lua CAS (same pattern as SAGA), request/reply via XADD/XREADGROUP/XREAD (same API as existing event consumers)
- **msgspec 0.18.6**: JSON serialization for stream message envelopes
- **quart 0.20.0**: Background tasks for queue consumer loops (same `app.add_background_task` mechanism)
- **circuitbreaker 2.1.3**: Same decorator wrapping stream-based call functions instead of gRPC stubs

**Explicitly rejected:** Kafka/RabbitMQ/NATS (Redis Streams sufficient), distributed lock libraries (Lua CAS sufficient), celery/dramatiq (raw stream control needed), new proto definitions for 2PC stream messages.

### Expected Features

See [FEATURES.md](FEATURES.md) for full feature landscape and dependency graph.

**Must have (table stakes for v2.0 milestone):**
- 2PC state machine with Lua CAS transitions (PREPARING, COMMITTING, COMMITTED, ABORTING, ABORTED)
- 2PC participant logic in Stock/Payment (tentative reservation pattern: PREPARE reserves, COMMIT finalizes, ABORT releases)
- 2PC coordinator flow with concurrent prepare via asyncio.gather
- 2PC transaction log persistence and recovery scanner
- Redis Streams request/reply with correlation IDs and timeout handling
- `TRANSACTION_PATTERN` and `COMM_MODE` env var toggles
- Idempotency preserved across both transport modes

**Should have (differentiators):**
- Unified test suite running all 37 tests across all 4 mode combinations
- 2PC lifecycle events published to audit trail
- Presumed-abort optimization for recovery

**Defer:**
- Runtime hot-swap of communication mode (overkill)
- Dead letter queue for failed requests (nice-to-have)
- Graceful degradation from queue to gRPC on failure

### Architecture Approach

The v2.0 architecture adds two orthogonal layers to the existing system through clean abstractions: a transport adapter (Strategy pattern) that selects gRPC or queue communication at startup, and a transaction coordinator selector that dispatches to SAGA or 2PC logic. Business logic extraction into `operations.py` modules is the prerequisite that makes both layers possible. See [ARCHITECTURE.md](ARCHITECTURE.md) for component diagrams and detailed file-level changes.

**Major components:**
1. **Transport Layer** (`transport.py`, `queue_client.py`, `reply_consumer.py`) -- abstracts inter-service communication behind identical function signatures regardless of gRPC or queue mode
2. **2PC Coordinator** (`tpc.py`, `tpc_coordinator.py`, `tpc_recovery.py`) -- state machine, execution flow, and crash recovery mirroring the existing SAGA modules
3. **Queue Consumers** (`stock/queue_consumer.py`, `payment/queue_consumer.py`) -- Redis Streams consumer loops dispatching commands to extracted business logic in `operations.py`
4. **Extracted Business Logic** (`stock/operations.py`, `payment/operations.py`) -- transport-agnostic async functions called by both gRPC servicers and queue consumers

### Critical Pitfalls

See [PITFALLS.md](PITFALLS.md) for all 16 identified pitfalls with detailed prevention strategies.

1. **2PC coordinator crash between phases (P1)** -- Write decision to Redis hash BEFORE sending phase-2 messages. Extend recovery scanner: COMMITTING records replay commit; PREPARING records presume abort. This is THE make-or-break design decision.
2. **Participant PREPARE without actual reservation (P2)** -- PREPARE must perform the real mutation (same Lua script as SAGA forward step). COMMIT is a no-op acknowledgment. ABORT is equivalent to SAGA compensation. Do NOT make PREPARE a read-only check.
3. **Request/reply correlation over Redis Streams (P3)** -- Use a single shared reply stream (`{queue}:orchestrator:replies`) with asyncio.Future map keyed by correlation ID. Background reply consumer resolves Futures. Avoid per-transaction reply streams (key proliferation under load).
4. **Mixed SAGA/2PC state contamination (P4)** -- Separate Redis key namespaces (`{saga:*}` vs `{2pc:*}`), separate transition maps, protocol field on transaction records. Recovery scanner must branch on protocol type.
5. **Lost idempotency in queue path (P5)** -- Message envelope MUST carry `idempotency_key`. Queue consumer MUST call the same Lua scripts as gRPC handler. ACK after processing, rely on Lua idempotency for exactly-once.

## Implications for Roadmap

Based on combined research, the build order is dictated by hard dependencies. Phases 2 and 4 can run in parallel after Phase 1.

### Phase 1: Extract Business Logic
**Rationale:** Both queue consumers and 2PC participant operations need to call the same business logic. Without extraction, we duplicate Lua scripts or create coupling between transport and operations.
**Delivers:** `stock/operations.py`, `payment/operations.py` with all existing Lua-backed functions extracted from gRPC servicers. gRPC servicers become thin wrappers. Zero behavior change.
**Addresses:** Prerequisite for every other feature. Avoids anti-pattern of queue consumer calling gRPC servicer (ARCHITECTURE.md Anti-Pattern 2).
**Avoids:** P5 (idempotency loss) by ensuring a single code path for business logic.
**Validation:** All existing integration tests pass unchanged.

### Phase 2: Redis Streams Request/Reply Infrastructure
**Rationale:** The queue transport can be validated against the known-good SAGA behavior before introducing the complexity of 2PC. Building it second (after extraction) means queue consumers can call the same `operations.py` functions.
**Delivers:** `orchestrator/queue_client.py`, `orchestrator/reply_consumer.py`, `stock/queue_consumer.py`, `payment/queue_consumer.py`. SAGA+queue mode working end-to-end.
**Addresses:** Redis Streams request/reply, correlation ID matching, reply timeout handling, consumer groups per service.
**Avoids:** P3 (correlation hell), P6 (hash slot mismatch), P8 (backpressure), P11 (consumer group collision), P14 (circuit breaker thresholds).
**Validation:** SAGA + queue passes all existing integration tests.

### Phase 3: Transport Adapter and COMM_MODE Toggle
**Rationale:** Connects Phase 2 to the existing SAGA flow through a clean abstraction. Also extracts `saga_coordinator.py` from `grpc_server.py` to prepare for Phase 5.
**Delivers:** `orchestrator/transport.py`, `orchestrator/saga_coordinator.py`. Both SAGA+gRPC and SAGA+queue modes toggleable and tested.
**Addresses:** `COMM_MODE` env var toggle, transport abstraction (Strategy pattern).
**Avoids:** P9 (untestable combinations) by establishing the abstraction boundary early.
**Validation:** Toggle COMM_MODE between grpc and queue; full test suite passes in both modes.

### Phase 4: 2PC State Machine and Participant Operations
**Rationale:** Can be developed in parallel with Phase 2 (after Phase 1). The 2PC state machine mirrors `saga.py` closely and can be unit-tested in isolation. Participant operations (prepare/commit/abort) depend on extracted business logic from Phase 1.
**Delivers:** `orchestrator/tpc.py`, 2PC Lua scripts in `operations.py` (multi-item atomic prepare, commit finalization, abort release), new gRPC proto definitions for PrepareReserve/CommitReserve/AbortReserve and equivalents for Payment.
**Addresses:** 2PC state machine, participant logic, transaction log persistence, 2PC idempotency.
**Avoids:** P1 (coordinator crash -- decision written before phase-2), P2 (paper vote -- PREPARE does real reservation), P4 (state contamination -- separate namespace and transition map).
**Validation:** Unit tests for state transitions. Prepare/commit/abort operations work in isolation.

### Phase 5: 2PC Coordinator and Recovery
**Rationale:** Depends on both the 2PC state machine (Phase 4) and the transport adapter (Phase 3). This is the highest-complexity new code -- the coordinator drives concurrent prepare, collects votes, makes commit/abort decision, and retries phase-2 with retry-forever.
**Delivers:** `orchestrator/tpc_coordinator.py`, `orchestrator/tpc_recovery.py`, modified `recovery.py` with protocol dispatch, `TRANSACTION_PATTERN` toggle.
**Addresses:** 2PC coordinator flow, timeout/abort handling, recovery scanner, TRANSACTION_PATTERN toggle.
**Avoids:** P1 (recovery scanner handles stale 2PC records), P7 (participant timeout with decision polling), P13 (stale PREPARE rejected via decision record check).
**Validation:** 2PC+gRPC end-to-end. 2PC+queue end-to-end. Recovery for stuck 2PC transactions.

### Phase 6: Integration Testing and Benchmark
**Rationale:** All four mode combinations must be validated. Kill-tests must prove 0 consistency violations under all configurations.
**Delivers:** Parameterized test suite for all 4 combinations, benchmark results, 2PC lifecycle events.
**Addresses:** Unified test suite (differentiator), 2PC events, presumed-abort optimization.
**Avoids:** P9 (blind spots in untested combinations), P10 (latency tradeoff documented), P16 (deployment validation).
**Validation:** All 4 combinations pass integration tests. Benchmark passes with 0 consistency violations.

### Phase Ordering Rationale

- **Phase 1 must come first** because every subsequent phase calls extracted business logic functions
- **Phases 2 and 4 are parallel** -- queue infrastructure and 2PC state machine have no mutual dependency, only a shared dependency on Phase 1
- **Phase 3 before Phase 5** because the 2PC coordinator must use the transport abstraction, not import from client.py directly
- **Phase 5 requires both Phase 3 and Phase 4** -- it combines the transport layer with the 2PC state machine
- **Phase 6 is terminal** -- integration testing requires all other phases complete

```
Phase 1: Extract Business Logic
    |
    +---> Phase 2: Queue Infrastructure  ---+
    |                                       |
    +---> Phase 4: 2PC State Machine    ---+---> Phase 3: Transport Adapter
              |                                       |
              +---------------------------------------+
                                |
                          Phase 5: 2PC Coordinator
                                |
                          Phase 6: Integration
```

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (Queue Infrastructure):** The asyncio.Future-based reply correlation pattern needs careful design. Verify per-correlation vs shared reply stream performance under benchmark load. Poll interval tuning (100-500ms for commands vs 2000ms for events) needs empirical validation.
- **Phase 4 (2PC Participants):** Multi-item atomic Lua script for stock PREPARE is new -- current scripts reserve one item at a time. This needs careful Lua scripting to atomically check and reserve N items.
- **Phase 5 (2PC Coordinator):** Concurrent prepare with asyncio.gather and vote collection is the most novel code path. Edge cases around partial prepare timeout (P13) need explicit test scenarios.

Phases with standard patterns (skip deep research):
- **Phase 1 (Extract Logic):** Pure refactoring -- move functions between files, no new patterns.
- **Phase 3 (Transport Adapter):** Standard Strategy pattern with env var dispatch. Well-understood.
- **Phase 6 (Integration Testing):** Parameterized tests over env var matrix. Mechanical, not conceptually complex.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Zero new dependencies verified by mapping every v2.0 feature to existing library capabilities. All primitives already exercised in v1.0 codebase. |
| Features | HIGH | 2PC is a textbook protocol with well-defined states. Redis Streams request/reply is application-level but uses proven XADD/XREADGROUP APIs already in codebase. |
| Architecture | HIGH | Component boundaries follow directly from existing code structure. File-level changes are specific and traceable. Build order validated against dependency graph. |
| Pitfalls | HIGH | 16 pitfalls identified from distributed systems literature and codebase-specific analysis. Critical pitfalls (P1-P5) have concrete prevention strategies grounded in existing patterns. |

**Overall confidence:** HIGH

### Gaps to Address

- **Per-correlation vs shared reply stream:** STACK.md recommends per-correlation ephemeral streams; ARCHITECTURE.md recommends a single shared reply stream with Future map. The shared stream approach (ARCHITECTURE.md) is the better design -- it avoids key proliferation and Redis Cluster overhead. Use this approach. Validate under benchmark load.
- **Multi-item atomic Lua for 2PC stock prepare:** Current Lua scripts handle one item at a time. 2PC prepare must atomically check-and-reserve all items in a single order. This requires a new Lua script that has not been prototyped. Design this during Phase 4 planning.
- **Queue timeout tuning:** The 5-second RPC_TIMEOUT may be too aggressive for queue-based communication. PITFALLS.md recommends 10-15 seconds. Empirical testing during Phase 2 will determine the right value. Start with 10 seconds for queue mode.
- **Orchestrator multi-cluster connections:** STACK.md notes the orchestrator needs Redis connections to Stock and Payment clusters for queue mode. ARCHITECTURE.md suggests a single `{queue}` hash tag routing all queue streams to one slot. Resolve which topology during Phase 2 planning -- the single-hash-tag approach is simpler and sufficient at 20-CPU scale.

## Sources

### Primary (HIGH confidence)
- Existing v1.0 codebase analysis (saga.py, events.py, consumers.py, client.py, grpc_server.py, all requirements.txt)
- [Martin Fowler -- Two-Phase Commit](https://martinfowler.com/articles/patterns-of-distributed-systems/two-phase-commit.html)
- [Redis Streams Official Documentation](https://redis.io/docs/latest/develop/data-types/streams/)
- [redis-py Async Documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)

### Secondary (MEDIUM confidence)
- [Princeton CS -- 2PC Lecture Notes](https://www.cs.princeton.edu/courses/archive/fall16/cos418/docs/L6-2pc.pdf)
- [Wikipedia -- Two-Phase Commit Protocol](https://en.wikipedia.org/wiki/Two-phase_commit_protocol)
- [Redis Blog -- Sync and Async Communication](https://redis.io/blog/what-to-choose-for-your-synchronous-and-asynchronous-communication-needs-redis-streams-redis-pub-sub-kafka-etc-best-approaches-synchronous-asynchronous-communication/)
- [GeeksforGeeks -- Recovery from Failures in 2PC](https://www.geeksforgeeks.org/dbms/recovery-from-failures-in-two-phase-commit-protocol-distributed-transaction/)
- [Baeldung -- 2PC vs SAGA Pattern](https://www.baeldung.com/cs/two-phase-commit-vs-saga-pattern)

### Tertiary (LOW confidence)
- [Redis Streams Wrong Assumptions](https://redis.io/blog/youre-probably-thinking-about-redis-streams-wrong/) -- stream entry structure clarification
- [OneUptime -- Reliable Message Queues with Redis Streams](https://oneuptime.com/blog/post/2026-01-21-redis-streams-message-queues/view) -- general patterns

---
*Research completed: 2026-03-12*
*Ready for roadmap: yes*
