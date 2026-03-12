# Pitfalls: Adding 2PC & Message Queues to Existing SAGA Checkout System

**Research Date:** 2026-03-12
**Scope:** Adding Two-Phase Commit (2PC) as alternative transaction pattern and Redis Streams message queues as default inter-service communication to existing SAGA-based distributed checkout system
**Context:** v2.0 milestone for DDS26-8. Existing system has working SAGA orchestrator, gRPC inter-service calls, Redis Cluster, Lua CAS operations, idempotency keys, circuit breakers, and container-kill recovery. Adding 2PC toggle and message queue toggle alongside existing patterns.

---

## Critical Pitfalls

### P1 -- 2PC Coordinator Crash Between Prepare and Commit (The Blocking Hazard)

**What goes wrong:** The defining weakness of 2PC. The orchestrator sends PREPARE to Stock and Payment. Both respond VOTE_COMMIT and write prepare logs. The orchestrator crashes before writing or sending COMMIT. Both participants are now holding reserved resources (stock decremented tentatively, payment held) indefinitely. Unlike SAGA where compensation eventually runs, 2PC participants that voted COMMIT cannot unilaterally abort -- they must wait for the coordinator's decision. Without a durable decision log, the coordinator restarts with no memory of the in-flight transaction.

In this codebase specifically: the orchestrator is already a single replica (`maxSurge=0`). A crash means no coordinator is running until Kubernetes restarts it. The participants hold resources for the entire restart window (typically 10-30 seconds). Under benchmark load, dozens of transactions can be blocked.

**Warning signs:**
- 2PC state stored only in memory (no Redis persistence of coordinator decision)
- No WAL (Write-Ahead Log) equivalent before sending COMMIT/ABORT to participants
- Participants hold locks/reservations with no timeout
- Recovery scanner does not handle 2PC records, only SAGA records
- Benchmark kill-test causes permanent resource leaks (stock reserved but never committed or released)

**Prevention:**
- Write the coordinator's decision (COMMIT or ABORT) to Redis BEFORE sending phase-2 messages to participants. Use the existing `{saga:ORDER_ID}` hash pattern but with 2PC-specific states (e.g., `2PC_PREPARING`, `2PC_COMMITTING`, `2PC_COMMITTED`, `2PC_ABORTING`, `2PC_ABORTED`)
- Extend the existing `recovery.py` scanner to handle 2PC records: if a 2PC record is in `2PC_COMMITTING`, replay COMMIT to all participants; if in `2PC_PREPARING`, send ABORT (the safe default when coordinator decision was never recorded)
- Add participant-side timeouts: if a participant has voted COMMIT but receives no phase-2 message within N seconds, it must query the coordinator (or its Redis log) for the decision. This prevents permanent blocking
- Use the existing Lua CAS pattern (`transition_state`) to atomically transition 2PC states, preventing split-brain on coordinator restart

**Phase:** Phase 1 -- 2PC state machine design. This is THE critical design decision for 2PC; get it wrong and container-kill tests will fail every time.

---

### P2 -- Participant Prepare Without Actual Resource Reservation (Paper Vote)

**What goes wrong:** A participant responds VOTE_COMMIT to PREPARE without actually reserving the resource. Between PREPARE and COMMIT, another transaction consumes the resource. When COMMIT arrives, the participant cannot fulfill its promise. This violates the fundamental 2PC contract: a VOTE_COMMIT is an irrevocable promise.

In this codebase: Stock uses Lua CAS to atomically decrement. If PREPARE just "checks" stock is available without decrementing, a concurrent checkout can consume that stock before COMMIT arrives. Payment has the same issue with credit.

**Warning signs:**
- PREPARE handler that reads current state without modifying it (read-only check)
- No tentative state for resources (stock shows "10 available" to everyone, rather than "10 available, 3 tentatively reserved")
- Concurrent checkouts succeed in PREPARE phase for the same limited resource
- The existing `ReserveStock` Lua script is used directly for PREPARE without adaptation

