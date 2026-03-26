# Project Research Summary

**Project:** DDS26-8 v3.0 — Abstract Workflow Engine & Refactoring
**Domain:** Distributed workflow engine abstraction over existing SAGA/2PC orchestrator
**Researched:** 2026-03-26
**Confidence:** HIGH

## Executive Summary

DDS26-8 v3.0 is a pure application-layer refactoring that introduces a Temporal/Cadence-inspired workflow engine abstraction into an already-working distributed checkout system. The existing orchestrator hardcodes SAGA and 2PC checkout logic in `grpc_server.py`; the v3.0 goal is to extract a generic engine that executes abstract step sequences (`WorkflowStep` dataclasses holding action and compensation callables) without any knowledge of Stock or Payment services. The checkout logic then re-registers itself as a `WorkflowDefinition`, and the engine handles execution, state persistence, retry, and compensation generically. The critical constraint is zero new dependencies — every engine primitive maps to Python 3.13 stdlib (`dataclasses`, `typing.Protocol`, `asyncio`) or already-installed packages (`msgspec`, `redis`).

The recommended approach is a strict bottom-up build order: WorkflowStore (replacing `saga.py`/`tpc.py` Lua CAS state machines) first, then the `WorkflowStep`/`WorkflowDefinition` data model, then strategy classes (`SagaStrategy`, `TwoPhaseStrategy`), then the `WorkflowEngine` integrator, then wiring into `grpc_server.py`/`recovery.py`/`consumers.py`, and finally deleting the superseded `saga.py` and `tpc.py`. This order respects the strict dependency flow — every layer depends on everything below it — and allows isolated testing at each step before the full integration is wired. The target is approximately 200 lines of new Python code for the engine and checkout definition combined; anything larger signals scope drift toward a full Temporal implementation that is explicitly out of scope.

The primary risk is breaking the existing 0-consistency-violation benchmark by introducing subtle behavioral changes during the abstraction: losing the Lua CAS atomicity guarantee, fragmenting the recovery scanner's coverage, or introducing a duplicate idempotency mechanism alongside the existing `HSETNX` guard. All three risks are highest during Phase 1 design decisions, not during implementation. The mitigations are explicit: use the existing `transition_state()` functions as the persistence API (never raw `HSET`), keep recovery scanner coverage as a first-class design constraint, and ensure the engine's "already running?" check delegates to the existing `create_saga_record()`/`create_tpc_record()` HSETNX mechanism.

## Key Findings

### Recommended Stack

See full analysis: `.planning/research/STACK.md`

The existing stack is complete. No new PyPI packages are required. The engine is built entirely from Python 3.13 stdlib primitives (`dataclasses` for `WorkflowStep`/`WorkflowDefinition`, `typing.Protocol` for the `StepFn` callable interface, `asyncio` for concurrent 2PC prepare phase) and the existing dependency set. Rejected additions include Temporal Python SDK (requires separate server), Prefect/Airflow/Celery (wrong domain), FSM libraries like `python-statemachine` (worse than the existing hand-rolled Lua CAS pattern), Pydantic (60ms import overhead, covered by `msgspec`), and Tenacity (retry logic already implemented in `retry_forever`/`retry_forward`).

**Core technologies:**
- Python 3.13 `dataclasses` + `typing.Protocol`: `WorkflowStep`, `WorkflowDefinition`, `StepFn` interface — zero external dependency; structural subtyping means existing `transport.py` functions satisfy `StepFn` without modification
- `redis[hiredis]` 5.0.3 + Lua CAS: engine state persistence via the same Redis hash + `TRANSITION_LUA` pattern already proven in `saga.py`/`tpc.py` — the script is identical in both files and extractable verbatim
- `msgspec` 0.18.6: serializes workflow context dict to Redis `context_json` field — 3-10x faster than stdlib `json`, already a dependency used throughout the codebase

### Expected Features

See full analysis: `.planning/research/FEATURES.md`

**Must have (table stakes — defines the v3.0 milestone):**
- `WorkflowStep` dataclass: `(name, action, compensation)` — the atomic unit; each step pairs a forward callable with its reverse callable
- `WorkflowDefinition` dataclass: `(name, steps, strategy)` — registration object; `strategy` selects SAGA vs 2PC execution path
- `WorkflowEngine.execute(workflow_id, definition, context)` — single entry point replacing `run_checkout()` and `run_2pc_checkout()`
- Durable execution state: `{workflow:<id>}` Redis hash with `step_N_done` per-step completion flags (reuses existing Lua CAS pattern)
- Reverse-order compensation on failure: engine drives compensation through registered callables, not through hardcoded step names
- Recovery scanner generalized: `recover_incomplete_workflows()` reads engine-managed keys and delegates to engine resume
- Checkout re-expressed as workflow definition: `make_checkout_workflow()` factory in `workflows/checkout.py`; engine knows nothing about Stock or Payment
- `grpc_server.py` refactored: `OrchestratorServiceServicer` receives `WorkflowEngine` instance, calls `engine.execute()` only

