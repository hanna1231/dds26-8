# Roadmap: DDS26-8 Distributed Checkout System

## Milestones

- ✅ **v1.0 Distributed Checkout System** -- Phases 1-7 (shipped 2026-03-11)
- ✅ **v2.0 2PC & Message Queues** -- Phases 8-13 (shipped 2026-03-26)
- 🚧 **v3.0 Abstract Orchestrator & Refactoring** -- Phases 14-18 (in progress)

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

<details>
<summary>v2.0 2PC & Message Queues (Phases 8-13) -- SHIPPED 2026-03-26</summary>

- [x] Phase 8: Business Logic Extraction (0/2 plans) -- completed 2026-03-26
- [x] Phase 9: Queue Infrastructure (2/2 plans) -- completed 2026-03-12
- [x] Phase 10: Transport Adapter (1/1 plans) -- completed 2026-03-12
- [x] Phase 11: 2PC State Machine & Participants (2/2 plans) -- completed 2026-03-12
- [x] Phase 12: 2PC Coordinator & Recovery (2/2 plans) -- completed 2026-03-12
- [x] Phase 13: Integration & Benchmark (1/2 plans) -- completed 2026-03-26

</details>

### v3.0 Abstract Orchestrator & Refactoring

**Milestone Goal:** Abstract SAGA/2PC coordination into a generic workflow engine artifact and refactor the codebase for quality, maintainability, and interview readiness. The checkout transaction is re-expressed as a WorkflowDefinition; the engine drives execution without knowing about Stock or Payment. Both SAGA and 2PC run through the same engine entry point.