**Prevention:**
- PREPARE must actually reserve the resource. For stock: decrement available stock and record the tentative reservation (e.g., `{item:UUID}:pending:ORDER_ID`). For payment: hold the credit. The existing `RESERVE_STOCK_ATOMIC_LUA` already does this -- the key insight is that 2PC PREPARE should use the same atomic reservation that SAGA's forward step uses
- COMMIT then finalizes (removes the pending marker, makes the reservation permanent). ABORT releases the tentative reservation (same as SAGA compensation)
- This means 2PC PREPARE is functionally identical to the current SAGA forward step, and 2PC ABORT is functionally identical to SAGA compensation. The difference is purely in the coordination protocol, not the participant operations
- If you make PREPARE a no-op check, you have converted 2PC into optimistic 2PC, which has weaker guarantees and will fail the consistency benchmark

**Phase:** Phase 1 -- participant 2PC handlers. Must be designed together with P1.

---

### P3 -- Request-Reply Over Redis Streams: Correlation and Timeout Hell

**What goes wrong:** Replacing gRPC (synchronous request-reply) with Redis Streams (async pub-sub) for inter-service calls requires building a request-reply protocol on top of an inherently one-directional messaging system. The orchestrator sends a command to `{stock:commands}` stream and must wait for a response on a reply stream. This requires: (1) a correlation ID linking request to response, (2) a per-request reply stream or a shared reply stream with filtering, (3) timeout handling when the response never arrives, (4) cleanup of orphaned reply entries.

Getting any of these wrong creates subtle bugs that only surface under load or during container kills.

**Warning signs:**
- Using a single shared reply stream without correlation IDs (responses go to wrong callers)
- Blocking `XREAD` with no timeout (hangs forever if participant crashes)
- Reply stream entries accumulating without cleanup (memory leak)
- No handling for the case where the reply arrives AFTER the caller has timed out and moved on
- Consumer group on reply stream with multiple orchestrator-side consumers stealing each other's responses

**Prevention:**
- Use a per-transaction reply stream or a correlation-ID-based approach. Recommended: the orchestrator includes a `reply_stream` and `correlation_id` in each command message. The participant writes the response to the specified reply stream with the correlation ID
- Use `XREAD` with `BLOCK` timeout (e.g., 5000ms matching the current `RPC_TIMEOUT = 5.0`). If timeout expires, treat as the same UNKNOWN outcome the existing circuit breaker handles
- Do NOT use consumer groups on reply streams -- the orchestrator is the sole consumer. Use plain `XREAD` with a last-seen ID
- Clean up reply stream entries after reading (XDEL or use MAXLEN trimming). Otherwise Redis memory grows unboundedly
- Consider using a single `{orchestrator:replies}` stream with correlation IDs rather than per-transaction reply streams to avoid stream proliferation. Use hash tags to keep it on one Redis Cluster slot

**Phase:** Phase 1 -- message queue architecture. The entire request-reply protocol must be designed before any implementation begins.

---

### P4 -- Mixing SAGA and 2PC State Machines in the Same Orchestrator

**What goes wrong:** The orchestrator currently has a clean SAGA state machine (`STARTED -> STOCK_RESERVED -> PAYMENT_CHARGED -> COMPLETED | COMPENSATING -> FAILED`). Adding 2PC introduces a second state machine (`2PC_STARTED -> 2PC_PREPARING -> 2PC_PREPARED -> 2PC_COMMITTING -> 2PC_COMMITTED | 2PC_ABORTING -> 2PC_ABORTED`). If both share the same `{saga:ORDER_ID}` namespace and `transition_state` Lua script without clear separation, state machine contamination occurs: a 2PC transaction accidentally follows SAGA transitions, or the recovery scanner applies SAGA recovery logic to a 2PC transaction.