**Should have (differentiators, grade-relevant):**
- Same `WorkflowDefinition` executable under both SAGA and 2PC strategies — demonstrates genuine genericity
- Named steps with execution logging — step names in log lines, zero cost, high observability value for demo
- `WorkflowEngine` as injectable dependency — no global state, testable without Redis

**Defer (out of scope for course deadline):**
- Workflow versioning, signals, queries, child workflows
- Activity worker pools, dynamic step sequences, per-step timeouts
- Workflow visualization, full event sourcing

### Architecture Approach

See full analysis: `.planning/research/ARCHITECTURE.md`

The orchestrator's external shape is unchanged — same gRPC interface, same Docker/K8s deployment, same `COMM_MODE` toggle. Only the internal structure changes: five new files are added (`workflow_engine.py`, `workflow_store.py`, `strategy/saga_strategy.py`, `strategy/tpc_strategy.py`, `workflows/checkout.py`), four files are modified (`grpc_server.py`, `recovery.py`, `consumers.py`, `app.py`), and two files are deleted after validation (`saga.py`, `tpc.py`). All transport, client, event, and circuit-breaker modules are unchanged. The key architectural invariant is that `WorkflowEngine` is transport-agnostic (calls step callables), strategy classes are domain-agnostic (run any step list), and checkout-specific knowledge lives exclusively in `workflows/checkout.py`.

**Major components:**
1. `workflow_store.py` (NEW) — generic Redis hash + Lua CAS persistence, replaces `saga.py` and `tpc.py`; exposes `create()`, `transition()`, `get()`, `mark_step_done()`
2. `strategy/saga_strategy.py` + `strategy/tpc_strategy.py` (NEW) — execution protocols; SAGA runs steps sequentially with reverse compensation; 2PC runs prepare concurrently then commits/aborts all
3. `workflow_engine.py` (NEW) — `WorkflowEngine` class; wires store + strategy + event publishing; single `execute()` and `resume()` entry points
4. `workflows/checkout.py` (NEW) — `make_checkout_workflow()` factory; the only file with knowledge of Stock/Payment step names; produces `WorkflowDefinition` per request with closures over `transport.py` functions
5. `grpc_server.py` (MODIFIED) — becomes a thin servicer; calls `engine.execute(build_checkout_workflow(...), ctx, db)`

### Critical Pitfalls

See full analysis: `.planning/research/PITFALLS.md`

1. **Losing Lua CAS atomicity** — engine state persistence must call `transition_state()` / `transition_tpc_state()` for all state changes; never raw `HSET`. A simple `HSET state NEW_STATE` allows concurrent recovery and live requests to both own the same transition, corrupting state. Prevention: `workflow_store.py` owns all Redis writes through the CAS script; no code outside it writes to the `state` field.

2. **Recovery scanner blindness** — if the engine uses a new key prefix, the existing `recovery.py` scanner silently skips in-flight workflows. Decide before writing any engine code: either reuse existing key prefixes (`{saga:*}`, `{tpc:*}`) so the scanner works unchanged, or update the scanner in the same commit that introduces new prefixes. Never ship the engine without confirmed scanner coverage.

3. **Compensation detached from engine abstraction** — `run_compensation()` in `grpc_server.py` hardcodes two steps (`stock_reserved`, `payment_charged` flags). If the engine drives compensation through registered callables but the implementation still reads hardcoded flags, workflows with different step ordering compensate incorrectly. Prevention: each `WorkflowStep` must carry its `compensation` callable; the engine iterates registered compensations in reverse, never inspecting step names.

4. **Over-abstracting for the 6-day deadline (The Temporal Trap)** — the engine needs ~200 lines total. If it grows past 400 lines before checkout is registered, scope has drifted into full Temporal territory. Prevention: cap the engine API surface before writing code; no `ActivityTask`, `TaskQueue`, `WorkflowHistory`, or `WorkflowSignal` classes.

