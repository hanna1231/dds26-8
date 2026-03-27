# Phase 14: Engine Core - Research

**Researched:** 2026-03-27
**Domain:** Python dataclasses, Redis async patterns, Lua CAS, workflow state persistence
**Confidence:** HIGH

## Summary

Phase 14 is a pure extraction and abstraction exercise. The implementation assets already exist verbatim in `orchestrator/saga.py` and `orchestrator/tpc.py`. The Lua CAS script is byte-for-byte identical in both files and can be copied without modification. The HSETNX creation guard, 7-day TTL, and `hgetall`/byte-decode retrieval pattern are all established and working in production code.

The two new modules are `orchestrator/workflow_types.py` (two dataclasses: `WorkflowStep` and `WorkflowDefinition`) and `orchestrator/workflow_store.py` (three async functions: `create`, `transition`, `mark_step_done`). Both modules are parallel companions to saga.py/tpc.py — they do not replace them yet (deletion is Phase 18).

The critical design insight from D-04 is that `WorkflowStore` is intentionally state-agnostic: it performs blind Lua CAS without validating state names. Strategies (Phase 15) own their state enums and valid-transition dicts; they validate before calling `store.transition()`. This keeps the store generic and eliminates the need for any protocol-specific knowledge in this phase.

**Primary recommendation:** Extract TRANSITION_LUA verbatim from saga.py, replicate the HSETNX+hset+expire create pattern with `{workflow:<id>}` key prefix, and add `mark_step_done` as a thin wrapper that writes `step_N_done = "1"` into the hash. Write two minimal dataclasses. Total new code is approximately 120–150 lines.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 (Key Prefix):** Use unified `{workflow:<workflow_id>}` key prefix for all workflow records. The existing `{saga:*}` and `{tpc:*}` prefixes stay untouched in saga.py/tpc.py until Phase 18 deletion. Recovery scanner update happens in Phase 17.
- **D-02 (Step Completion Flags):** Use flat hash fields `step_0_done`, `step_1_done`, etc. as completion flags — directly replacing hardcoded `stock_reserved`/`payment_charged` fields. No nested JSON or bitmaps. Consistent with existing HSET/HGET patterns.
- **D-03 (WorkflowDefinition):** Minimal dataclass: `name` (str), `steps` (list[WorkflowStep]), `strategy` (str literal "saga" | "2pc"). No timeout config, retry policy, or metadata fields. Retry behavior is strategy-internal (already exists in SAGA compensation logic).
- **D-04 (State Machine Design):** WorkflowStore is state-agnostic — performs blind Lua CAS transitions. Each strategy defines its own state enum and valid transitions dict. The store never validates state names; strategies validate before calling `store.transition()`.

### Claude's Discretion

