# Feature Research

**Domain:** Abstract workflow engine orchestrator (Cadence/Temporal-inspired, SAGA + 2PC, course project)
**Researched:** 2026-03-26
**Confidence:** HIGH (based on existing codebase analysis + Temporal/Cadence/Dapr documentation)

---

## Context: What Already Exists

The existing `orchestrator/` service has all the *mechanics* of a workflow engine but zero abstraction:

- `grpc_server.py` — `run_checkout()` and `run_2pc_checkout()` hardcode Stock/Payment service calls
- `saga.py` — Lua CAS state machine, already generic enough to reuse
- `tpc.py` — Lua CAS state machine, already generic enough to reuse
- `transport.py` — re-exports service-specific named functions (`reserve_stock`, `charge_payment`, etc.)
- `recovery.py` — hardcodes SAGA/TPC state names and Stock/Payment step logic

The v3.0 goal is NOT to rebuild from scratch. It is to extract an engine layer that executes abstract step sequences, then re-express checkout as a workflow definition using that engine. The engine must not know about Stock or Payment.

---

## How Temporal/Cadence Approach This

Temporal and Cadence split execution into two roles:

**Workflow** — deterministic orchestration logic. Defines what steps to run, in what order, with what compensations. Does not touch external services directly. Does not know which services exist.

**Activity** — non-deterministic work unit. An atomic callable that contacts an external service (reserve stock, charge payment). Can fail, be retried, and be compensated.

The key insight from Temporal's design: the engine executes a *registered* sequence of `(action, compensation)` pairs. It knows nothing about what those callables do. The workflow definition registers the steps. The engine handles execution, persistence, retry, and compensation.

For this project, "Activities" are the functions already in `transport.py`. The gap is the registration and execution layer that sits between the hardcoded orchestration logic and those transport functions.

---

## Feature Landscape

### Table Stakes (Must Exist for the Abstraction to Work)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| `WorkflowStep` dataclass: `(name, action, compensation)` | Every workflow engine (Temporal, Cadence, liteflow, py-saga-orchestration) uses this as the atomic unit — a paired forward+undo callable | LOW | `action` and `compensation` are async callables with signature `(ctx: dict) -> dict`; `name` is used for idempotency key derivation and log lines |
| `WorkflowDefinition` dataclass: `(name, steps, strategy)` | Engine needs to know what to execute without knowing what steps do; strategy selects SAGA vs 2PC execution path | LOW | `strategy: Literal["saga", "2pc"]`; registered once at startup, reused per execution |
| `WorkflowEngine.execute(workflow_id, definition, context)` | Single entry point replaces `run_checkout()` and `run_2pc_checkout()`; caller is transport-agnostic | MEDIUM | Routes to `_run_saga()` or `_run_2pc()` internally based on `definition.strategy`; returns `{"success": bool, "error_message": str}` |
| Durable execution state in Redis | Engine must survive crashes — proven pattern from `saga.py`/`tpc.py`; without persistence, recovery is impossible | LOW | Reuse Lua CAS pattern; key schema becomes `{workflow:<id>}` instead of `{saga:<id>}` or `{tpc:<id>}`; stores step index, completion flags, strategy |
| Per-step completion flags | Prevents double-execution of compensations on crash recovery; `stock_restored`/`refund_done` generalized to `step_0_done`, `step_1_done` | LOW | Flag written atomically with Lua CAS transition; read by compensation and recovery to know which steps ran |
| Reverse-order compensation on step failure | Core SAGA guarantee — engine triggers compensation without knowing what it is undoing; only knows step index and whether it completed | MEDIUM | Track completed step indices; iterate in reverse calling `step.compensation(ctx)` for each completed step; infinite retry for each compensation callable |
| Recovery scanner updated to engine API | Recovery must resume workflows without knowing about Stock/Payment specifics; must read generic state and call engine resume | MEDIUM | `recover_incomplete_workflows()` scans `{workflow:*}` keys, reads strategy + step index, delegates to engine |
| Checkout re-expressed as workflow definition | Demonstrates the abstraction is real; validates that the engine works end-to-end | LOW | New `checkout_workflow.py` creates `WorkflowDefinition` with steps pointing to `transport.py` functions; `grpc_server.py` only calls `engine.execute()` |

### Differentiators (Grade-Relevant, Course Demo Value)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Same `WorkflowDefinition` runs under both SAGA and 2PC | Demonstrates the engine is genuinely generic — step callables are reused, execution protocol differs | MEDIUM | `WorkflowDefinition.strategy` field selects execution path at registration time; no step change needed to switch patterns |
| Named steps with execution log | Human-readable log lines like `"[workflow:abc] step reserve_stock: OK"` instead of scattered messages; aids debugging during live demo | LOW | Use `step.name` in all log lines within engine; zero cost, high observability value |
| Context dict passed through to all step callables | Steps like `reserve_stock` need `order_id`, `user_id`, `items`, `total_cost` — passing as opaque dict keeps engine agnostic | LOW | All callables share signature `async (ctx: dict) -> dict`; engine never reads ctx contents |
| `WorkflowEngine` as injectable dependency | Instantiate with `db` reference; pass to servicer constructor; makes unit testing straightforward without Redis | LOW | No global mutable state in engine module; all state is in Redis or passed via arguments |

