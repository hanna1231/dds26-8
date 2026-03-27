# Requirements: DDS26-8 Distributed Checkout System

**Defined:** 2026-03-27
**Core Value:** Checkout transactions must never lose money or item counts -- consistency is non-negotiable, even when containers crash mid-transaction.

## v3.0 Requirements

Requirements for v3.0 milestone. Each maps to roadmap phases.

### Workflow Engine Core

- [ ] **ENG-01**: WorkflowStep dataclass with name, async action callable, and async compensation callable
- [ ] **ENG-02**: WorkflowDefinition dataclass with name, ordered steps list, and strategy field (saga/2pc)
- [ ] **ENG-03**: WorkflowEngine class with execute(workflow_id, definition, context) entry point that routes to strategy
- [ ] **ENG-04**: Durable workflow state persisted in Redis using existing Lua CAS transition pattern
- [ ] **ENG-05**: Per-step completion flags (step_N_done) replacing hardcoded field names (stock_reserved, payment_charged)

### Execution Strategies

- [ ] **STR-01**: SAGA strategy executor with forward step execution and bounded retry
- [ ] **STR-02**: SAGA compensation with reverse-order step undoing and infinite retry
- [ ] **STR-03**: 2PC strategy executor with concurrent prepare, WAL decision write, and phase-2 commit/abort
- [ ] **STR-04**: Both strategies callable from the same WorkflowDefinition (strategy field selects execution path)

### Checkout Abstraction

- [ ] **CHK-01**: checkout_workflow.py defining checkout as WorkflowDefinition using transport.py functions
- [ ] **CHK-02**: grpc_server.py refactored to receive WorkflowEngine and call engine.execute() only
- [ ] **CHK-03**: Recovery scanner generalized to read workflow state and resume via engine API

### Refactoring & Cleanup

- [ ] **REF-01**: saga.py and tpc.py deleted after engine migration is validated
- [ ] **REF-02**: Named step execution logging (step names in log lines with workflow_id context)
- [ ] **REF-03**: WorkflowEngine as injectable dependency (no global mutable state in engine module)
- [ ] **REF-04**: General codebase cleanup for clarity, consistency, and maintainability

## Future Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Engine Features

- **ADV-01**: Workflow versioning (Temporal-style deterministic replay)
- **ADV-02**: Signals and queries for running workflows
- **ADV-03**: Child workflow support
- **ADV-04**: Activity worker pools (separate processes)
- **ADV-05**: Per-step timeout configuration
- **ADV-06**: WorkflowEngine.get_status() for observability

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Full event sourcing / history replay | Massive complexity; Redis hash WAL is sufficient |
| Separate worker processes for activities | Not needed; in-process callables via transport adapter |
| Dynamic step sequences at runtime | Untestable in course timeline; static registration sufficient |
| Workflow visualization UI | No grade benefit; step names in logs sufficient |
| Full Temporal/Cadence feature parity | Course project scope; only core workflow abstraction needed |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ENG-01 | — | Pending |
| ENG-02 | — | Pending |
| ENG-03 | — | Pending |
| ENG-04 | — | Pending |
| ENG-05 | — | Pending |
| STR-01 | — | Pending |
| STR-02 | — | Pending |
| STR-03 | — | Pending |
| STR-04 | — | Pending |
| CHK-01 | — | Pending |
| CHK-02 | — | Pending |
| CHK-03 | — | Pending |
| REF-01 | — | Pending |
| REF-02 | — | Pending |
| REF-03 | — | Pending |
| REF-04 | — | Pending |

**Coverage:**
- v3.0 requirements: 16 total
- Mapped to phases: 0
- Unmapped: 16

---
*Requirements defined: 2026-03-27*
*Last updated: 2026-03-27 after initial definition*
