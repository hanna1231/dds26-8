# Phase 15: Execution Strategies - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement SagaStrategy (sequential + reverse compensation) and TwoPhaseStrategy (concurrent prepare + WAL commit/abort) as isolated, testable classes that drive any WorkflowDefinition without knowledge of specific services. Both strategies use WorkflowStore from Phase 14 for state persistence.

</domain>

<decisions>
## Implementation Decisions

### Strategy Interface Design
- **D-01:** Both strategies expose `async execute(workflow_id, definition, context, store)` where `store` is a `WorkflowStore` instance (injectable per REF-03), `definition` is a `WorkflowDefinition`, and `context` is a dict of domain metadata (order_id, user_id, etc.).
- **D-02:** `SagaStrategy` additionally exposes a public `async compensate(workflow_id, definition, context, store)` method because the recovery scanner (Phase 17) needs to trigger compensation independently of forward execution.
- **D-03:** `TwoPhaseStrategy` does NOT have a separate compensate — abort is integral to the execute flow (if any prepare fails, abort all within execute).

### Retry Policy Ownership
- **D-04:** Extract `retry_forward()` and `retry_forever()` from `grpc_server.py` into a new `orchestrator/retry.py` shared module. Strategies import and use them directly. No configuration surface — existing defaults (max 3 attempts for forward, infinite for compensation, full-jitter exponential backoff) are proven in production.

### State Enum Placement
- **D-05:** Each strategy module defines its own state constants and `VALID_TRANSITIONS` dict (per Phase 14 D-04: store is state-agnostic). Reuse exact state values from existing code:
  - SAGA: `STARTED`, `STOCK_RESERVED`, `PAYMENT_CHARGED`, `COMPLETED`, `COMPENSATING`, `FAILED`
  - 2PC: `INIT`, `PREPARING`, `COMMITTING`, `ABORTING`, `COMMITTED`, `ABORTED`
- **D-06:** Strategies validate transitions before calling `store.transition()`. Invalid transitions raise `ValueError` (same pattern as existing saga.py/tpc.py).

### Event Publishing Scope
- **D-07:** Strategies do NOT publish events. They return structured result dicts (`{"success": bool, "error_message": str}`). Event publishing is deferred to the WorkflowEngine (Phase 16), keeping strategies testable without mocking event infrastructure.

### Claude's Discretion
All decisions above were deferred to Claude's judgment. Additional implementation details at Claude's discretion:
- Exact method signatures beyond what's specified in D-01/D-02/D-03
- How SagaStrategy tracks which steps completed for partial compensation (internal list vs reading store)
- Whether strategies are stateless classes or carry constructor params
- Test structure (unit tests with mock callables vs integration tests with Redis)
- How `context` dict is threaded through step callables

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Extraction Sources (existing execution logic)
- `orchestrator/grpc_server.py` — `run_checkout()` (lines 183-344) is the SAGA execution to generalize; `run_2pc_checkout()` (lines 351-453) is the 2PC execution to generalize; `retry_forward()` and `retry_forever()` (lines 46-112) are the retry utilities to extract
- `orchestrator/saga.py` — SAGA_STATES and VALID_TRANSITIONS (lines 14-28) to reuse in SagaStrategy
- `orchestrator/tpc.py` — TPC_STATES and TPC_VALID_TRANSITIONS (lines 15-29) to reuse in TwoPhaseStrategy

### Phase 14 Outputs (dependencies)
- `orchestrator/workflow_types.py` — WorkflowStep and WorkflowDefinition dataclasses that strategies consume
- `orchestrator/workflow_store.py` — WorkflowStore that strategies use for state persistence

### Requirements
- `.planning/REQUIREMENTS.md` — STR-01 (SAGA forward execution), STR-02 (SAGA compensation), STR-03 (2PC execution), STR-04 (both strategies accept same WorkflowDefinition)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `retry_forward()` (grpc_server.py:77-112) — bounded retry with full-jitter exponential backoff, CircuitBreakerError bypass. Extract to retry.py.
- `retry_forever()` (grpc_server.py:46-70) — infinite retry with exponential backoff. Extract to retry.py.
- SAGA_STATES/VALID_TRANSITIONS (saga.py:14-28) — copy into SagaStrategy module
- TPC_STATES/TPC_VALID_TRANSITIONS (tpc.py:15-29) — copy into TwoPhaseStrategy module

### Established Patterns
- Step callables return `{"success": bool, "error_message": str}` — strategies should expect this interface
- `asyncio.gather(*futures, return_exceptions=True)` for concurrent 2PC prepare
- Compensation reads flags from Redis to avoid double-execution (idempotent per step)
- Forward steps use lambda closures with default args to avoid late-binding bug

### Integration Points
- New `orchestrator/saga_strategy.py` — SagaStrategy class
- New `orchestrator/tpc_strategy.py` — TwoPhaseStrategy class
- New `orchestrator/retry.py` — extracted retry utilities
- New `tests/test_strategies.py` — strategy tests with mock callables
- WorkflowStore.transition(), mark_step_done(), create(), get() — called by strategies

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 15-execution-strategies*
*Context gathered: 2026-03-27*
