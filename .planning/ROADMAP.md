# Roadmap: DDS26-8 Distributed Checkout System

## Milestones

- ✅ **v1.0 Distributed Checkout System** -- Phases 1-7 (shipped 2026-03-11)
- 🚧 **v2.0 2PC & Message Queues** -- Phases 8-13 (in progress)

## Phases

<details>
<summary>v1.0 Distributed Checkout System (Phases 1-7) -- SHIPPED 2026-03-11</summary>

- [x] Phase 1: Async Foundation (3/3 plans) -- completed 2026-02-28
- [x] Phase 2: gRPC Communication (4/4 plans) -- completed 2026-02-28
- [x] Phase 3: SAGA Orchestration (4/4 plans) -- completed 2026-02-28
- [x] Phase 4: Fault Tolerance (2/2 plans) -- completed 2026-02-28
- [x] Phase 5: Event-Driven Architecture (2/2 plans) -- completed 2026-02-28
- [x] Phase 6: Infrastructure (3/3 plans) -- completed 2026-03-01
- [x] Phase 7: Validation and Delivery (3/3 plans) -- completed 2026-03-01

Full details: `.planning/milestones/v1.0-ROADMAP.md`

</details>

### v2.0 2PC & Message Queues

**Milestone Goal:** Add Two-Phase Commit as an alternative transaction pattern and migrate inter-service communication to Redis Streams message queues, with env var toggles for both SAGA/2PC and queue/gRPC paths.

