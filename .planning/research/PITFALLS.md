# Pitfalls: Adding Abstract Workflow Engine to Existing SAGA/2PC Orchestrator

**Domain:** Distributed workflow engine abstraction over existing concrete orchestrator
**Researched:** 2026-03-26
**Confidence:** HIGH (based on direct codebase analysis + HIGH confidence domain principles)
**Milestone scope:** v3.0 — Generic workflow engine + checkout rewritten as workflow definition

---

## Context

The existing system has working SAGA and 2PC implementations with 0 consistency violations under benchmark. The v3.0 goal is to introduce a generic workflow engine abstraction (Temporal/Cadence-inspired) so checkout is defined as a workflow registration rather than hardcoded orchestrator logic. Deadline is 6 days. The primary risk is breaking what works.

---

## Critical Pitfalls

### P1 -- Losing Lua CAS Atomicity Behind the Abstraction Layer

**What goes wrong:**
The existing `saga.py` and `tpc.py` use Lua CAS scripts (`TRANSITION_LUA`) for atomic state transitions in Redis. Every state change is: read current state, compare against expected, set new state — atomically within a single `EVAL`. If the workflow engine's "step completed" callback replaces this with a simple `HSET state NEW_STATE`, you lose the compare-and-swap guarantee. Two concurrent recovery attempts (e.g., startup scanner + live request) can both think they own the transition and both proceed, corrupting state.

**Why it happens:**
The abstraction hides the transport. The engine says "mark step X done" and the implementation does `HSET` without understanding that the Lua CAS is what prevents TOCTOU races. The developer implementing the engine callbacks copies the simpler `HSET` pattern and misses that `transition_state()` exists for a reason.

**How to avoid:**
The workflow engine's state persistence layer MUST call `transition_state()` (or `transition_tpc_state()`) for all state changes — never raw `HSET`. If the engine introduces its own state record format, it must include an equivalent Lua CAS. Do not add a new `workflow_state` field that bypasses the existing CAS mechanism. The safest approach: the engine's execution layer calls the existing `transition_state()` / `transition_tpc_state()` functions directly, treating them as the storage API.

**Warning signs:**
- Engine `StepResult` or `WorkflowContext` that writes state without calling `transition_state()`
- A new `workflow.py` module that reimplements state persistence independently of `saga.py`/`tpc.py`
- Tests that pass but benchmark kill-tests show consistency violations

**Phase to address:**
Phase 1 (engine design). This is the most dangerous structural pitfall. The Lua CAS is the consistency foundation — the engine must be designed around it, not over it.

---

### P2 -- Recovery Scanner Blindness to Abstract Workflow Records

**What goes wrong:**
`recovery.py` scans `{saga:*}` and `{tpc:*}` keys and applies protocol-specific recovery logic. If the workflow engine introduces a new key format (e.g., `{workflow:ORDER_ID}`) or changes the `state` field semantics, the recovery scanner will silently skip in-flight workflows during orchestrator restart. Transactions are stuck forever in a non-terminal state. Container-kill tests fail.

**Why it happens:**
The engine is designed as a new abstraction but the recovery scanner is not updated. The developer considers the engine "separate" from the recovery path and defers the integration. Under normal operation everything works — the scanner is only exercised during crash recovery.

**How to avoid:**
Choose one of two approaches and commit before writing any engine code:
1. **Reuse existing record format**: the engine stores its state in `{saga:ORDER_ID}` or `{tpc:ORDER_ID}` hashes using the exact same field names (`state`, `updated_at`, etc.) and the same state machine strings. The recovery scanner works unchanged. This is the safe, fast option for a 6-day deadline.
2. **Extend the scanner**: if the engine needs a new record format, update `recover_incomplete_sagas()` and `recover_incomplete_tpc()` in the same PR that adds the engine. Never ship the engine without confirmed scanner coverage.

**Warning signs:**
- Engine creates keys with a new prefix (`workflow:`, `engine:`, etc.) without updating the scan pattern in `recovery.py`
- Recovery scanner tested only in isolation, not against engine-generated records
- `NON_TERMINAL_STATES` set in `recovery.py` not extended when engine adds intermediate states

