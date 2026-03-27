# Phase 15: Execution Strategies - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 15-execution-strategies
**Areas discussed:** Strategy interface design, Retry policy ownership, State enum placement, Event publishing scope

---

## Strategy Interface Design

| Option | Description | Selected |
|--------|-------------|----------|
| execute() only | Both strategies expose only execute() | |
| execute() + compensate() on SagaStrategy | SagaStrategy gets separate compensate() for recovery scanner; TwoPhaseStrategy only execute() | ✓ |
| Full interface with execute/compensate/resume on both | Both strategies expose all lifecycle methods | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Recovery scanner (Phase 17) needs independent compensation trigger, justifying separate method on SagaStrategy only.

---

## Retry Policy Ownership

| Option | Description | Selected |
|--------|-------------|----------|
| Extract to shared retry.py | Move retry_forward/retry_forever from grpc_server.py to orchestrator/retry.py | ✓ |
| Embed in each strategy | Each strategy has its own retry logic | |
| Accept callable retry wrappers | Strategies receive retry functions as params | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Existing retry functions are proven; extraction keeps strategies clean.

---

## State Enum Placement

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse exact values from saga.py/tpc.py | Copy SAGA_STATES and TPC_STATES into strategy modules | ✓ |
| Define fresh generic states | New state names like RUNNING/COMPENSATING/DONE | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Reusing exact values avoids breaking recovery scanner (Phase 17) and keeps continuity with existing Redis data.

---

## Event Publishing Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Strategies publish events | Each strategy calls publish_event() directly | |
| Defer to engine (Phase 16) | Strategies return results; engine publishes events | ✓ |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Keeps strategies testable without mocking event infrastructure. Engine is the natural event publisher.

---

## Claude's Discretion

All four gray areas were deferred to Claude's judgment. User expressed trust in defaults.

## Deferred Ideas

None — discussion stayed within phase scope.