- Exact dataclass field types and defaults
- WorkflowStore method signatures beyond what success criteria require
- Lua script structure (can extract verbatim from saga.py/tpc.py since they're identical)
- Hash field naming conventions for workflow metadata (order_id, user_id, items_json, etc.)
- TTL policy (7-day expiry pattern from existing code)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ENG-01 | WorkflowStep dataclass with name, async action callable, and async compensation callable | Python `dataclasses` module with `Callable` type hints; `asyncio` protocol for callables |
| ENG-02 | WorkflowDefinition dataclass with name, ordered steps list, and strategy field (saga/2pc) | Python `dataclasses`; `Literal["saga", "2pc"]` type annotation via `typing` module |
| ENG-04 | Durable workflow state persisted in Redis using existing Lua CAS transition pattern | TRANSITION_LUA extracted verbatim from saga.py lines 42–49; `db.eval()` call pattern documented below |
| ENG-05 | Per-step completion flags (step_N_done) replacing hardcoded field names | HSET with `f"step_{index}_done"` key; creation mapping must omit hardcoded `stock_reserved`/`payment_charged` |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis[hiredis] | 5.0.3 (pinned in requirements.txt) | Async Redis client | Already used project-wide; `redis.asyncio` provides `db.eval()`, `db.hsetnx()`, `db.hset()`, `db.expire()`, `db.hgetall()` |
| Python dataclasses | stdlib (Python 3.13.1) | WorkflowStep / WorkflowDefinition data model | No dependencies; `@dataclass` with `field()` for mutable defaults |
| Python typing | stdlib | `Callable`, `Awaitable`, `Literal` type hints | Enables static analysis of async callable fields |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio | stdlib | Async execution context | All WorkflowStore methods are async; callable fields are awaited by strategies |
| json | stdlib | items serialization in hash | Existing `json.dumps(items)` pattern from saga.py — only needed if create() accepts domain metadata |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `@dataclass` | `pydantic.BaseModel` | pydantic adds validation but is not in requirements.txt and adds a dep; dataclass is sufficient |
| flat `step_N_done` fields | bitmask or JSON array | flat fields are consistent with existing HSET/HGET pattern; bitmask requires bit-manipulation in Lua |
| `db.eval()` | `db.register_script()` (cached SHA) | `register_script` / `evalsha` avoids re-sending the script on every call but adds complexity; EVAL is simpler and fine for this scope |

**Installation:** No new packages required. All dependencies are in `orchestrator/requirements.txt` already.

---

## Architecture Patterns

### Recommended Project Structure

```
orchestrator/
├── workflow_types.py    # WorkflowStep, WorkflowDefinition dataclasses
├── workflow_store.py    # WorkflowStore: create(), transition(), mark_step_done(), get()
├── saga.py             # UNTOUCHED until Phase 18
├── tpc.py              # UNTOUCHED until Phase 18
└── transport.py        # UNTOUCHED (step callables live here for Phase 16)
tests/
└── test_workflow_store.py  # Unit tests for ENG-01, ENG-02, ENG-04, ENG-05
```

### Pattern 1: WorkflowStep and WorkflowDefinition Dataclasses

**What:** Two minimal `@dataclass` definitions in `workflow_types.py`. `WorkflowStep` wraps a named pair of async callables. `WorkflowDefinition` wraps an ordered list of steps with a strategy selector.

**When to use:** Constructed by Phase 16 checkout_workflow.py and Phase 15 strategies. Phase 14 just defines the types.

**Example:**

```python
# orchestrator/workflow_types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any, Literal


@dataclass
class WorkflowStep:
    name: str
    action: Callable[..., Awaitable[Any]]
    compensation: Callable[..., Awaitable[Any]]


@dataclass
class WorkflowDefinition:
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    strategy: Literal["saga", "2pc"] = "saga"
```

**Notes on discretion areas:**
- `action` / `compensation` are typed as `Callable[..., Awaitable[Any]]` — the exact signature is not enforced here; strategies accept any async callable that matches their call convention.
- `steps` uses `field(default_factory=list)` to avoid the mutable-default pitfall.
- `strategy` defaults to `"saga"` — reasonable since saga is the simpler path.

### Pattern 2: WorkflowStore with Lua CAS

**What:** Module-level functions (or a class with `__init__(self, db)`) mirroring saga.py structure. The Lua CAS script is extracted verbatim. `create()` uses HSETNX for exactly-once semantics. `transition()` calls `db.eval()`. `mark_step_done()` writes a single HSET field.

**When to use:** Called by strategies (Phase 15) and eventually the engine (Phase 16).

**Example:**

```python
# orchestrator/workflow_store.py
import time
import json

TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""

# Source: saga.py:42-49 and tpc.py:43-50 — verbatim copy, confirmed identical


def _workflow_key(workflow_id: str) -> str:
    """Produce Redis hash-tagged key for cluster slot locality."""
    return f"{{workflow:{workflow_id}}}"


async def create(
    db,
    workflow_id: str,
    initial_state: str,
    metadata: dict | None = None,
) -> bool:
    """
    Atomically create a new workflow record.

    Uses HSETNX on 'state' to prevent duplicate creation (exactly-once guarantee).
    Returns True if created, False if record already existed.
    """
    key = _workflow_key(workflow_id)
    now = str(int(time.time()))

    created = await db.hsetnx(key, "state", initial_state)
    if not created:
        return False

    fields: dict = {
        "workflow_id": workflow_id,
        "started_at": now,
        "updated_at": now,
    }
    if metadata:
        for k, v in metadata.items():
            fields[k] = v if isinstance(v, str) else json.dumps(v)

    await db.hset(key, mapping=fields)
    await db.expire(key, 7 * 24 * 3600)
    return True


async def transition(
    db,
    workflow_id: str,
    from_state: str,
    to_state: str,
    flag_field: str = "",
    flag_value: str = "",
) -> bool:
    """
    Atomically transition workflow state using Lua CAS.

    State-agnostic: no transition validation. Caller (strategy) must validate
    before calling this function. Returns True if transition applied, False
    if current state did not match from_state.
    """
    key = _workflow_key(workflow_id)
    result = await db.eval(
        TRANSITION_LUA,
        1,
        key,
        from_state,
        to_state,
        flag_field,
        flag_value,
    )
    return bool(result)


async def mark_step_done(db, workflow_id: str, step_index: int) -> None:
    """
    Write step_N_done = "1" into the workflow hash.

    Replaces hardcoded field names (stock_reserved, payment_charged).
    """
    key = _workflow_key(workflow_id)
    await db.hset(key, f"step_{step_index}_done", "1")


async def get(db, workflow_id: str) -> dict | None:
    """
    Retrieve workflow record and decode bytes.
    Returns None if no record exists.
    """
    raw = await db.hgetall(_workflow_key(workflow_id))
    if not raw:
        return None
    return {k.decode(): v.decode() for k, v in raw.items()}
```

### Pattern 3: WorkflowStore as Class (alternative to module functions)

**What:** If the planner prefers an injectable class (aligns with REF-03 future requirement), `WorkflowStore` can be a class that stores `self.db`.

**When to use:** Class form is better if Phase 16 will inject the engine as a dependency. Module functions are simpler for Phase 14 isolation. Either is fine — choose one and stay consistent.

```python
class WorkflowStore:
    def __init__(self, db):
        self._db = db

    async def create(self, workflow_id: str, initial_state: str, metadata: dict | None = None) -> bool:
        ...

    async def transition(self, workflow_id: str, from_state: str, to_state: str, ...) -> bool:
        ...

    async def mark_step_done(self, workflow_id: str, step_index: int) -> None:
        ...

    async def get(self, workflow_id: str) -> dict | None:
        ...
```

### Anti-Patterns to Avoid

- **State validation in WorkflowStore:** Do NOT add transition validation (like `VALID_TRANSITIONS` dicts) inside workflow_store.py. D-04 is explicit: strategies own state machine rules.
- **Hardcoded field names in create():** Do NOT write `"stock_reserved": "0"` or `"payment_charged": "0"` in the workflow creation mapping. ENG-05 requires generic `step_N_done` flags — these are written later by `mark_step_done()`.
- **decode_responses=True on the Redis client:** Existing pattern uses manual `k.decode(): v.decode()` in `get()`. Changing this would break the `db.eval()` integer return (Lua returns integer 0/1 which must not be decoded).
- **Lua script modification:** Do not alter TRANSITION_LUA. The optional field (`ARGV[3]`/`ARGV[4]`) pattern is already implemented and allows atomic step-flag writes during transitions if needed.
- **Mutable default for `steps` field:** `steps: list[WorkflowStep] = []` in a dataclass is a Python error. Must use `field(default_factory=list)`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic state CAS | Custom get-compare-set in Python | TRANSITION_LUA from saga.py | Race condition window between GET and SET; Lua executes atomically on Redis server |
| Exactly-once record creation | `exists()` + `hset()` | `hsetnx()` on state field | HSETNX is a single atomic Redis command; exists+set has TOCTOU race |
| Byte decoding | Custom decoder | `{k.decode(): v.decode() for k,v in raw.items()}` | Already works; do not change the Redis client to `decode_responses=True` as it affects integer returns from `eval()` |
| Cluster key locality | Manual sharding | Hash tag `{workflow:id}` in key | Curly-brace hash tags ensure all keys for a workflow land on the same Redis cluster slot |

**Key insight:** The entire Lua CAS infrastructure is already tested and production-proven in saga.py and tpc.py. The value of this phase is reuse without modification, not reimplementation.

---

## Common Pitfalls

### Pitfall 1: Mutable Default in Dataclass

**What goes wrong:** `WorkflowDefinition(steps=[])` at class body level causes all instances to share the same list object.

**Why it happens:** Python evaluates default argument once at class definition time, not at instantiation.

**How to avoid:** Use `steps: list[WorkflowStep] = field(default_factory=list)`.

**Warning signs:** `wd1.steps.append(x)` also modifies `wd2.steps`.

### Pitfall 2: decode_responses Conflicts with eval() Return

**What goes wrong:** If `db = redis.Redis(decode_responses=True)` is used, `db.eval()` returns a decoded string `"1"` instead of integer `1`. The existing `bool(result)` check still works, but `db.hgetall()` returns `{str: str}` and the manual decode loop breaks with a `bytes has no .decode()` error on string objects.

**Why it happens:** `decode_responses=True` applies to all responses including eval returns; the existing code contracts on raw bytes everywhere.

**How to avoid:** Keep all Redis clients without `decode_responses=True`, consistent with the rest of the codebase.

**Warning signs:** `AttributeError: 'str' object has no attribute 'decode'` in the `get()` function.

### Pitfall 3: Key Prefix Collision with Existing saga:/tpc: Keys

**What goes wrong:** Using `{saga:id}` or `{tpc:id}` as the new workflow key prefix would collide with existing records during the migration period (Phases 14–17).

**Why it happens:** The old modules are not deleted until Phase 18.

**How to avoid:** D-01 is locked: use `{workflow:<workflow_id>}` only.

**Warning signs:** `get()` returns unexpected fields (e.g., `stock_reserved`, `protocol`) from old-format records.

### Pitfall 4: Concurrent create() Calls with Two-Step Init

**What goes wrong:** If `create()` does `hsetnx` then `hset` in sequence, a second caller could call `hsetnx` before the first caller has finished writing metadata fields. The second caller gets `False` (correct), but if the first caller crashes between hsetnx and hset, the record exists with only `state` set and missing all other fields.

**Why it happens:** The create pattern is inherently two-phase (claim + populate). This is the same pattern in saga.py.

**How to avoid:** The recovery scanner (Phase 17) handles crash-recovery. For Phase 14, the pattern is correct as-is — it matches saga.py exactly. Document the invariant: a record with only `state` is recoverable.

**Warning signs:** `get()` returns a dict with only the `state` key.

### Pitfall 5: Callable Type Annotation Complexity

**What goes wrong:** Over-specifying the `action`/`compensation` callable signatures makes `WorkflowStep` unusable across strategies that call them differently.

**Why it happens:** `Callable[[str, int], Awaitable[dict]]` is rigid; SAGA calls with positional args but 2PC uses keyword args or different arities.

**How to avoid:** Use `Callable[..., Awaitable[Any]]` — the `...` means "any arguments". Type checking on call sites is the strategy's responsibility.

---

## Code Examples

Verified patterns from existing codebase (orchestrator/saga.py and orchestrator/tpc.py):

### HSETNX Exactly-Once Creation (saga.py:83)

```python
# Source: orchestrator/saga.py lines 79-103
created = await db.hsetnx(saga_key, "state", "STARTED")
if not created:
    return False
await db.hset(saga_key, mapping={...})
await db.expire(saga_key, 7 * 24 * 3600)
return True
```

### Lua CAS Transition (saga.py:42-49, identical in tpc.py:43-50)

```python
# Source: orchestrator/saga.py lines 42-49
TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""

result = await db.eval(TRANSITION_LUA, 1, key, from_state, to_state, flag_field, flag_value)
return bool(result)
```

### Byte-Decode Retrieval (saga.py:165-168)

```python
# Source: orchestrator/saga.py lines 165-168
raw = await db.hgetall(f"{{saga:{order_id}}}")
if not raw:
    return None
return {k.decode(): v.decode() for k, v in raw.items()}
```

### Hash Tag Key Format

```python
# Pattern from saga.py line 79, tpc.py line 80
# Curly braces create a Redis hash tag ensuring cluster slot locality
saga_key = f"{{saga:{order_id}}}"   # existing
tpc_key  = f"{{tpc:{order_id}}}"    # existing
wf_key   = f"{{workflow:{workflow_id}}}"  # new — Phase 14
```

### Dataclass with Callable Field

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any, Literal

@dataclass
class WorkflowStep:
    name: str
    action: Callable[..., Awaitable[Any]]
    compensation: Callable[..., Awaitable[Any]]

@dataclass
class WorkflowDefinition:
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    strategy: Literal["saga", "2pc"] = "saga"
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded `stock_reserved`, `payment_charged` field names | Generic `step_0_done`, `step_1_done` flags | Phase 14 (now) | Strategies can add any number of steps without modifying the store |
| Protocol-specific state enums inside store (SAGA_STATES, TPC_STATES) | State-agnostic store; strategies own enums | Phase 14 (now) | Store is reusable for future protocols without modification |
| Separate saga.py / tpc.py modules per protocol | Single workflow_store.py shared across protocols | Phase 14 (now), old files deleted Phase 18 | Single source of truth for persistence; less duplication |

---

## Open Questions

1. **WorkflowStore as class vs. module functions**
   - What we know: Both approaches work. Module functions are simpler now; class form aligns with REF-03 (injectable dependency).
   - What's unclear: Phase 16 will construct WorkflowEngine with injectable db — whether it passes `db` to WorkflowStore functions or constructs a `WorkflowStore(db)` instance.
   - Recommendation: Use class form now to pre-align with REF-03 and avoid refactoring in Phase 16. Cost is ~5 lines of boilerplate.

2. **`create()` metadata parameter design**
   - What we know: CONTEXT.md leaves hash field naming conventions to Claude's discretion.
   - What's unclear: Phase 16 checkout_workflow.py will need to store `order_id`, `user_id`, `items_json`, `total_cost` in the workflow hash for recovery. The create() signature needs to accept these without becoming checkout-specific.
   - Recommendation: Accept an optional `metadata: dict | None = None` parameter that gets HSET alongside the system fields. Strategies/engine pass domain fields as a dict. This keeps `workflow_store.py` generic while allowing arbitrary metadata storage.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All code | Yes | 3.13.1 | — |
| redis[hiredis] | WorkflowStore | Yes | 5.0.3 | — |
| Redis server | Tests | Yes | responds to PING | — |
| pytest + pytest-asyncio | Test suite | Yes (in tests/) | see pytest.ini | — |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `/Users/daniel/WebstormProjects/dds26-8/pytest.ini` (asyncio_mode = auto) |
| Quick run command | `pytest tests/test_workflow_store.py -x -v` |
| Full suite command | `pytest tests/ -x -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ENG-01 | WorkflowStep has name, action, compensation fields | unit | `pytest tests/test_workflow_store.py::test_workflow_step_fields -x` | No — Wave 0 |
| ENG-01 | WorkflowStep action and compensation are async callables | unit | `pytest tests/test_workflow_store.py::test_workflow_step_callables_async -x` | No — Wave 0 |
| ENG-02 | WorkflowDefinition has name, steps, strategy fields | unit | `pytest tests/test_workflow_store.py::test_workflow_definition_fields -x` | No — Wave 0 |
| ENG-02 | WorkflowDefinition strategy accepts "saga" and "2pc" | unit | `pytest tests/test_workflow_store.py::test_workflow_definition_strategy -x` | No — Wave 0 |
| ENG-04 | WorkflowStore.create() initializes Redis hash with HSETNX | integration | `pytest tests/test_workflow_store.py::test_workflow_store_create -x` | No — Wave 0 |
| ENG-04 | Concurrent create() calls for same workflow_id are idempotent | integration | `pytest tests/test_workflow_store.py::test_workflow_store_create_duplicate -x` | No — Wave 0 |
| ENG-04 | WorkflowStore.transition() applies Lua CAS atomically | integration | `pytest tests/test_workflow_store.py::test_workflow_store_transition_valid -x` | No — Wave 0 |
| ENG-04 | WorkflowStore.transition() returns False when state mismatch | integration | `pytest tests/test_workflow_store.py::test_workflow_store_transition_mismatch -x` | No — Wave 0 |
| ENG-05 | mark_step_done() writes step_N_done = "1" | integration | `pytest tests/test_workflow_store.py::test_workflow_store_mark_step_done -x` | No — Wave 0 |
| ENG-05 | Multiple step flags coexist without collision | integration | `pytest tests/test_workflow_store.py::test_workflow_store_multiple_steps -x` | No — Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_workflow_store.py -x -v`
- **Per wave merge:** `pytest tests/ -x -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_workflow_store.py` — covers ENG-01, ENG-02, ENG-04, ENG-05 (does not exist yet)
- [ ] `orchestrator/workflow_types.py` — source module (does not exist yet)
- [ ] `orchestrator/workflow_store.py` — source module (does not exist yet)

No new pytest infrastructure required — existing `conftest.py` already provides `orchestrator_db` and `clean_orchestrator_db` fixtures on Redis db=3 that `test_workflow_store.py` can reuse directly.

---

## Sources

### Primary (HIGH confidence)

- `orchestrator/saga.py` (local codebase) — TRANSITION_LUA, HSETNX pattern, byte-decode pattern, key format
- `orchestrator/tpc.py` (local codebase) — confirms Lua CAS is identical across protocols
- `tests/conftest.py` (local codebase) — `orchestrator_db`, `clean_orchestrator_db`, `tpc_db` fixtures available for reuse
- `tests/test_saga.py` (local codebase) — test patterns for WorkflowStore unit tests
- `tests/test_tpc.py` (local codebase) — confirms fixture and assertion patterns
- `pytest.ini` (local codebase) — asyncio_mode = auto, asyncio_default_fixture_loop_scope = session

### Secondary (MEDIUM confidence)

- Python 3.13 `dataclasses` stdlib documentation — `@dataclass`, `field(default_factory=...)`, `Literal` type usage
- redis-py 5.0 `eval()` documentation — confirmed integer return from Lua script

### Tertiary (LOW confidence)

None.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries are already in use in the project; no new dependencies
- Architecture: HIGH — implementation is an extraction from existing working code; patterns are verified
- Pitfalls: HIGH — pitfalls are derived from reading the actual codebase and known Python dataclass rules

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (stable domain; Python stdlib + redis-py don't change meaningfully in 30 days)