In this codebase: `saga.py` has hardcoded `VALID_TRANSITIONS` dict. The recovery scanner in `recovery.py` has hardcoded `NON_TERMINAL_STATES`. Neither knows about 2PC states.

**Warning signs:**
- 2PC records stored in the same Redis key namespace as SAGA records without a `protocol` field
- `VALID_TRANSITIONS` dict extended with 2PC states rather than having a separate dict
- Recovery scanner treating 2PC `PREPARING` state like SAGA `STARTED` (attempting forward recovery instead of aborting)
- Env var toggle switches protocol but recovery scanner doesn't check which protocol the record uses
- Unit tests only test one protocol at a time, never mixed scenarios

**Prevention:**
- Add a `protocol` field (`saga` or `2pc`) to the transaction record hash, written at creation time
- Create separate transition maps: `SAGA_TRANSITIONS` and `TPC_TRANSITIONS`. The `transition_state` Lua script is protocol-agnostic (just does CAS on state field), but Python-side validation must select the correct transition map based on the record's protocol field
- Recovery scanner must branch on `protocol`: SAGA records get forward-or-compensate recovery; 2PC records get abort-if-uncommitted recovery (the safe default per P1)
- The env var toggle (`TRANSACTION_PATTERN=saga|2pc`) controls which protocol NEW transactions use, but the system must always be able to handle BOTH protocols for in-flight and historical records (especially during rolling deploys)
- Never allow a running system to have mixed-protocol in-flight transactions for the same order_id -- use the existing `create_saga_record` HSETNX pattern to prevent duplicates

**Phase:** Phase 1 -- state machine design. This is the integration backbone for the entire v2.0.

---

### P5 -- Message Queue Replacing gRPC Without Preserving Idempotency Guarantees

**What goes wrong:** The current gRPC path has carefully designed idempotency: each call carries an idempotency key (e.g., `{saga:ORDER_ID}:step:reserve:ITEM_ID`), and participants use Lua scripts to atomically check-and-execute. When switching to Redis Streams, the idempotency key must still be present in the message payload and the participant must still perform the same Lua CAS check. If the message queue handler processes messages without idempotency keys (relying on consumer group "at-most-once" semantics from XACK), then crash-recovery message redelivery causes double-execution.

In this codebase: the existing `RESERVE_STOCK_ATOMIC_LUA` and `CHARGE_PAYMENT_ATOMIC_LUA` scripts expect an idempotency key. The gRPC handlers pass it from the request protobuf field. The new message queue consumer must do the same.

**Warning signs:**
- Message queue command schema that omits `idempotency_key` field
- Consumer that calls business logic directly (e.g., stock decrement) without going through the idempotency-aware Lua path
- Consumer that ACKs before processing (at-most-once semantics that lose messages on crash)
- Consumer that processes then ACKs (at-least-once) but without idempotency (double-processes on redelivery)

**Prevention:**
- The message queue command payload MUST include `idempotency_key` (same format as gRPC: `{saga:ORDER_ID}:step:OPERATION:ENTITY_ID`)
- The message queue consumer handler must call the exact same Lua scripts the gRPC handler calls. Extract the business logic into shared functions that both gRPC and message queue handlers invoke
- ACK after processing (at-least-once delivery), relying on Lua idempotency for exactly-once execution. This is the same pattern the existing SAGA compensation consumer already uses
- Do NOT attempt at-most-once delivery (ACK before processing). Redis Streams consumer groups do not guarantee at-most-once under crashes -- XAUTOCLAIM will redeliver unACKed messages

**Phase:** Phase 1 -- message queue handler implementation. Must be designed alongside gRPC handler refactoring.

---

## High Severity Pitfalls

### P6 -- Reply Stream Hash Slot Mismatch in Redis Cluster

**What goes wrong:** Redis Streams used for inter-service communication must be accessible by both producer and consumer. In Redis Cluster, the stream lives on whichever node owns its hash slot. The orchestrator writes to `{stock:commands}` (slot determined by `stock`), Stock reads from it, processes, and writes to `{orchestrator:replies}` (slot determined by `orchestrator`). This works fine -- the stream names hash to predictable slots.