5. **Breaking exactly-once semantics** — if the engine adds its own idempotency key namespace (`{workflow:ORDER_ID}`) alongside the existing SAGA record, two concurrent `StartCheckout` calls with the same `order_id` can race on two separate keys. Prevention: the engine's "already running?" check must delegate to the existing HSETNX mechanism, not add a layer above it.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Engine Core — WorkflowStore + Data Model

**Rationale:** The Lua CAS state machine is the consistency foundation of the entire system. It must be extracted and generalized before anything else is built. The `WorkflowStep`/`WorkflowDefinition` data model must be defined before strategy classes or the engine can be written, because type interfaces drive everything downstream. Both have no inward dependencies and can be developed together.

**Delivers:** `workflow_store.py` (generic Redis CAS persistence), `engine/types.py` (`WorkflowStep`, `WorkflowDefinition`, `StepFn` Protocol). The extracted Lua CAS script module eliminates the existing duplication between `saga.py` and `tpc.py`.

**Addresses features:** Durable execution state, per-step completion flags, injectable `WorkflowEngine` dependency (type definition phase).

**Avoids pitfalls:** P1 (Lua CAS atomicity — designed in from the start), P4 (over-abstraction — data model is minimal), P11 (circular imports — shared module with no upward imports), P12 (key namespace collision — decided at this phase).

**Scope gate:** `workflow_store.py` < 120 lines, `types.py` < 80 lines. Unit tests: create record, transition states, verify Lua CAS rejects invalid transitions.

### Phase 2: Strategy Classes — SagaStrategy + TwoPhaseStrategy

**Rationale:** Strategies encapsulate the execution logic currently in `run_checkout()` and `run_2pc_checkout()`. They depend on the data model (Phase 1) and can be unit-tested in isolation against a mock store before the full engine is wired. Migrating this logic in an isolated phase — not interleaved with wiring into `grpc_server.py` — allows regression verification step by step.

**Delivers:** `strategy/saga_strategy.py` (sequential forward execution + reverse compensation), `strategy/tpc_strategy.py` (concurrent prepare + WAL commit/abort). The `retry_forever()`/`retry_forward()` utilities move to a shared `engine/retry.py` module here to avoid circular imports.

**Addresses features:** Internal SAGA executor, internal 2PC executor, reverse-order compensation, `CircuitBreakerError` propagation.

**Avoids pitfalls:** P3 (compensation detached — compensation callables are registered per step, strategies call them in reverse), P6 (CircuitBreakerError swallowed — reuse existing `retry_forward()` which already propagates the error correctly).

**Scope gate:** Each strategy < 100 lines. Unit tests: SAGA compensation called in reverse order on step failure; 2PC WAL write (COMMITTING state) happens before phase-2 messages are sent.

### Phase 3: WorkflowEngine + Checkout Definition

**Rationale:** The engine is the integrator — it wires store + strategy + event publishing into a single `execute()` entry point. Checkout is re-expressed as a `WorkflowDefinition` factory simultaneously, because the engine API cannot be validated without a concrete caller. These two pieces belong in the same phase to catch API mismatches before wiring into `grpc_server.py`.

**Delivers:** `workflow_engine.py` (`WorkflowEngine` class with `execute()` and `resume()`), `workflows/checkout.py` (`make_checkout_workflow()` factory with step closures over `transport.py` functions). The abstraction is complete and verifiable end-to-end in this phase.

**Addresses features:** `WorkflowEngine.execute()` routing dispatch, checkout re-expressed as workflow definition, same `WorkflowDefinition` running under both SAGA and 2PC.

**Avoids pitfalls:** P5 (exactly-once — engine delegates idempotency to existing HSETNX mechanism in `workflow_store.create()`), P13 (idempotency key mismatch — step closures in `checkout.py` construct keys explicitly in the expected format; engine does not auto-generate keys from step names).

**Scope gate:** Engine < 150 lines, checkout definition < 80 lines. Integration test: full happy-path checkout through engine. Integration test: stock failure triggers compensation. Integration test: payment failure after stock reserved triggers partial compensation.

### Phase 4: Wiring — grpc_server + recovery + consumers

**Rationale:** Swapping out the hardcoded orchestration path in `grpc_server.py` and updating `recovery.py`/`consumers.py` is a distinct integration phase. Keeping it separate from Phase 3 ensures the engine is validated in isolation before being exposed to the full test suite, the benchmark, and the kill-test.

