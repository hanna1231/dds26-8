# Phase 14: Engine Core - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Define WorkflowStep/WorkflowDefinition data model and build generic Redis-persisted WorkflowStore with Lua CAS transitions. This phase produces the data types and persistence layer that strategies (Phase 15) and the engine (Phase 16) build on.

</domain>

<decisions>
## Implementation Decisions

### Redis Key Prefix Scheme
- **D-01:** Use unified `{workflow:<workflow_id>}` key prefix for all workflow records. The existing `{saga:*}` and `{tpc:*}` prefixes stay untouched in saga.py/tpc.py until Phase 18 deletion. Recovery scanner update happens in Phase 17.

### Step Completion Tracking
- **D-02:** Use flat hash fields `step_0_done`, `step_1_done`, etc. as completion flags — directly replacing hardcoded `stock_reserved`/`payment_charged` fields. No nested JSON or bitmaps. Consistent with existing HSET/HGET patterns.

### WorkflowDefinition Data Model
- **D-03:** Minimal dataclass: `name` (str), `steps` (list[WorkflowStep]), `strategy` (str literal "saga" | "2pc"). No timeout config, retry policy, or metadata fields. Retry behavior is strategy-internal (already exists in SAGA compensation logic).

### State Machine Design
- **D-04:** WorkflowStore is state-agnostic — it performs blind Lua CAS transitions (`if current == expected then set new`). Each strategy defines its own state enum and valid transitions dict. The store never validates state names; strategies validate before calling `store.transition()`.

### Claude's Discretion
All four areas above were deferred to Claude's judgment. Implementation details including:
- Exact dataclass field types and defaults
- WorkflowStore method signatures beyond what success criteria require
- Lua script structure (can extract verbatim from saga.py/tpc.py since they're identical)
- Hash field naming conventions for workflow metadata (order_id, user_id, items_json, etc.)
- TTL policy (7-day expiry pattern from existing code)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing State Machine Patterns (extraction sources)
- `orchestrator/saga.py` — SAGA state definitions, Lua CAS script, create/transition/get functions. The TRANSITION_LUA script is the verbatim extraction target.
- `orchestrator/tpc.py` — 2PC state definitions, identical Lua CAS script, create/transition/get functions. Confirms the pattern is protocol-agnostic.

### Transport Layer (step callable source)
- `orchestrator/transport.py` — Exports 12 domain functions (reserve_stock, charge_payment, etc.) that become WorkflowStep action/compensation callables in Phase 16.

### Requirements
- `.planning/REQUIREMENTS.md` — ENG-01 (WorkflowStep), ENG-02 (WorkflowDefinition), ENG-04 (Redis Lua CAS), ENG-05 (per-step completion flags)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `TRANSITION_LUA` script (identical in saga.py:42-49 and tpc.py:43-50) — extract verbatim into workflow_store.py
- HSETNX creation pattern (saga.py:83, tpc.py:84) — reuse for exactly-once workflow creation
- `hgetall` + byte decode pattern (saga.py:165-168, tpc.py:164-167) — reuse for workflow retrieval

### Established Patterns
- Redis hash per workflow record with `state` as primary field
- Hash tag in key prefix `{saga:id}` ensures cluster slot locality — maintain with `{workflow:id}`
- 7-day TTL via `db.expire()` after creation
- `json.dumps(items)` for list-of-dict serialization in hash fields
- All functions are async, accept raw `db` client (no decode_responses)

### Integration Points
- New `orchestrator/workflow_store.py` module — parallel to saga.py/tpc.py, not replacing them yet
- New `orchestrator/workflow_types.py` module — WorkflowStep and WorkflowDefinition dataclasses
- Both saga.py and tpc.py remain untouched until Phase 18

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

*Phase: 14-engine-core*
*Context gathered: 2026-03-27*