BUT: if you use per-transaction reply streams like `reply:ORDER_ID` without hash tags, each reply stream lands on a different slot. The orchestrator must now XREAD from streams on different nodes. Worse: if the orchestrator creates a consumer group on a reply stream, but that stream's slot migrates during cluster rebalancing, the consumer group state is on the old node.

**Warning signs:**
- Reply stream names without hash tags (e.g., `reply:abc123` instead of `{orchestrator:replies}`)
- `CROSSSLOT` errors when trying to read from multiple reply streams in one XREAD call
- Stream counts growing unboundedly because per-transaction streams are never deleted
- MOVED errors during cluster rebalancing that cause missed replies

**Prevention:**
- Use a shared reply stream with hash tag: `{orchestrator:replies}` -- all replies go to one stream on one slot
- Differentiate replies using correlation IDs in the message fields, not separate streams
- If you must use per-transaction reply streams, use hash tags: `{orchestrator}:reply:ORDER_ID` -- all streams hash to the same slot as `orchestrator`
- XREAD can block on only one stream at a time per call anyway (unless you pass multiple stream names). With a single shared reply stream, this is a non-issue

**Phase:** Phase 1 -- message queue architecture design.

---

### P7 -- 2PC Participant Timeout Causing Silent Resource Leak

**What goes wrong:** A participant receives PREPARE and votes COMMIT, reserving resources (stock held, credit held). The coordinator decides ABORT (e.g., the other participant voted ABORT) and sends ABORT to all participants. But the network drops the ABORT message to one participant. That participant holds the reservation forever.

With gRPC, the participant would eventually get a timeout on the coordinator connection and could infer something is wrong. With Redis Streams, there is no connection to time out -- the participant is passively reading from a stream. If the ABORT message is lost (e.g., the stream was trimmed before the participant read it), the participant never learns the decision.

**Warning signs:**
- No timeout on participant's PREPARED state
- MAXLEN trimming on command streams that could discard unread COMMIT/ABORT messages
- No participant-side polling of transaction outcome
- Resources held indefinitely after coordinator crash or message loss
- Kill-test shows stock reserved but never committed or released

**Prevention:**
- Participants must have a PREPARED-state timeout. If no COMMIT or ABORT arrives within N seconds, the participant queries the coordinator's transaction record in Redis (`{saga:ORDER_ID}` hash) to read the decision directly
- Alternatively, have participants poll a dedicated decision stream/key. The coordinator writes the decision to `{2pc:ORDER_ID}:decision` which participants can read independently of the command stream
- Never use MAXLEN trimming on command streams that carry COMMIT/ABORT messages for in-flight transactions. Trim only after all participants have ACKed
- The participant timeout should be longer than the coordinator restart time (to give the coordinator time to recover and resend)

**Phase:** Phase 2 -- 2PC fault tolerance hardening. Initial implementation can assume reliable delivery, but kill-tests will expose this.

---

### P8 -- Message Queue Backpressure: Stock Service Overwhelmed by Queued Commands

**What goes wrong:** With gRPC, the orchestrator waits for a response before sending the next request -- natural backpressure through synchronous request-reply. With message queues, the orchestrator can fire N commands into the stream without waiting. If the stock service is slow or temporarily down, commands pile up. When it recovers, it processes a burst of stale commands. Some may reference resources that no longer exist or transactions that already timed out and were compensated.

**Warning signs:**
- Stream length (`XLEN`) growing during load spikes
- Consumer lag increasing (visible in `XPENDING` output)
- Processing stale commands that reference already-failed transactions
- CPU spike on stock/payment service when it catches up after downtime
- Orchestrator timeout fires before the participant processes the command, causing double-execution (orchestrator retries while original is still in queue)