**Delivers:** Modified `grpc_server.py` (calls `engine.execute()` only), modified `recovery.py` (`recover_incomplete_workflows()` replaces dual scanners), modified `consumers.py` (calls `engine.compensate()` instead of importing `run_compensation`), updated `app.py` (engine instantiation and checkout workflow registration at startup).

**Addresses features:** `grpc_server.py` refactored, recovery scanner generalized, all 37 integration tests passing.

**Avoids pitfalls:** P2 (recovery scanner coverage — scanner updated in this phase, kill-tested before proceeding), P7 (transport toggle — `checkout.py` imports only from `transport.py`, verified in queue-mode integration test), P9 (recovery uses old step sequence — `recovery.py` updated atomically with checkout rewrite), P10 (integration tests broken — full suite runs after every commit in this phase).

**Scope gate:** All 37 tests pass. Benchmark shows 0 consistency violations. Kill-test passes in both `COMM_MODE=grpc` and `COMM_MODE=queue`.

### Phase 5: Cleanup — Delete saga.py + tpc.py + Refactoring

**Rationale:** Deleting the superseded modules only after Phase 4 is fully validated eliminates regression risk. Refactoring (variable renames, log message cleanup) is deliberately separated from feature addition to prevent behavioral changes from being introduced alongside structural changes.

**Delivers:** Deletion of `orchestrator/saga.py` and `orchestrator/tpc.py`. Named step logging (P2 priority). `WorkflowEngine.get_status()` if time permits (P2 priority, stretch).

**Addresses features:** Named step execution logging, optional `get_status()` observability endpoint.

**Avoids pitfalls:** P8 (readability — evaluate checkout definition for readability before marking done), P14 (refactoring changes behavior — refactoring is a dedicated phase, not interleaved; benchmark runs immediately after any rename).

**Scope gate:** `grep -r "from saga import\|from tpc import" orchestrator/` returns nothing. Full suite passes. Benchmark 0 violations confirmed.

### Phase Ordering Rationale

- **State persistence before logic:** `workflow_store.py` must exist before strategy classes or the engine can be written — it is the lowest-level dependency in the graph.
- **Data model before implementation:** `WorkflowStep`/`WorkflowDefinition` types are used by strategies, engine, and checkout definition simultaneously — defining them first prevents downstream type mismatches.
- **Strategies before engine:** strategies can be unit-tested in isolation; the engine's integration value comes from wiring them together, not from independent behavior.
- **Engine before wiring:** validate the engine end-to-end before exposing it to `grpc_server.py` and the full integration test suite.
- **Wiring before deletion:** superseded modules (`saga.py`, `tpc.py`) stay alive during the integration phase as a regression safety net.
- **Deletion and refactoring last:** separating cleanup from feature work prevents subtle behavioral regressions during the most fragile phase.

```
Phase 1: WorkflowStore + Data Model (types.py)
    |
    v
Phase 2: SagaStrategy + TwoPhaseStrategy
    |
    v
Phase 3: WorkflowEngine + checkout.py (workflow definition)
    |
    v
Phase 4: Wire grpc_server + recovery + consumers
    |
    v
Phase 5: Delete saga.py + tpc.py + Refactoring
```

### Research Flags

Phases with well-documented patterns from codebase analysis (skip `research-phase`):
- **Phase 1:** Lua CAS extraction is mechanical — the script is identical in `saga.py` and `tpc.py`, extractable verbatim. `WorkflowStep`/`WorkflowDefinition` are pure data.
- **Phase 2:** Strategy logic is directly derived from existing `run_checkout()`/`run_2pc_checkout()` — this is migration, not new design.
- **Phase 3:** Engine integrator pattern is well-defined by the data model. Checkout factory is a direct translation of existing step logic.
- **Phase 5:** Mechanical cleanup — deletion + grep verification.

Phases that may benefit from targeted research during planning:
- **Phase 4 (recovery integration):** The interaction between `engine.resume()` and the existing recovery scanner is the highest-risk integration surface. The decision between "reuse existing key prefixes" and "extend scanner to new prefixes" must be locked before Phase 1 begins (it determines `workflow_store.py` key design). A focused read of `recovery.py` scan patterns and TTL behavior is warranted before Phase 4 planning starts if that decision was deferred.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Full codebase analysis — every engine primitive maps to existing stdlib or installed dependencies; confirmed no new packages needed |
| Features | HIGH | Derived from Temporal/Cadence architecture docs + direct codebase analysis; MVP scope is clearly bounded by the ~200 LOC target |
| Architecture | HIGH | Based entirely on direct codebase analysis of v2.0 implementation; build order dependency graph is explicit and testable at each step |
| Pitfalls | HIGH | Grounded in existing code — Lua CAS pattern, recovery scanner, exactly-once mechanism all directly inspected; pitfalls map to real code paths |

