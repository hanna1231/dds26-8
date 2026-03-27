# Phase 14: Engine Core - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 14-engine-core
**Areas discussed:** Redis key prefix scheme, Step completion tracking, WorkflowDefinition data model, State machine design

---

## Redis Key Prefix Scheme

| Option | Description | Selected |
|--------|-------------|----------|
| Unified `{workflow:*}` | New prefix for all workflow records; old prefixes untouched until Phase 18 | ✓ |
| Reuse `{saga:*}/{tpc:*}` | Keep existing prefixes; recovery scanner unchanged | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** STATE.md flagged this as must-decide before coding. Claude selected unified prefix since old modules get deleted in Phase 18 anyway.

---

## Step Completion Tracking

| Option | Description | Selected |
|--------|-------------|----------|
| Flat hash fields (`step_N_done`) | Direct replacement for stock_reserved/payment_charged | ✓ |
| Nested JSON | Single field with JSON object tracking all step completions | |
| Bitmap | Bit-per-step in a single field | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Flat fields match existing codebase pattern and are simplest to query.

---

## WorkflowDefinition Data Model

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal (name, steps, strategy) | Only what ENG-01/ENG-02 require | ✓ |
| Extended (+ metadata, timeouts) | Additional fields for future extensibility | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Retry/timeout behavior is strategy-internal per existing SAGA compensation pattern.

---

## State Machine Design

| Option | Description | Selected |
|--------|-------------|----------|
| Strategy-owned states | Store does blind CAS; strategies define their own state enums | ✓ |
| Unified state enum | Single set of states shared by SAGA and 2PC | |

**User's choice:** Claude's Discretion — user deferred all decisions
**Notes:** Keeps WorkflowStore truly generic — no knowledge of protocol-specific states.

---

## Claude's Discretion

All four gray areas were deferred to Claude's judgment. User expressed trust in defaults and no opinion on implementation specifics.

## Deferred Ideas

None — discussion stayed within phase scope.
