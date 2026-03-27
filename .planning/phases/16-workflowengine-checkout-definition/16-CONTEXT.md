# Phase 16: WorkflowEngine + Checkout Definition - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire WorkflowStore and strategies into WorkflowEngine.execute() as the single entry point for all transaction coordination. Rewrite checkout as a WorkflowDefinition factory (make_checkout_workflow) using transport.py functions as step closures. The engine knows nothing about Stock or Payment.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion

All implementation decisions deferred to Claude's judgment. Key areas and likely approaches based on codebase analysis:

**Engine lifecycle events:**
- D-07 from Phase 15 says strategies don't publish events -- engine does
- Current events.py uses saga-specific naming (saga_id, saga:events stream)
- Claude decides: reuse existing events.py with minimal generalization (workflow_id param), or wrap calls. Stream name and field naming are implementation details.
- Events to publish: started, step_completed, succeeded, failed (matching existing event types in events.py)

**Checkout closure design:**
- make_checkout_workflow() returns WorkflowDefinition with closures over transport.py functions
- STATE.md flags Python late-binding as known pitfall -- use default-arg capture pattern (lambda s=step, c=context: ...) already established in saga_strategy.py:86
- Context dict (order_id, user_id, items) flows through the context parameter that strategies already pass to step callables

**Engine API surface:**
- WorkflowEngine receives WorkflowStore injected (REF-03 alignment, same pattern as strategies)
- execute(workflow_id, definition, context) is the primary entry point
- Strategy instances can be pre-registered or instantiated on the fly -- both are stateless
- get_status() is ADV-06 (Future Requirements) -- skip for now

**Strategy selection:**
- Simple dict registry mapping "saga" -> SagaStrategy, "2pc" -> TwoPhaseStrategy
- definition.strategy field selects the executor
- No dynamic registration needed (Out of Scope confirms this)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 14/15 Outputs (direct dependencies)
- `orchestrator/workflow_types.py` -- WorkflowStep and WorkflowDefinition dataclasses
- `orchestrator/workflow_store.py` -- WorkflowStore with Lua CAS transitions
- `orchestrator/saga_strategy.py` -- SagaStrategy.execute() and compensate()
- `orchestrator/tpc_strategy.py` -- TwoPhaseStrategy.execute()
- `orchestrator/retry.py` -- retry_forward() and retry_forever() shared utilities

### Transport Layer (step callable source)
- `orchestrator/transport.py` -- 12 domain functions that become WorkflowStep action/compensation callables

### Event Publishing
- `orchestrator/events.py` -- publish_event() fire-and-forget pattern, stream naming

### Existing Checkout Logic (reference for closure design)
- `orchestrator/grpc_server.py` -- run_checkout() and run_2pc_checkout() show how transport functions are called with order_id/user_id/items context

### Requirements
- `.planning/REQUIREMENTS.md` -- ENG-03 (WorkflowEngine.execute), CHK-01 (checkout_workflow.py)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `SagaStrategy` and `TwoPhaseStrategy` -- both expose identical `execute(workflow_id, definition, context, store)` interface
- `WorkflowStore` -- injectable class, ready to pass to engine
- `events.py:publish_event()` -- fire-and-forget pattern, needs workflow_id adaptation
- `transport.py` -- 12 domain functions already exported, transport-agnostic

### Established Patterns
- Strategies are stateless (no constructor params) -- engine can hold singleton instances
- Lambda default-arg capture for closures: `lambda s=step, c=context: s.action(c)` (saga_strategy.py:86)
- Context dict threading: strategies pass context to step callables
- Fire-and-forget event publishing: never blocks checkout path

### Integration Points
- New `orchestrator/workflow_engine.py` -- WorkflowEngine class
- New `orchestrator/workflows/checkout.py` (or `orchestrator/checkout_workflow.py`) -- make_checkout_workflow() factory
- Engine receives WorkflowStore via constructor (injectable dependency)
- Engine calls strategy.execute() and wraps with event publishing

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 16-workflowengine-checkout-definition*
*Context gathered: 2026-03-27*