**Prevention:**
- Even with message queues, the orchestrator should wait for each reply before sending the next command in a transaction. This is request-reply over streams, not fire-and-forget. The queue decouples transport, not the protocol
- Monitor stream length and consumer lag in the health endpoint (extend existing `/health` which already reports consumer lag)
- Set MAXLEN on command streams to prevent unbounded growth, but ensure it is large enough that commands are not trimmed before consumption
- Add a `created_at` timestamp to command messages. Consumers should check message age and skip commands older than the transaction timeout (they have already been compensated)
- Circuit breaker on the producer side: if the stream length exceeds a threshold, the orchestrator should fail fast rather than adding to the backlog

**Phase:** Phase 2 -- load testing and performance tuning.

---

### P9 -- Dual Communication Path Toggle Creating Untestable Combinations

**What goes wrong:** The v2.0 spec requires env var toggles for both transaction pattern (SAGA/2PC) and communication mode (gRPC/message queue). This creates 4 combinations: SAGA+gRPC (existing), SAGA+queue, 2PC+gRPC, 2PC+queue. Each combination has different failure modes, timing characteristics, and recovery behaviors. If each path is independently tested but combinations are not, the 2PC+queue combination (which has never existed before) will have integration bugs discovered only during the benchmark.

**Warning signs:**
- Tests structured as "test SAGA" and "test 2PC" separately, with communication mode hardcoded
- Toggle implemented as top-level if/else that duplicates the entire checkout flow
- One combination works reliably in dev but fails in CI because the CI env vars default differently
- Recovery scanner tested only with the default toggle values

**Prevention:**
- Design the orchestrator with a clean abstraction: a `TransactionCoordinator` interface with `saga_coordinator` and `tpc_coordinator` implementations, and a `ServiceClient` interface with `grpc_client` and `queue_client` implementations. The coordinator uses the client interface, so all 4 combinations work through the same code paths
- Run the full integration test suite (all 37 existing tests) against ALL 4 combinations in CI. This is a matrix build, not optional
- Test recovery (kill-test) for each combination separately -- SAGA recovery and 2PC recovery have different semantics
- Default to the existing combination (SAGA+gRPC) in production/benchmark to preserve the known-working path. Only switch after the new combination passes all tests

**Phase:** Spans all phases -- but the abstraction design must happen in Phase 1.

---

## Moderate Pitfalls

### P10 -- 2PC Holding Locks Longer Than SAGA (Latency Impact Under 20 CPU Budget)

**What goes wrong:** SAGA executes steps sequentially: reserve stock, charge payment, done. Each step takes ~5ms (gRPC roundtrip). Total lock time per resource: ~5ms. 2PC sends PREPARE to all participants simultaneously, waits for all VOTE responses, then sends COMMIT. The resource is held from PREPARE through COMMIT -- minimum 2 roundtrips. With message queues (higher latency than gRPC), this hold time increases further.

Under the 20 CPU benchmark, longer resource hold times mean more contention, more CAS retries, and lower throughput. The benchmark measures total checkout throughput -- 2PC may be measurably slower.

**Prevention:**
- Accept that 2PC will have higher latency than SAGA for the same workload. This is inherent to the protocol, not a bug
- Optimize the prepare-commit gap: batch the PREPARE messages and read replies concurrently (asyncio.gather on XREAD calls)
- Keep the gRPC path as the performance-optimized option; document that the message queue path trades latency for decoupling
- Benchmark both configurations and report the tradeoff in the architecture document (this is valuable for the course evaluation)

**Phase:** Phase 3 -- performance testing and optimization.

---

### P11 -- Redis Streams Consumer Group Name Collision With Existing Consumers

**What goes wrong:** The existing system has two consumer groups on `{saga:events}:checkout`: `compensation-handler` and `audit-logger`. If the new message queue communication adds consumer groups on the same stream (e.g., for routing stock/payment commands), or creates new streams with conflicting group names, existing consumers may start receiving messages they do not understand.