**Phase Numbering:**
- Integer phases (8, 9, ...): Planned milestone work
- Decimal phases (9.1, 9.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 8: Business Logic Extraction** - Extract Stock and Payment business logic from gRPC servicers into shared operations modules
- [x] **Phase 9: Queue Infrastructure** - Build Redis Streams request/reply messaging with consumer groups and correlation ID routing (completed 2026-03-12)
- [x] **Phase 10: Transport Adapter** - Create transport abstraction enabling transparent gRPC/queue swap with COMM_MODE toggle (completed 2026-03-12)
- [ ] **Phase 11: 2PC State Machine & Participants** - Build 2PC state machine and tentative reservation Lua scripts for Stock and Payment
- [ ] **Phase 12: 2PC Coordinator & Recovery** - Implement 2PC coordinator flow, WAL pattern, recovery scanner, and TRANSACTION_PATTERN toggle
- [ ] **Phase 13: Integration & Benchmark** - Validate all 4 mode combinations with integration tests, kill-tests, and benchmark

## Phase Details

### Phase 8: Business Logic Extraction
**Goal**: Stock and Payment business logic is callable from any transport layer without coupling to gRPC
**Depends on**: Nothing (first phase of v2.0)
**Requirements**: BLE-01, BLE-02
**Success Criteria** (what must be TRUE):
  1. Stock service gRPC servicer delegates all business logic to `operations.py` functions -- no Lua scripts or Redis calls remain in the servicer
  2. Payment service gRPC servicer delegates all business logic to `operations.py` functions -- no Lua scripts or Redis calls remain in the servicer
  3. All existing integration tests pass unchanged (zero behavior change)
**Plans:** 2 plans

Plans:
- [ ] 08-01-PLAN.md — Extract Stock business logic into operations.py
- [ ] 08-02-PLAN.md — Extract Payment business logic into operations.py

### Phase 9: Queue Infrastructure
**Goal**: Redis Streams request/reply messaging works end-to-end between orchestrator and domain services
**Depends on**: Phase 8
**Requirements**: MQC-01, MQC-02, MQC-03
**Success Criteria** (what must be TRUE):
  1. Orchestrator can send commands to Stock and Payment via Redis Streams and receive replies with correct correlation
  2. Stock and Payment queue consumers process commands by calling the same operations module functions as gRPC servicers
  3. SAGA checkout completes successfully over queue transport (manual wiring, no toggle yet)
  4. Consumer groups provide at-least-once delivery with proper ACK after processing
**Plans:** 2/2 plans complete

Plans:
- [ ] 09-01-PLAN.md — Orchestrator queue client and reply listener
- [ ] 09-02-PLAN.md — Domain service queue consumers and integration tests

### Phase 10: Transport Adapter
**Goal**: Orchestrator transparently switches between gRPC and queue communication via a single env var
**Depends on**: Phase 9
**Requirements**: MQC-04, MQC-05
**Success Criteria** (what must be TRUE):
  1. Setting `COMM_MODE=grpc` uses gRPC transport; setting `COMM_MODE=queue` uses Redis Streams transport -- no other code changes needed
  2. SAGA coordinator calls transport adapter functions with identical signatures regardless of mode
  3. Full integration test suite passes in both SAGA+gRPC and SAGA+queue modes
**Plans:** 1/1 plans complete

Plans:
- [ ] 10-01-PLAN.md — Transport adapter with COMM_MODE toggle and caller updates

### Phase 11: 2PC State Machine & Participants
**Goal**: 2PC protocol state machine and participant-side tentative reservation logic are complete and unit-testable
**Depends on**: Phase 8
**Requirements**: TPC-01, TPC-02, TPC-03
**Success Criteria** (what must be TRUE):
  1. 2PC state machine enforces valid transitions (INIT, PREPARING, COMMITTING, ABORTING, COMMITTED, ABORTED) via Lua CAS -- invalid transitions are rejected
  2. Stock PREPARE atomically reserves all items in an order; COMMIT finalizes; ABORT releases -- partial prepare is impossible
  3. Payment PREPARE atomically reserves funds; COMMIT finalizes; ABORT releases
  4. All 2PC Lua scripts preserve idempotency (duplicate PREPARE/COMMIT/ABORT calls are safe)
**Plans:** 2 plans

Plans:
- [ ] 11-01-PLAN.md — 2PC state machine (tpc.py) with Lua CAS transitions and tests
- [ ] 11-02-PLAN.md — Stock and Payment 2PC participant operations (prepare/commit/abort)

### Phase 12: 2PC Coordinator & Recovery
**Goal**: Orchestrator can execute checkout via 2PC with crash recovery, switchable with SAGA via env var
**Depends on**: Phase 10, Phase 11
**Requirements**: TPC-04, TPC-05, TPC-06, TPC-07
**Success Criteria** (what must be TRUE):
  1. 2PC coordinator sends concurrent PREPARE to Stock and Payment, collects votes, and executes COMMIT or ABORT
  2. Coordinator decision is persisted to Redis BEFORE sending phase-2 messages (WAL pattern) -- crash between phases recovers correctly
  3. Recovery scanner distinguishes SAGA and 2PC transactions by protocol field and applies correct recovery logic
  4. Setting `TRANSACTION_PATTERN=saga` uses SAGA; setting `TRANSACTION_PATTERN=2pc` uses 2PC -- no other code changes needed
  5. 2PC+gRPC checkout completes end-to-end; 2PC+queue checkout completes end-to-end
**Plans**: TBD

Plans:
- [ ] 12-01: TBD
- [ ] 12-02: TBD

### Phase 13: Integration & Benchmark
**Goal**: All 4 mode combinations are validated for correctness under normal operation, container failures, and benchmark load
**Depends on**: Phase 12
**Requirements**: INT-01, INT-02, INT-03
**Success Criteria** (what must be TRUE):
  1. All existing integration tests pass in all 4 mode combinations (SAGA/gRPC, SAGA/queue, 2PC/gRPC, 2PC/queue)
  2. Kill-test produces 0 consistency violations (no lost money or items) in 2PC mode
  3. wdm-project-benchmark passes with 0 consistency violations in all 4 modes
**Plans**: TBD

Plans:
- [ ] 13-01: TBD
- [ ] 13-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 8 -> 9 -> 10 -> 11 -> 12 -> 13
Note: Phases 9-10 (queue) and Phase 11 (2PC state machine) can proceed in parallel after Phase 8. Phase 12 requires both Phase 10 and Phase 11.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Async Foundation | v1.0 | 3/3 | Complete | 2026-02-28 |
| 2. gRPC Communication | v1.0 | 4/4 | Complete | 2026-02-28 |
| 3. SAGA Orchestration | v1.0 | 4/4 | Complete | 2026-02-28 |
| 4. Fault Tolerance | v1.0 | 2/2 | Complete | 2026-02-28 |
| 5. Event-Driven Architecture | v1.0 | 2/2 | Complete | 2026-02-28 |
| 6. Infrastructure | v1.0 | 3/3 | Complete | 2026-03-01 |
| 7. Validation and Delivery | v1.0 | 3/3 | Complete | 2026-03-01 |
| 8. Business Logic Extraction | v2.0 | 0/2 | Not started | - |
| 9. Queue Infrastructure | 2/2 | Complete   | 2026-03-12 | - |
| 10. Transport Adapter | 1/1 | Complete    | 2026-03-12 | - |
| 11. 2PC State Machine & Participants | v2.0 | 0/2 | Not started | - |
| 12. 2PC Coordinator & Recovery | v2.0 | 0/? | Not started | - |
| 13. Integration & Benchmark | v2.0 | 0/? | Not started | - |