### Anti-Features (Explicitly Out of Scope)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Workflow versioning (Temporal-style) | "What if checkout logic changes while workflows are in-flight?" | Requires execution history replay, determinism constraints, version branching — massive complexity for zero course grade benefit | Document deployed version; clear Redis on breaking schema changes during development |
| Signals and queries (Temporal-style) | "Can external callers inject events into a running workflow?" | Requires persistent mailboxes, signal delivery guarantees, async handoff — not needed for synchronous request-response checkout | Checkout is request-response; caller waits for `execute()` to return |
| Child workflows | "Can one workflow trigger another workflow?" | Recursive engine calls, cross-workflow compensation coordination — not needed for single-domain checkout | Flat step sequence is sufficient for this use case |
| Activity worker pools | "Can activities run on separate worker processes?" | Requires worker registration, task queues, heartbeating — that is a full Temporal deployment, not an in-process engine | Activities are in-process callables; `transport.py` already abstracts the remote call |
| Full event sourcing (rebuild state from event replay) | "Can we reconstruct workflow state from Redis Streams history?" | Stream cleanup complexity, snapshot gaps, replay ordering — significantly complex for zero grade benefit | Redis hash as write-ahead log (current pattern) is sufficient; per-step flags are the durable state |
| Dynamic step sequences (add steps at runtime) | "Can the checkout workflow change which steps run based on conditions?" | Requires re-entrant execution, mid-flight schema changes — untestable in course timeline | Register all steps at startup; strategy enum provides sufficient flexibility |
| Timeout-per-step (Temporal-style activity timeouts) | "What if a step hangs forever?" | Adds per-step timer management to the engine core — significant complexity | Circuit breakers on transport layer already prevent hangs; `retry_forward` bounded attempts already cap wait time |
| UI or workflow visualization | "Show a graph of workflow state" | Scope creep; no grade benefit | Step names in log lines are sufficient observability for course demo |

---

## Feature Dependencies

```
WorkflowStep (name, action, compensation)
    └──required-by──> WorkflowDefinition (steps list + strategy)
                          └──required-by──> WorkflowEngine.execute()
                                                └──required-by──> checkout_workflow.py definition
                                                └──required-by──> recovery scanner

Per-step completion flags (step_N_done in Redis hash)
    └──required-by──> Reverse-order compensation (knows which steps to undo)
    └──required-by──> Recovery scanner (knows which step to resume from)

Durable execution state ({workflow:<id>} Redis hash)
    └──required-by──> Per-step completion flags
    └──required-by──> Strategy field (stored for recovery to know which executor to use)

WorkflowEngine injectable dependency
    └──enhances──> Unit testability (no global state)
    └──required-by──> grpc_server.py refactor (receives engine in constructor)
```

### Dependency Notes

- `WorkflowStep` is the atom — must exist before `WorkflowDefinition`.
- Per-step flags must be designed before writing compensation logic — compensation reads flags to decide what to undo.
- The durable state Redis schema (field names) must be finalized before writing recovery — recovery must know exactly what fields to read.
- `checkout_workflow.py` depends on the engine API being stable — write it after the engine contract is settled.
- Recovery scanner depends on checkout workflow definition existing to know what step callables to pass to engine resume.

---

## MVP Definition

### Launch With (v3.0 — course deadline)

- [ ] `WorkflowStep` dataclass: `name: str`, `action: Callable[[dict], Awaitable[dict]]`, `compensation: Callable[[dict], Awaitable[dict]]` — the atomic unit
- [ ] `WorkflowDefinition` dataclass: `name: str`, `steps: list[WorkflowStep]`, `strategy: Literal["saga", "2pc"]` — the registration object
- [ ] `WorkflowEngine` class: `__init__(db)`, `execute(workflow_id, definition, context) -> dict` — single entry point
- [ ] Internal SAGA executor: forward execution with bounded retry, reverse compensation with infinite retry, Lua CAS state transitions — migrated and generalized from `run_checkout()`
- [ ] Internal 2PC executor: concurrent prepare phase, WAL decision write, phase-2 commit/abort — migrated and generalized from `run_2pc_checkout()`
- [ ] Durable state schema: `{workflow:<id>}` hash with `step_N_done` flags replacing `stock_reserved`/`payment_charged`; `strategy` and `step_count` stored for recovery
- [ ] `checkout_workflow.py`: `WorkflowDefinition` with steps pointing to `transport.py` functions; engine knows nothing about Stock or Payment
- [ ] Recovery scanner generalized: `recover_incomplete_workflows()` reads `{workflow:*}` keys, resumes via engine
- [ ] `grpc_server.py` refactored: `OrchestratorServiceServicer` receives `WorkflowEngine` instance, calls `engine.execute()` only

### Add After Core Works (stretch within v3.0)