**Phase to address:**
Phase 1 (engine design). Must be an explicit decision at design time, not retrofitted.

---

### P3 -- Compensation Logic Detached From the Engine Abstraction

**What goes wrong:**
`run_compensation()` in `grpc_server.py` is tightly coupled to the SAGA model: it reads `payment_charged`, `stock_reserved`, `refund_done`, `stock_restored` flags from the Redis hash and executes compensation in reverse order. If the workflow engine abstracts steps as a generic list but the compensation implementation still reads these hardcoded flags, a workflow with different step ordering or different step names will compensate incorrectly — or skip compensation entirely.

Concrete failure: the engine executes steps in order `[reserve_stock, charge_payment]`. If `charge_payment` fails, the engine calls "compensate". The compensation function reads `payment_charged == "0"` and skips the refund. But the Lua CAS may not have set `payment_charged = "1"` yet if the engine wraps state transitions differently. Result: stock is not released.

**Why it happens:**
`run_compensation()` was written with concrete knowledge of exactly two steps. The workflow engine abstraction makes step order/names configurable, but compensation still assumes the original field names. The integration point between the abstract engine and the concrete compensation is never defined.

**How to avoid:**
Define compensation as part of the workflow step definition, not as a separate hardcoded function. Each step registration includes an `action` and a `compensation` callable. The engine executes compensations in reverse step order using only the registered callables. The hardcoded `run_compensation()` becomes the checkout-specific implementation of this contract, not a generic compensation runner. Preserve the existing per-step completion flags (`payment_charged`, `stock_reserved`) as the mechanism for idempotency-safe compensation — just route through the abstraction.

**Warning signs:**
- Engine has a `compensate()` method that calls `run_compensation()` directly with no additional indirection
- Step registration does not include a `compensation` callable
- `reserved_items_json` partial-reservation tracking is lost in the abstraction
- Kill-test shows stock not released when payment fails mid-checkout

**Phase to address:**
Phase 1 (engine API design). The compensation contract must be explicit in the engine interface.

---

### P4 -- Over-Abstracting for a 6-Day Deadline (The Temporal Trap)

**What goes wrong:**
The developer designs a full workflow engine inspired by Temporal/Cadence: activities, workflow contexts, task queues, event sourcing, history replay, signals, and child workflows. This takes 4 of 6 days. There is no time left to rewrite checkout as a workflow definition, run the benchmark, or debug edge cases. The submission is a half-implemented engine with the existing hardcoded checkout still doing the actual work under the hood.

**Why it happens:**
Temporal/Cadence are genuinely impressive systems. Reading their documentation makes you want to implement all of it. The course requirement says "Temporal/Cadence-inspired" — which can mean anything from a 50-line step executor to a full durable execution engine. Without explicit scope constraints, scope creeps to the complex end.

**How to avoid:**
The engine only needs to do what the existing checkout does — nothing more. The minimum viable engine for this codebase:
- A `Workflow` dataclass: `steps: list[WorkflowStep]`, `execution_mode: Literal["saga", "2pc"]`
- A `WorkflowStep` dataclass: `name: str`, `action: Callable`, `compensation: Callable`
- A `WorkflowEngine.execute(workflow, context)` method that runs steps, handles compensation on failure, and delegates all state persistence to the existing `transition_state()` / `transition_tpc_state()` functions
- A `checkout_workflow` registration that maps the existing `run_checkout()` steps into `WorkflowStep` instances

This is ~150 lines of new Python. The checkout rewrite is ~50 lines. Total new code for the engine feature: ~200 lines. If it is growing past 400 lines, scope has drifted.

**Warning signs:**
- Engine module has classes for `WorkflowContext`, `ActivityTask`, `TaskQueue`, `WorkflowHistory`, `WorkflowSignal`
- Engine implements history replay or event sourcing
- Engine has its own retry logic independent of `retry_forever()` and `retry_forward()`
- `workflow.py` is more than 300 lines before checkout is even registered

**Phase to address:**
Phase 1 (scope definition). Explicitly cap the engine API surface before writing code.