More subtly: if the new command/reply streams use the same consumer group infrastructure (XGROUP_CREATE, XREADGROUP), but with different semantics (commands need exactly-one-consumer delivery vs. events need broadcast), mismatched group configuration causes either message duplication or message loss.

**Warning signs:**
- New consumer groups created on existing event streams
- Existing `compensation_consumer` receiving command messages instead of events
- BUSYGROUP errors on startup because group already exists with different stream
- Event consumers and command consumers sharing group names across different streams

**Prevention:**
- Keep event streams (`{saga:events}:checkout`) completely separate from command/reply streams (`{stock:commands}`, `{payment:commands}`, `{orchestrator:replies}`)
- Use descriptive, namespaced group names: `stock-cmd-processor`, `payment-cmd-processor` -- not generic names like `worker` or `consumer`
- Command streams should use consumer groups with a single consumer per service instance (ensures each command is processed once). Event streams can have multiple groups (fan-out to multiple consumers)
- Document the stream topology: which streams exist, which groups consume from each, and what message schemas each carries

**Phase:** Phase 1 -- stream topology design.

---

### P12 -- Forgetting to Propagate Idempotency Keys Through the Message Queue Envelope

**What goes wrong:** The existing gRPC protos define `idempotency_key` as an explicit field on each request message (e.g., `ReserveStockRequest.idempotency_key`). When wrapping these operations in Redis Streams messages, the idempotency key must be serialized into the stream entry fields. If the message envelope schema is designed separately from the gRPC proto, the idempotency key gets lost in translation.

In this codebase: the orchestrator's `client.py` constructs idempotency keys like `{saga:ORDER_ID}:step:reserve:ITEM_ID` and passes them as gRPC request fields. The message queue equivalent must carry the same key in the same format.

**Warning signs:**
- Message queue command schema defined without looking at the gRPC proto fields
- Participant message handler that generates a NEW idempotency key instead of using the one from the message
- Different idempotency key formats between gRPC and message queue paths (making it impossible to switch mid-transaction)

**Prevention:**
- Define the message queue command schema as a superset of the gRPC request fields. Every field in the proto must appear in the stream entry
- The `client.py` abstraction should generate the idempotency key once, and both gRPC and message queue paths use the same key
- Write a test that starts a transaction over gRPC, kills the orchestrator, restarts with message queue mode, and verifies the same idempotency keys are used for recovery retries

**Phase:** Phase 1 -- message schema design.

---

### P13 -- 2PC ABORT After Partial PREPARE (Not All Participants Responded)

**What goes wrong:** The orchestrator sends PREPARE to Stock and Payment. Stock responds VOTE_COMMIT. Payment never responds (crash, network partition, or message queue delay). The orchestrator times out and decides ABORT. It sends ABORT to Stock (which releases its reservation) and ABORT to Payment. But Payment never received PREPARE -- so it has nothing to abort. Later, Payment recovers and reads the original PREPARE from the stream (messages persist in Redis Streams). It processes PREPARE, votes COMMIT, and waits for COMMIT that will never come.

**Warning signs:**
- No TTL or expiry on stream messages, causing stale PREPAREs to be processed after abort
- Participant processes PREPARE without checking if the transaction has already been decided
- ABORT sent to a participant that never received PREPARE -- harmless but confusing in logs
- Recovered participant holds resources indefinitely (same as P7)

**Prevention:**
- Before processing a PREPARE message, the participant should check the transaction's decision record in Redis (`{saga:ORDER_ID}` hash). If the record shows ABORTED, skip the PREPARE entirely
- Add a `created_at` timestamp to PREPARE messages. Participants should reject PREPAREs older than the transaction timeout
- The coordinator should record which participants were sent PREPARE and which responded, so ABORT is only sent to participants that actually received PREPARE
- Combine with P7's participant timeout: even if a stale PREPARE is processed, the participant timeout will trigger a decision lookup and release resources