**Phase Numbering:**
- Integer phases (14, 15, ...): Planned milestone work
- Decimal phases (14.1, 14.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 14: Engine Core** - Define WorkflowStep/WorkflowDefinition data model and build generic Redis-persisted WorkflowStore with Lua CAS transitions (completed 2026-03-27)
- [x] **Phase 15: Execution Strategies** - Implement SagaStrategy (sequential + reverse compensation) and TwoPhaseStrategy (concurrent prepare + WAL commit/abort) (completed 2026-03-27)
- [x] **Phase 16: WorkflowEngine + Checkout Definition** - Wire store and strategies into WorkflowEngine.execute() and rewrite checkout as a WorkflowDefinition factory (completed 2026-03-27)
- [ ] **Phase 17: Wiring** - Replace hardcoded orchestration in grpc_server.py with engine.execute() and generalize recovery scanner to use engine API
- [ ] **Phase 18: Cleanup & Refactoring** - Delete saga.py/tpc.py after validation, add named step logging, make engine injectable, and clean up the codebase

## Phase Details

### Phase 14: Engine Core
**Goal**: Generic workflow persistence and data model are defined -- WorkflowStore handles all Redis state transitions via Lua CAS and WorkflowStep/WorkflowDefinition types give strategies and the engine a shared interface
**Depends on**: Nothing (first phase of v3.0; builds on v2.0 Redis/Lua patterns)
**Requirements**: ENG-01, ENG-02, ENG-04, ENG-05
**Success Criteria** (what must be TRUE):
  1. `WorkflowStep` dataclass exists with name, async action callable, and async compensation callable fields
  2. `WorkflowDefinition` dataclass exists with name, ordered steps list, and strategy field (saga/2pc)
  3. `WorkflowStore.create()` initializes a workflow Redis hash using HSETNX -- concurrent calls for the same workflow_id are safe (exactly-once guarantee preserved)
  4. `WorkflowStore.transition()` uses the extracted Lua CAS script -- invalid state transitions are rejected atomically
  5. `WorkflowStore.mark_step_done()` writes `step_N_done` flags replacing hardcoded `stock_reserved`/`payment_charged` field names
**Plans:** 1/1 plans complete
Plans:
- [ ] 14-01-PLAN.md -- TDD: WorkflowStep/WorkflowDefinition types + WorkflowStore with Lua CAS

### Phase 15: Execution Strategies
**Goal**: SAGA and 2PC execution logic lives in isolated, testable strategy classes that drive any WorkflowDefinition without knowledge of specific services
**Depends on**: Phase 14
**Requirements**: STR-01, STR-02, STR-03, STR-04
**Success Criteria** (what must be TRUE):
  1. `SagaStrategy.execute()` runs steps sequentially in definition order with bounded forward retry -- a step failure halts forward progress and triggers compensation
  2. `SagaStrategy.compensate()` runs compensations in reverse step order with infinite retry -- each step's registered compensation callable is invoked, never a hardcoded flag check
  3. `TwoPhaseStrategy.execute()` sends prepare concurrently to all steps, writes WAL decision (COMMITTING state) before sending phase-2 messages, and calls abort if any prepare fails
  4. Both strategies accept and execute the same `WorkflowDefinition` object -- strategy selection is driven by the definition's `strategy` field, not by caller logic
**Plans:** 2/2 plans complete
Plans:
- [x] 15-01-PLAN.md -- Extract retry utilities + SagaStrategy with tests (STR-01, STR-02, STR-04)
- [x] 15-02-PLAN.md -- TwoPhaseStrategy with tests + STR-04 cross-strategy proof (STR-03, STR-04)

### Phase 16: WorkflowEngine + Checkout Definition
**Goal**: WorkflowEngine.execute() is the single entry point for all transaction coordination and checkout is expressed as a WorkflowDefinition factory -- the engine knows nothing about Stock or Payment
**Depends on**: Phase 15
**Requirements**: ENG-03, CHK-01
**Success Criteria** (what must be TRUE):
  1. `WorkflowEngine.execute(workflow_id, definition, context)` routes to the correct strategy based on the definition's strategy field and publishes lifecycle events
  2. `make_checkout_workflow()` in `checkout_workflow.py` returns a `WorkflowDefinition` whose steps are closures over `transport.py` functions -- no Stock/Payment service names appear in the engine or strategy modules
  3. A full happy-path checkout driven through `engine.execute()` completes successfully with correct Redis state transitions
  4. A stock failure mid-checkout triggers the registered compensation path and leaves no partial reservations
**Plans:** 2/2 plans complete
Plans:
- [ ] 16-01-PLAN.md -- TDD: WorkflowEngine with strategy routing + lifecycle events (ENG-03)
- [ ] 16-02-PLAN.md -- TDD: Checkout workflow definition factory with transport closures (CHK-01)

### Phase 17: Wiring
**Goal**: The running system uses the workflow engine for all checkout coordination -- grpc_server.py, recovery.py, and consumers.py are updated to call engine APIs and all 37 integration tests pass
**Depends on**: Phase 16
**Requirements**: CHK-02, CHK-03
**Success Criteria** (what must be TRUE):
  1. `grpc_server.py` calls only `engine.execute()` for checkout -- all hardcoded `run_checkout()` / `run_2pc_checkout()` call sites are gone
  2. Recovery scanner calls `engine.resume()` for incomplete workflows -- it covers both SAGA and 2PC transactions discovered at startup
  3. All 37 existing integration tests pass in both `COMM_MODE=grpc` and `COMM_MODE=queue` modes
  4. Kill-test produces 0 consistency violations (no lost money or items) after the wiring change
**Plans:** 2 plans
Plans:
- [ ] 17-01-PLAN.md -- Wire grpc_server.py to engine.execute() with duplicate detection + fix tests (CHK-02)
- [ ] 17-02-PLAN.md -- Add engine.resume() + rewrite recovery.py + update consumers.py (CHK-03)

### Phase 18: Cleanup & Refactoring
**Goal**: The codebase is clean, the superseded modules are deleted, and all log lines carry workflow context -- the engine is ready for demo and code review
**Depends on**: Phase 17
**Requirements**: REF-01, REF-02, REF-03, REF-04
**Success Criteria** (what must be TRUE):
  1. `saga.py` and `tpc.py` are deleted -- `grep -r "from saga import\|from tpc import" orchestrator/` returns no matches
  2. All execution log lines include the step name and workflow_id -- a checkout trace in logs shows the named sequence (e.g., "reserve_stock", "charge_payment")
  3. `WorkflowEngine` is instantiated in `app.py` and injected as a dependency -- no module-level engine singleton or global mutable state exists in any engine module
  4. Benchmark passes with 0 consistency violations after all refactoring changes
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute sequentially: 14 -> 15 -> 16 -> 17 -> 18
Each phase's output is the next phase's direct input -- no parallel execution within v3.0.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Async Foundation | v1.0 | 3/3 | Complete | 2026-02-28 |
| 2. gRPC Communication | v1.0 | 4/4 | Complete | 2026-02-28 |
| 3. SAGA Orchestration | v1.0 | 4/4 | Complete | 2026-02-28 |
| 4. Fault Tolerance | v1.0 | 2/2 | Complete | 2026-02-28 |
| 5. Event-Driven Architecture | v1.0 | 2/2 | Complete | 2026-02-28 |
| 6. Infrastructure | v1.0 | 3/3 | Complete | 2026-03-01 |
| 7. Validation and Delivery | v1.0 | 3/3 | Complete | 2026-03-01 |
| 8. Business Logic Extraction | v2.0 | 0/2 | Complete | 2026-03-26 |
| 9. Queue Infrastructure | v2.0 | 2/2 | Complete | 2026-03-12 |
| 10. Transport Adapter | v2.0 | 1/1 | Complete | 2026-03-12 |
| 11. 2PC State Machine & Participants | v2.0 | 2/2 | Complete | 2026-03-12 |
| 12. 2PC Coordinator & Recovery | v2.0 | 2/2 | Complete | 2026-03-12 |
| 13. Integration & Benchmark | v2.0 | 1/2 | Complete | 2026-03-26 |
| 14. Engine Core | v3.0 | 1/1 | Complete    | 2026-03-27 |
| 15. Execution Strategies | v3.0 | 2/2 | Complete    | 2026-03-27 |
| 16. WorkflowEngine + Checkout Definition | v3.0 | 0/2 | Complete    | 2026-03-27 |
| 17. Wiring | v3.0 | 0/2 | Not started | - |
| 18. Cleanup & Refactoring | v3.0 | 0/TBD | Not started | - |