---

### P5 -- Breaking Exactly-Once Semantics During the Checkout Rewrite

**What goes wrong:**
The current `run_checkout()` has a carefully designed exactly-once check at the top: read the existing SAGA record, return early if `COMPLETED`, clean up and retry if `FAILED`, return "in progress" for any other state. If the workflow engine wraps this function and adds its own "was this workflow already run?" check that uses a different key or logic, you get two independent idempotency mechanisms that can disagree. The engine says "no record, start fresh" while the SAGA record says "STARTED" — and a new execution begins alongside the existing one.

**Why it happens:**
The engine is designed generically. It might add `{workflow:ORDER_ID}:status` as its own record without knowing the SAGA record already serves this purpose. Two callers of `StartCheckout` with the same `order_id` now race on two separate keys.

**How to avoid:**
The workflow engine must not introduce a separate idempotency key namespace. The existing SAGA/TPC record is the idempotency record. The engine's "already running?" check must delegate to `get_saga()` / `get_tpc()` — or more precisely, the existing `create_saga_record()` / `create_tpc_record()` HSETNX mechanism is the exactly-once guard, and the engine must not bypass or duplicate it. The engine's execute method should start with the existing record-creation and exactly-once logic, not add a layer above it.

**Warning signs:**
- Engine creates `{workflow:*}` keys as its own idempotency guard
- `run_checkout()` is called from inside the engine after the engine's own idempotency check
- Concurrent requests with the same `order_id` result in two SAGA records being created
- Benchmark shows double-charges or double-stock-decrements

**Phase to address:**
Phase 1 (engine design) and Phase 2 (checkout rewrite). Verify with existing idempotency tests.

---

### P6 -- Silently Dropping Circuit Breaker Errors in Engine Step Execution

**What goes wrong:**
`run_checkout()` propagates `CircuitBreakerError` explicitly: it catches the error at the outer `try/except`, sets `COMPENSATING` state, and runs compensation. If the workflow engine wraps step execution in a generic `try/except Exception` that converts all exceptions to step-failure results, `CircuitBreakerError` is silently downgraded to a regular step failure. The engine retries the step (via `retry_forward`) even though the circuit is open. This causes unnecessary retry attempts against a failing service and delays compensation.

**Why it happens:**
Generic step executors catch broad exceptions. The developer does not realize that `CircuitBreakerError` is semantically different from a recoverable RPC failure — it means "this service is down, do not retry, compensate immediately."

**How to avoid:**
The engine's step executor must propagate `CircuitBreakerError` immediately, identical to how `retry_forward()` does: `except CircuitBreakerError: raise`. The engine's compensation trigger must distinguish between "step returned failure result" (retry is OK) and "CircuitBreakerError raised" (compensate immediately, no retry). The existing `retry_forward()` function already encodes this logic — the engine should call it rather than reimplement its retry/exception handling.

**Warning signs:**
- Engine step executor has a bare `except Exception as e: return StepResult(success=False, error=str(e))`
- `CircuitBreakerError` is not imported in the engine module
- Under kill-tests, the compensation path is not triggered when the circuit is open

**Phase to address:**
Phase 1 (engine step executor design).

---

## High Severity Pitfalls

### P7 -- Refactoring Breaks gRPC/Queue Transport Toggle

**What goes wrong:**
The `transport.py` adapter provides a clean interface that `grpc_server.py` uses regardless of `COMM_MODE`. The checkout rewrite inside the workflow engine must continue to import from `transport` (not directly from `client` or `queue_client`). If the engine module imports `from client import reserve_stock` directly — because the developer is testing with gRPC and forgets the abstraction — the queue transport path silently breaks. The `COMM_MODE=queue` benchmark configuration starts failing.

**How to avoid:**
The engine and checkout workflow definition must only import transport functions from `transport.py`. This is a one-line rule: `from transport import reserve_stock, charge_payment, ...` — never from `client` or `queue_client` directly. Enforce this with a comment in `transport.py` or a module-level assertion.

**Warning signs:**
- `from client import ...` in `workflow.py` or `engine.py`
- Tests only run with default `COMM_MODE=grpc`
- Queue integration tests fail after checkout rewrite