**Overall confidence:** HIGH

### Gaps to Address

- **Recovery key-prefix decision:** The choice between reusing `{saga:*}`/`{tpc:*}` prefixes vs introducing a unified `{workflow:*}` prefix must be made before Phase 1 begins, not discovered during Phase 4 wiring. Both ARCHITECTURE.md and PITFALLS.md flag this as a decision point. The safer choice for a 6-day deadline is reusing existing prefixes (recovery scanner unchanged). Validate by checking if any `{workflow:*}` keys already exist in Redis before choosing a new prefix.

- **Lambda closure correctness:** STACK.md rates lambda closure semantics for step action/compensation as MEDIUM confidence — the late-binding bug (`for item in items: lambda ctx: use_item` without `i=item` default arg binding) is a known Python footgun. Requires an explicit code review gate during Phase 3 when `checkout.py` step closures are written.

- **`consumers.py` compensation path:** ARCHITECTURE.md identifies `consumers.py` as needing modification (compensation consumer calls `engine.compensate()` instead of `run_compensation`), but PITFALLS.md does not have a dedicated pitfall entry for this path. The compensation consumer is exercised by kill-tests, not unit tests — Phase 4 kill-test coverage must explicitly validate this integration.

## Sources

### Primary (HIGH confidence)

**Codebase (direct analysis):**
- `orchestrator/saga.py` — Lua CAS state machine, exact `TRANSITION_LUA` script, `VALID_TRANSITIONS`, `create_saga_record()`
- `orchestrator/tpc.py` — Lua CAS state machine, 2PC state names, `create_tpc_record()`
- `orchestrator/grpc_server.py` — `run_checkout()`, `run_2pc_checkout()`, `run_compensation()`, `retry_forward()`, `retry_forever()`
- `orchestrator/recovery.py` — `recover_incomplete_sagas()`, `recover_incomplete_tpc()`, scan patterns, `NON_TERMINAL_STATES`
- `orchestrator/transport.py` — transport abstraction layer, `COMM_MODE` toggle pattern
- `orchestrator/consumers.py` — compensation consumer loop, `_handle_compensation_message()`
- `orchestrator/app.py` — startup sequence, Redis init, background task registration
- All `requirements.txt` files — confirmed no new dependencies needed

**External (architecture patterns):**
- [Temporal Workflow Engine Design Principles](https://temporal.io/blog/workflow-engine-principles) — workflow-as-data, activity separation, state persistence model
- [Python Protocol structural subtyping (PEP 544)](https://peps.python.org/pep-0544/) — `StepFn` Protocol for async `__call__`
- [Python dataclasses stdlib docs](https://docs.python.org/3/library/dataclasses.html) — `WorkflowStep`/`WorkflowDefinition` design

### Secondary (MEDIUM confidence)

- [Cadence Workflow Concepts](https://cadenceworkflow.io/docs/concepts/workflows) — fault-oblivious stateful workflow abstraction, activity registration
- [Dapr Workflow Patterns](https://docs.dapr.io/developing-applications/building-blocks/workflow/workflow-patterns/) — compensation structure, chaining pattern
- [py-saga-orchestration](https://github.com/cdddg/py-saga-orchestration) — `OrchestrationBuilder.add_step(action, compensation)` pattern
- [Architecture Weekly: Workflow Engine Design Proposal](https://www.architecture-weekly.com/p/workflow-engine-design-proposal-tell) — minimal vs over-engineered engine features
- [Temporal Saga compensation pattern](https://temporal.io/blog/compensating-actions-part-of-a-complete-breakfast-with-sagas) — workflow step + compensation pairing
- [Microservices.io SAGA Pattern](https://microservices.io/patterns/data/saga.html) — compensation and consistency guarantees

### Tertiary (background context only)

- v2.0 PITFALLS.md (2026-03-12) — existing consistency guarantees to preserve (prior milestone research)
- [Comparing Orchestration Frameworks: Cadence, Conductor, Temporal](https://medium.com/@natesh.somanna/comparing-orchestration-frameworks-ubers-cadence-netflix-conductor-and-temporal-3778cff24574) — used to bound scope correctly (not to copy features)

---
*Research completed: 2026-03-26*
*Ready for roadmap: yes*