**Phase:** Phase 2 -- 2PC fault tolerance. This is a nuanced failure mode that surfaces in kill-tests.

---

### P14 -- Message Queue Latency Variance Breaking Circuit Breaker Thresholds

**What goes wrong:** The existing circuit breaker on gRPC calls has a 5-second timeout (`RPC_TIMEOUT = 5.0`). gRPC latency is predictable (~1-5ms per call). Redis Streams latency is less predictable: XREAD BLOCK has polling intervals (currently `POLL_INTERVAL_MS = 2000`), consumer processing time adds to the response latency, and stream backlog under load can add seconds of delay. If the orchestrator keeps the same 5-second timeout for message queue responses, it will false-trigger under normal load variations.

**Warning signs:**
- Circuit breaker opening under moderate load when using message queue path (but not gRPC path)
- High timeout rate on message queue path compared to gRPC
- Transactions failing with "service unavailable" when services are actually healthy but slow to consume
- Inconsistent latency between first message (stream is empty, XREAD blocks full interval) and subsequent messages

**Prevention:**
- Use different timeout values for gRPC and message queue paths. gRPC: keep 5s. Message queue: set to 10-15s to account for polling interval + processing time
- The circuit breaker should be scoped per-communication-mode, not per-target-service. A gRPC circuit breaker should not trip based on message queue failures and vice versa
- Monitor actual p99 latency for message queue request-reply during load testing and set the timeout to p99 + generous margin
- Consider that message queue latency has a floor of POLL_INTERVAL_MS (the consumer checks for new messages every N ms). Set poll interval to 100-500ms for command streams, not the 2000ms used for event streams

**Phase:** Phase 2 -- integration testing and tuning.

---

## Minor Pitfalls

### P15 -- Stream Naming Convention Inconsistency With Existing Hash Tag Patterns

**What goes wrong:** The existing codebase uses hash tags carefully: `{saga:ORDER_ID}` for SAGA records, `{item:UUID}` for stock, `{user:UUID}` for users. All hash tags are designed so related keys land on the same Redis Cluster slot. If new streams use different hash tag patterns or no hash tags at all, the streams end up on unpredictable slots. This does not cause correctness issues but makes monitoring, debugging, and cluster capacity planning harder.

**Prevention:**
- Follow the existing convention. Command streams: `{stock:commands}`, `{payment:commands}`. Reply streams: `{orchestrator:replies}`. Event streams: `{saga:events}:STREAM_NAME` (already established)
- Document the full key namespace in the architecture doc

**Phase:** Phase 1 -- convention during stream design.

---

### P16 -- Rolling Deploy With Mixed Protocol Versions

**What goes wrong:** During a rolling deploy from v1.0 (SAGA+gRPC only) to v2.0 (SAGA/2PC toggle + gRPC/queue toggle), old pods run the v1.0 code that does not understand 2PC state records or message queue commands. If the deployment is not properly staged, old and new orchestrator pods may coexist briefly. An old pod picks up a 2PC record from the recovery scanner and applies SAGA recovery logic, corrupting the transaction.

**Prevention:**
- The orchestrator runs as a single replica -- rolling deploy replaces one pod with one pod. There is no overlap if `maxSurge=0` (already configured)
- BUT: verify that Kubernetes actually waits for the old pod to terminate before starting the new one. With `maxSurge=0` and `maxUnavailable=1`, this is guaranteed
- Add the `protocol` field check in recovery scanner defensively even for v1.0 backfill: existing records without a `protocol` field default to `saga`