**Phase to address:**
Phase 2 (checkout rewrite). Simple rule, easy to miss.

---

### P8 -- The "Interview-Ready" Refactoring That Makes the System Harder to Read

**What goes wrong:**
Refactoring for "clarity and maintainability" introduces layers of abstraction that make the system harder to understand without knowing the engine abstraction. An interviewer reading `grpc_server.py` can currently trace the entire checkout flow in one file: create record, reserve stock, charge payment, compensate, done. After the refactoring, the flow is: `engine.execute(checkout_workflow, ctx)` — and you need to read `engine.py`, `workflow.py`, `checkout.py`, and `WorkflowStep` to understand what happens. For a course evaluation, this can look over-engineered rather than thoughtful.

**How to avoid:**
The abstraction should make the checkout definition MORE readable, not less. A good checkout workflow definition reads like pseudocode: steps defined inline, compensations co-located with actions, the engine calling convention obvious. Keep the engine API surface minimal so it requires no preamble to understand. The goal is that `checkout_workflow` can be read and understood by someone who has never seen the engine. Add inline comments explaining the SAGA/2PC execution model at the registration site, not in the engine internals.

**Warning signs:**
- `checkout_workflow` is defined across multiple files with no single readable definition
- Step names are generic (`step_1`, `step_2`) rather than descriptive (`reserve_stock`, `charge_payment`)
- The engine has more abstraction than the checkout has steps

**Phase to address:**
Phase 2 (checkout rewrite) and Phase 3 (refactoring). Evaluate readability explicitly before marking phase complete.

---

### P9 -- Startup Recovery Does Not Use the Engine to Resume Workflows

**What goes wrong:**
The recovery scanner in `recovery.py` currently calls `run_checkout()` steps directly to resume partial SAGAs. After the refactoring, if checkout steps are defined in the workflow engine, the recovery scanner still calls the old `resume_saga()` function which hardcodes the step sequence. The engine and the recovery path diverge. A refactoring that changes step order, adds a step, or changes step names will fix the engine path but silently leave the recovery path using the old sequence. The next kill-test uses the recovery path and fails.

**How to avoid:**
The recovery scanner must call the engine's execution path, not `resume_saga()` directly. Specifically: `resume_saga()` should be refactored to call `engine.execute(checkout_workflow, ctx, resume_from=current_state)` with a resume-from-state capability. If the engine does not support resume-from-state (reasonable for a 6-day scope), then the simplest safe approach is to keep `resume_saga()` as-is and ensure it is updated atomically with any changes to the checkout workflow steps. Document this coupling explicitly with a comment.

**Warning signs:**
- `recovery.py` still references step names/functions that no longer exist in the engine-based checkout path
- Recovery is tested with a hardcoded state sequence but checkout is tested through the engine
- A new step is added to the checkout workflow but not added to the resume logic

**Phase to address:**
Phase 2 (checkout rewrite). The recovery path integration must be explicitly addressed, not assumed.

---

### P10 -- Breaking the 37 Integration Tests by Changing Behavior, Not Just Structure

**What goes wrong:**
The refactoring is structural — the checkout behavior must not change. But if the engine introduces a different retry policy, different error message format, different state transition timing, or different flag-setting sequence, existing tests can fail. More subtly: tests that mock `run_checkout` directly will need to mock `engine.execute` instead, which may have a different signature or call chain.

**How to avoid:**
Run the full 37-test suite after every meaningful change during the refactoring, not just at the end. The test suite is the regression guard. If a test fails, treat it as a bug in the refactoring (behavior changed) not a problem with the test. The only acceptable test changes are signature updates for mocking the new engine interface — never relaxing assertions.

**Warning signs:**
- Any test failures during the refactoring treated as "I'll fix this later"
- Mock patches changed from `grpc_server.run_checkout` to `engine.execute` with weakened assertions
- Tests skipped or commented out during development

**Phase to address:**
All phases. Continuous, not just at the end.

---

## Moderate Pitfalls

### P11 -- New `workflow.py` Module With Circular Imports