- [ ] Structured execution log — step names in log lines, engine logs step success/failure with workflow_id context
- [ ] `WorkflowEngine.get_status(workflow_id) -> dict` — reads Redis hash, returns current step, state, error; useful for health endpoint enrichment

### Defer (Out of Scope for Course)

- [ ] Workflow versioning
- [ ] Signals and queries
- [ ] Activity worker pools
- [ ] Dynamic step sequences
- [ ] Per-step timeouts
- [ ] Workflow visualization

---

## Feature Prioritization Matrix

| Feature | Grade Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| `WorkflowStep` + `WorkflowDefinition` dataclasses | HIGH — defines the abstraction | LOW — pure data, no logic | P1 |
| `WorkflowEngine.execute()` routing dispatch | HIGH — entry point for all execution | LOW — thin strategy dispatch | P1 |
| Internal SAGA executor (migrated from `run_checkout`) | HIGH — existing logic, new generic home | MEDIUM — refactor field names, step indexing | P1 |
| Internal 2PC executor (migrated from `run_2pc_checkout`) | HIGH — shows both patterns in one engine | MEDIUM — same refactor as SAGA | P1 |
| Durable state schema generalization | HIGH — enables crash recovery | MEDIUM — rename fields, update Lua CAS | P1 |
| `checkout_workflow.py` workflow definition | HIGH — proves the abstraction works | LOW — wire existing transport.py functions | P1 |
| Recovery scanner generalization | HIGH — correctness under crashes | MEDIUM — update scan pattern, call engine | P1 |
| `grpc_server.py` refactor to use engine | HIGH — removes hardcoded logic | LOW — replace function calls with engine.execute | P1 |
| Named step execution logging | LOW — quality of life | LOW — add step.name to existing log lines | P2 |
| `WorkflowEngine.get_status()` | LOW — observability | LOW — read Redis hash, format dict | P2 |

**Priority key:**
- P1: Must have — defines the v3.0 milestone
- P2: Should have — add when P1 is stable and tested
- P3: Nice to have — not for this milestone

---

## Comparable System Analysis

| Aspect | Temporal | Cadence | Dapr Workflow | This Project (v3.0 Target) |
|--------|----------|---------|---------------|---------------------------|
| Workflow unit | Deterministic function | Deterministic function | Decorated async function | `WorkflowDefinition` dataclass |
| Activity unit | `@activity.defn` function | Activity method | `@activity_method` | `WorkflowStep.action` async callable |
| Compensation unit | Manual saga helper class | Manual try/catch + rollback | Manual try/catch | `WorkflowStep.compensation` async callable, auto-invoked by engine |
| State persistence | Temporal server event history | Cadence server event history | Dapr state store | Redis hash (existing Lua CAS pattern) |
| Recovery mechanism | Automatic event replay | Automatic event replay | Automatic checkpoint replay | Recovery scanner + per-step flags (existing pattern extended) |
| Execution strategies | SAGA only (compensating) | SAGA only (compensating) | SAGA only (compensating) | SAGA + 2PC via strategy enum (course differentiator) |
| Worker model | Separate worker processes | Separate worker processes | In-process | In-process callables via transport adapter |
| Scope | Full production platform | Full production platform | Framework + sidecar | Minimal in-process engine for course project |

The key simplification vs full Temporal/Cadence: no event history log, no deterministic replay, no separate worker pool, no versioning. All are correct omissions given the existing Redis hash WAL and course project scope.

---

## Sources

- [Temporal Workflow Engine Principles](https://temporal.io/blog/workflow-engine-principles) — atomicity requirements, task queue patterns, transactional consistency
- [Cadence Workflow Concepts](https://cadenceworkflow.io/docs/concepts/workflows) — fault-oblivious stateful workflow abstraction
- [Cadence Activity Concepts](https://cadenceworkflow.io/docs/concepts/activities) — activity registration via task lists, retry policies
- [Dapr Workflow Patterns](https://docs.dapr.io/developing-applications/building-blocks/workflow/workflow-patterns/) — compensation structure, chaining pattern, error handling
- [py-saga-orchestration](https://github.com/cdddg/py-saga-orchestration) — `OrchestrationBuilder.add_step(action, compensation)` pattern, fluent API
- [Architecture Weekly: Workflow Engine Design Proposal](https://www.architecture-weekly.com/p/workflow-engine-design-proposal-tell) — minimal vs nice-to-have features, event stream model
- [liteflow Python workflow engine](https://github.com/danielgerlag/liteflow) — `StepBody` abstract class pattern
- [Comparing Orchestration Frameworks: Cadence, Conductor, Temporal](https://medium.com/@natesh.somanna/comparing-orchestration-frameworks-ubers-cadence-netflix-conductor-and-temporal-3778cff24574) — abstractions comparison
- Existing codebase: `orchestrator/grpc_server.py`, `orchestrator/saga.py`, `orchestrator/tpc.py`, `orchestrator/recovery.py`, `orchestrator/transport.py`

---

*Feature research for: Abstract workflow engine orchestrator (v3.0 milestone)*
*Researched: 2026-03-26*