**Phase:** Phase 3 -- deployment and testing.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| 2PC state machine design | P1: Coordinator crash between phases, P4: Mixed SAGA/2PC states | Write decision to Redis BEFORE sending phase-2 messages; separate state machine per protocol |
| 2PC participant handlers | P2: Paper vote without reservation, P7: Silent resource leak | PREPARE must actually reserve; add participant-side decision timeout |
| Message queue architecture | P3: Request-reply correlation, P6: Hash slot mismatch | Shared reply stream with correlation IDs and hash tags |
| Message queue handlers | P5: Lost idempotency, P12: Key propagation | Same Lua scripts, same idempotency keys, shared handler functions |
| Integration/toggle design | P9: Untestable combinations, P4: Mixed state contamination | Clean interface abstraction; CI matrix testing all 4 combinations |
| Fault tolerance (kill-test) | P1: Coordinator crash, P7: Participant timeout, P13: Stale PREPARE | WAL in Redis, participant decision polling, message age checks |
| Performance tuning | P10: 2PC lock duration, P8: Queue backpressure, P14: Circuit breaker thresholds | Different timeouts per communication mode; accept 2PC latency tradeoff |
| Deployment | P16: Mixed protocol versions, P11: Consumer group collision | Single-replica orchestrator with maxSurge=0; namespaced stream/group names |

---

## Summary Table

| # | Pitfall | Severity | Category | Phase |
|---|---------|----------|----------|-------|
| P1 | 2PC coordinator crash between prepare and commit | Critical | 2PC | Phase 1 |
| P2 | Participant PREPARE without actual resource reservation | Critical | 2PC | Phase 1 |
| P3 | Request-reply correlation and timeout over Redis Streams | Critical | Message Queue | Phase 1 |
| P4 | Mixed SAGA/2PC state machine contamination | Critical | Integration | Phase 1 |
| P5 | Message queue handlers losing idempotency guarantees | Critical | Message Queue | Phase 1 |
| P6 | Reply stream hash slot mismatch in Redis Cluster | High | Message Queue | Phase 1 |
| P7 | 2PC participant timeout causing silent resource leak | High | 2PC | Phase 2 |
| P8 | Message queue backpressure overwhelming participants | High | Message Queue | Phase 2 |
| P9 | Dual toggle creating untestable 4-combination matrix | High | Integration | All |
| P10 | 2PC longer lock duration impacting benchmark throughput | Moderate | 2PC | Phase 3 |
| P11 | Consumer group name collision with existing streams | Moderate | Message Queue | Phase 1 |
| P12 | Idempotency key not propagated through message envelope | Moderate | Message Queue | Phase 1 |
| P13 | Stale PREPARE processed after transaction already aborted | Moderate | 2PC | Phase 2 |
| P14 | Message queue latency variance breaking circuit breaker | Moderate | Message Queue | Phase 2 |
| P15 | Stream naming inconsistency with existing hash tag patterns | Minor | Message Queue | Phase 1 |
| P16 | Rolling deploy with mixed protocol versions | Minor | Deployment | Phase 3 |

---

## Sources

- [Martin Fowler - Two-Phase Commit Pattern](https://martinfowler.com/articles/patterns-of-distributed-systems/two-phase-commit.html)
- [Redis Streams Official Documentation](https://redis.io/docs/latest/develop/data-types/streams/)
- [Redis Microservices Interservice Communication Tutorial](https://redis.io/learn/howtos/solutions/microservices/interservice-communication)
- [GeeksforGeeks - Recovery from Failures in 2PC](https://www.geeksforgeeks.org/dbms/recovery-from-failures-in-two-phase-commit-protocol-distributed-transaction/)
- [Baeldung - 2PC vs SAGA Pattern](https://www.baeldung.com/cs/two-phase-commit-vs-saga-pattern)
- [Princeton CS - 2PC Lecture Notes](https://www.cs.princeton.edu/courses/archive/fall16/cos418/docs/L6-2pc.pdf)
- [Wikipedia - Two-Phase Commit Protocol](https://en.wikipedia.org/wiki/Two-phase_commit_protocol)
- [OneUptime - Reliable Message Queues with Redis Streams](https://oneuptime.com/blog/post/2026-01-21-redis-streams-message-queues/view)

*Research: 2026-03-12 -- v2.0 milestone pitfalls for DDS26-8*