**What goes wrong:**
The orchestrator already has circular import risks: `grpc_server.py` imports from `recovery.py` (via `run_compensation`), and `recovery.py` imports from `grpc_server.py`. A new `engine.py` or `workflow.py` that imports from `grpc_server.py` (to reuse `retry_forward`, `run_compensation`) while `grpc_server.py` imports from `engine.py` creates a circular import that fails at startup.

**How to avoid:**
Move shared utilities (`retry_forward`, `retry_forever`, `run_compensation`) to a dedicated module (e.g., `coordinator.py` or `retry.py`) that has no upward imports. Both the engine and `grpc_server.py` import from this shared module. The existing circular import between `grpc_server.py` and `recovery.py` (resolved via deferred local imports) is a warning that the import graph needs care.

**Warning signs:**
- `ImportError: cannot import name X from partially initialized module`
- Deferred imports (`from grpc_server import Y` inside a function body) to work around circular imports in the new modules
- More than two levels of import indirection to call a utility function

**Phase to address:**
Phase 1 (module structure design).

---

### P12 -- Engine State Storage Namespace Collision With Existing Keys

**What goes wrong:**
If the engine introduces its own Redis keys (e.g., `workflow:ORDER_ID`, `engine:step:ORDER_ID:N`) without careful hash tag design, these keys land on unpredictable Redis Cluster slots. More critically, if the engine uses a key like `{saga:ORDER_ID}:workflow` alongside the existing `{saga:ORDER_ID}` hash, and the recovery scanner scans `{saga:*}`, it will hit the new key and try to decode it as a SAGA hash, causing decode errors or silent skips.

**How to avoid:**
The safest approach is to store all engine-managed state inside the existing `{saga:ORDER_ID}` or `{tpc:ORDER_ID}` hash (adding new fields, not new keys). If new keys are needed, use `{saga:ORDER_ID}:engine:*` with the same hash tag so they live on the same slot, and update the recovery scanner's scan pattern to explicitly skip these keys.

**Warning signs:**
- `hgetall` in recovery scanner silently failing on engine-format keys
- `CROSSSLOT` errors in Redis logs
- New key format without a hash tag

**Phase to address:**
Phase 1 (engine design).

---

### P13 -- Inconsistent Step Names Between Engine Registration and Redis Idempotency Keys

**What goes wrong:**
The existing idempotency key format is `{saga:ORDER_ID}:step:reserve:ITEM_ID`. This format is hardcoded in `grpc_server.py`, `recovery.py`, and `run_checkout()`. If the workflow engine uses its own step naming convention (e.g., `step_0`, `step_1` or `reserve-stock`), and the engine auto-generates idempotency keys from step names, the generated keys will not match the format the stock/payment services expect. The Lua idempotency check in participants looks up a specific key format — a mismatch means idempotency is not enforced.

**How to avoid:**
The checkout workflow step definitions must specify the exact idempotency key format, not let the engine generate it from step names. Either pass the idempotency key as part of the step context, or have the engine call the action callable with the idempotency key as a parameter constructed outside the engine. The engine should not own idempotency key construction.

**Warning signs:**
- Engine constructs idempotency keys from step index or step name
- Participant-side idempotency check never matches any engine-generated key
- Duplicate stock decrements under retry (idempotency not working)

**Phase to address:**
Phase 2 (checkout workflow registration).

---

### P14 -- Codebase Refactoring Changing Observable Behavior Under Benchmark

**What goes wrong:**
The "refactoring for clarity" part of v3.0 includes renaming functions, reorganizing modules, and cleaning up inconsistencies. Under a 6-day deadline, refactoring + feature addition simultaneously is risky. A variable rename that accidentally changes a Redis key format (e.g., changing `saga_key = f"{{saga:{order_id}}}"` to use a helper function that formats differently) breaks every Redis operation silently. The benchmark runs fine in dev (fresh Redis) but consistency violations appear because old in-flight records use the old key format.

**How to avoid:**
Refactoring and feature addition should be in separate commits/phases, not interleaved. Refactor first, verify with full test suite, commit. Then add the engine. If under time pressure, defer non-critical refactoring entirely — the engine abstraction IS the refactoring for interview purposes.

**Warning signs:**
- Commits that mix engine additions and variable renames
- Redis key string constants changed during "cleanup" without a grep for all usages
- Test suite passes but benchmark shows new consistency violations

**Phase to address:**
Phase 3 (refactoring). Keep it separate from Phase 1-2 (engine).

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Engine only supports SAGA (not 2PC) | 3 days faster, simpler engine | 2PC path bypasses engine, inconsistent architecture | Only if 2PC checkout is re-registered as a workflow before demo |
| Keep `run_checkout()` and add engine as a thin wrapper | Minimal refactoring risk | Engine is cosmetic, not structural — hard to explain in interview | Acceptable if deadline is the binding constraint |
| Engine does not support resume-from-state | Avoids complex engine state tracking | Recovery scanner cannot use engine path; must maintain dual paths | Acceptable if documented and recovery scanner stays in sync manually |
| Skip refactoring, only add engine | Saves 1-2 days | Codebase remains inconsistent, lower code quality score | Acceptable given 6-day deadline — engine feature is higher value |
| Hardcode checkout step list in engine rather than registration API | 30 min vs 2 hours for full registration | Not reusable, but reusability is irrelevant for a course project | Acceptable — YAGNI applies strongly here |

---

## Integration Gotchas

| Integration Point | Common Mistake | Correct Approach |
|-------------------|----------------|------------------|
| `transition_state()` via engine | Engine calls `HSET state X` directly | Always call `transition_state()` or `transition_tpc_state()` — Lua CAS is mandatory |
| `transport.py` via checkout workflow | Direct `from client import reserve_stock` | Always `from transport import reserve_stock` — preserves COMM_MODE toggle |
| `recovery.py` after checkout rewrite | Scanner calls old `resume_saga()` with hardcoded steps | Either update `resume_saga()` to call engine, or explicitly sync it with engine steps |
| `run_compensation()` from engine | Engine calls `run_compensation()` as a black box | Compensation must read the same per-step flags (`payment_charged`, `stock_reserved`) the existing code uses |
| `retry_forward()` / `retry_forever()` from engine | Engine reimplements retry with different `CircuitBreakerError` handling | Reuse existing retry functions — their `CircuitBreakerError` propagation is load-bearing |
| Idempotency key generation | Engine generates keys from step names | Keys must be constructed explicitly in the format participants expect |

---

## "Looks Done But Isn't" Checklist

- [ ] **Engine state persistence:** Does every state transition go through `transition_state()` / `transition_tpc_state()` with Lua CAS? Verify by reading `engine.py` for any `HSET` calls on state fields.
- [ ] **Recovery scanner coverage:** Does `recover_incomplete_sagas()` and `recover_incomplete_tpc()` still find and resume engine-managed workflows? Test by running kill-test with COMM_MODE=grpc and COMM_MODE=queue.
- [ ] **Compensation via engine:** Does the engine compensation path correctly call `run_compensation()` with the same partial-reservation data (`reserved_items_json`) as before? Kill-test after step-1 completion.
- [ ] **Exactly-once via HSETNX:** Does the engine path still use `create_saga_record()` / `create_tpc_record()` with HSETNX? Send two concurrent checkout requests with the same `order_id` — only one should succeed.
- [ ] **Transport abstraction intact:** Does `COMM_MODE=queue` still work after checkout rewrite? Run one test in each mode.
- [ ] **37 tests passing:** Run `pytest tests/` with zero failures after every major commit.
- [ ] **Benchmark 0 violations:** Run the benchmark after the engine is wired end-to-end before any refactoring.
- [ ] **Kill-test passes:** Kill the orchestrator mid-checkout after engine rewrite and verify recovery lands in COMPLETED or FAILED, never stuck.
- [ ] **Circuit breaker propagation:** Verify `CircuitBreakerError` from a step triggers immediate compensation, not retry. Mock a tripped breaker and check the code path.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| P1: Lua CAS bypassed | HIGH | Identify all `HSET state` calls in engine, replace with `transition_state()` calls, re-run kill-tests. May require Redis data cleanup if corruption occurred. |
| P2: Recovery scanner blind to engine records | MEDIUM | Add engine key pattern to scan in `recovery.py`, run kill-test to verify. Low risk if addressed before benchmark. |
| P3: Compensation detached from engine | HIGH | Redefine `WorkflowStep` to include `compensation` callables, refactor `run_compensation()` to use them. Re-run all tests + kill-test. |
| P4: Over-engineered engine (time lost) | HIGH | Scope-cut to minimal engine wrapper: delete non-essential engine features, reduce to step list + executor. Accept technical debt on unused abstractions. |
| P5: Exactly-once broken | HIGH | Identify conflicting idempotency mechanism, consolidate to HSETNX guard, run concurrent-request test. May need Redis key cleanup. |
| P7: Transport toggle broken | LOW | Find direct client imports in engine/workflow files, replace with transport imports, run both COMM_MODE tests. |
| P10: Integration tests broken | MEDIUM | Treat each failure as a behavior regression, not a test problem. Revert the change that broke it, re-implement more carefully. |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| P1: Lua CAS atomicity lost | Phase 1 (engine design) | Code review: no raw `HSET` on state field in engine code |
| P2: Recovery scanner blindness | Phase 1 (engine design) | Kill-test: orchestrator restart with in-flight engine-managed workflow resolves to terminal state |
| P3: Compensation detached | Phase 1 (engine API design) | Kill-test: orchestrator kill after step 1 completes triggers correct compensation |
| P4: Over-abstracting (scope creep) | Phase 1 (scope cap) | LOC check: engine module < 300 lines before checkout registration |
| P5: Exactly-once broken | Phase 1 + Phase 2 | Concurrent request test: two requests with same order_id, only one SAGA record created |
| P6: CircuitBreaker swallowed | Phase 1 (step executor) | Mock test: tripped breaker triggers compensation not retry |
| P7: Transport toggle broken | Phase 2 (checkout rewrite) | Integration test: full checkout in COMM_MODE=queue passes |
| P8: Abstraction hurts readability | Phase 2 + Phase 3 | Manual review: checkout_workflow readable without engine knowledge |
| P9: Recovery uses old step sequence | Phase 2 (checkout rewrite) | Kill-test: kill after every possible SAGA state, verify recovery |
| P10: Integration tests broken | All phases (continuous) | CI: `pytest tests/` green after every commit |
| P11: Circular imports | Phase 1 (module structure) | `python -c "import engine"` succeeds with no import errors |
| P12: Key namespace collision | Phase 1 (engine design) | No new top-level Redis key prefixes; check `scan_iter` patterns in recovery |
| P13: Idempotency key mismatch | Phase 2 (checkout registration) | Retry test: duplicate step execution does not double-decrement stock |
| P14: Refactoring changes behavior | Phase 3 (refactoring) | Benchmark immediately after refactoring: 0 consistency violations |

---

## Sources

- Direct codebase analysis: `/orchestrator/saga.py`, `tpc.py`, `grpc_server.py`, `recovery.py`, `transport.py`, `app.py`
- [Temporal Workflow Engine Design Principles](https://temporal.io/blog/workflow-engine-principles) — what a real workflow engine needs to handle
- [Microservices.io SAGA Pattern](https://microservices.io/patterns/data/saga.html) — compensation and consistency guarantees
- [Workflow Engine Design Proposal](https://www.architecture-weekly.com/p/workflow-engine-design-proposal-tell) — common over-engineering traps
- [Code Refactoring: When to Refactor and How to Avoid Mistakes](https://www.tembo.io/blog/code-refactoring) — refactoring + feature addition concurrently
- [Workflow Design Anti-Patterns](https://docs.fluentcommerce.com/essential-knowledge/workflow-design-anti-patterns) — abstraction complexity traps
- v2.0 PITFALLS.md (2026-03-12) — existing consistency guarantees to preserve

---
*Pitfalls research for: abstract workflow engine layer over existing SAGA/2PC orchestrator*
*Researched: 2026-03-26 — v3.0 milestone for DDS26-8*
