# Phase 11: 2PC State Machine & Participants - Research

**Researched:** 2026-03-12
**Domain:** Two-Phase Commit protocol implementation with Redis Lua scripting
**Confidence:** HIGH

## Summary

Phase 11 implements the 2PC (Two-Phase Commit) state machine and participant-side tentative reservation logic. This builds on Phase 8's business logic extraction (stock/payment `operations.py` modules) and mirrors the existing SAGA state machine pattern (`orchestrator/saga.py`) but with 2PC-specific states and transition rules.

The core difference from SAGA: in SAGA, services execute operations fully and compensate on failure. In 2PC, services perform **tentative reservations** during PREPARE that are finalized by COMMIT or released by ABORT. This requires new Lua scripts that manage tentative holds separately from actual balances, and a new state machine with states INIT, PREPARING, COMMITTING, ABORTING, COMMITTED, ABORTED.

**Primary recommendation:** Create a `orchestrator/tpc.py` module mirroring `saga.py` for the 2PC state machine, and add 2PC-specific Lua scripts to `stock/operations.py` and `payment/operations.py` for tentative reservation logic (prepare/commit/abort). All Lua scripts must be idempotent using the same idempotency key pattern established in SAGA.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TPC-01 | 2PC state machine with states INIT->PREPARING->COMMITTING/ABORTING->COMMITTED/ABORTED using Lua CAS transitions | Mirror saga.py pattern: define TPC_STATES, TPC_VALID_TRANSITIONS, reuse TRANSITION_LUA script, create tpc.py with create/transition/get functions |
| TPC-02 | Stock service tentative reservation Lua scripts (prepare reserves, commit finalizes, abort releases) | Add prepare_stock, commit_stock, abort_stock to stock/operations.py using Lua CAS + tentative hold keys |
| TPC-03 | Payment service tentative reservation Lua scripts (prepare reserves, commit finalizes, abort releases) | Add prepare_payment, commit_payment, abort_payment to payment/operations.py using Lua CAS + tentative hold keys |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis.asyncio | existing | Async Redis client for Lua EVAL | Already in use, supports EVAL for Lua CAS |
| msgspec | existing | Binary serialization for StockValue/UserValue | Already used for stock/payment data encoding |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest + pytest-asyncio | existing | Unit testing Lua scripts against real Redis | Test all state transitions and Lua idempotency |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Separate tentative hold keys | Encoding hold in StockValue struct | Separate keys are cleaner, avoid schema migration, hash-tag co-location handles atomicity |

**Installation:**
No new dependencies needed. All libraries already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
orchestrator/
├── saga.py              # Existing SAGA state machine (unchanged)
├── tpc.py               # NEW: 2PC state machine (mirrors saga.py)
stock/
├── operations.py        # ADD: prepare_stock, commit_stock, abort_stock
payment/
├── operations.py        # ADD: prepare_payment, commit_payment, abort_payment
tests/
├── test_tpc.py          # NEW: 2PC state machine + participant tests
```

### Pattern 1: 2PC State Machine (tpc.py)
**What:** Redis-persisted 2PC records with Lua CAS state transitions, mirroring saga.py
**When to use:** For all 2PC transaction coordination

The 2PC state machine follows the same pattern as saga.py:
- Redis hash stores transaction record (`{tpc:<order_id>}`)
- Lua CAS script atomically transitions state (same TRANSITION_LUA from saga.py can be reused)
- Python validates transition before calling Lua (fail-fast)

```python
TPC_STATES = {
    "INIT",
    "PREPARING",
    "COMMITTING",
    "ABORTING",
    "COMMITTED",
    "ABORTED",
}

TPC_VALID_TRANSITIONS: dict[str, set[str]] = {
    "INIT": {"PREPARING"},
    "PREPARING": {"COMMITTING", "ABORTING"},
    "COMMITTING": {"COMMITTED"},
    "ABORTING": {"ABORTED"},
}
```

The record hash includes fields:
- `state`, `order_id`, `user_id`, `total_cost`, `items_json`
- `protocol`: `"2pc"` (distinguishes from SAGA records for recovery scanner -- TPC-06)
- `stock_prepared`, `payment_prepared` (participant readiness flags)
- `started_at`, `updated_at`

### Pattern 2: Tentative Reservation (Stock Participant)
**What:** PREPARE atomically deducts stock AND writes a tentative hold key; COMMIT deletes hold; ABORT reads hold and restores stock
**When to use:** Stock PREPARE/COMMIT/ABORT in 2PC

Key design: use a **tentative hold key** `{item:<id>}:hold:<order_id>` that stores the reserved quantity. This enables:
- PREPARE: atomically deduct stock + write hold key (single Lua script)
- COMMIT: delete hold key (stock already deducted, just clean up)
- ABORT: read hold key, restore stock, delete hold key (single Lua script)
- Idempotency: check hold key existence to detect duplicate PREPARE/COMMIT/ABORT

```
# Redis key layout for stock 2PC:
{item:<item_id>}                          # StockValue (msgpack)
{item:<item_id>}:hold:<order_id>          # tentative hold: quantity (string)
```

Hash tags `{item:<id>}` ensure item key and hold key are on the same Redis Cluster slot, enabling atomic Lua across both keys.

### Pattern 3: Tentative Reservation (Payment Participant)
**What:** Same pattern as stock but for user credit
**When to use:** Payment PREPARE/COMMIT/ABORT in 2PC

```
# Redis key layout for payment 2PC:
{user:<user_id>}                          # UserValue (msgpack)
{user:<user_id>}:hold:<order_id>          # tentative hold: amount (string)
```

### Pattern 4: Idempotency in 2PC Lua Scripts
**What:** Each PREPARE/COMMIT/ABORT checks for an existing hold key to determine if the operation already ran
**When to use:** All 2PC participant operations

Unlike SAGA idempotency (separate idempotency keys with JSON result caching), 2PC can use the hold key itself as the idempotency indicator:
- PREPARE: if hold key exists -> already prepared, return success
- COMMIT: if hold key does NOT exist -> already committed, return success
- ABORT: if hold key does NOT exist -> already aborted (or never prepared), return success

This is simpler than the SAGA idempotency pattern and avoids additional keys.

### Anti-Patterns to Avoid
- **Modifying StockValue/UserValue structs for 2PC:** Adding fields to existing msgpack structs would break backward compatibility and require migration. Use separate hold keys instead.
- **Non-atomic PREPARE (read-then-write without Lua):** The PREPARE must deduct stock AND write the hold key in a single Lua EVAL to prevent partial state on crash.
- **Sharing Lua scripts between SAGA and 2PC paths:** Keep SAGA operations and 2PC operations in separate functions for clarity, even if Lua patterns are similar.
- **Using MULTI/EXEC instead of Lua:** Lua EVAL is atomic and allows conditional logic; MULTI/EXEC cannot branch on intermediate results.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic multi-key operations | Python-level locking or MULTI/EXEC | Lua EVAL scripts | Redis guarantees atomic execution of Lua, handles crashes mid-script |
| State machine validation | Ad-hoc if/else chains | Transition table + Lua CAS (like saga.py) | Proven pattern in this codebase, prevents invalid transitions |
| Tentative hold tracking | In-memory tracking or separate service | Redis keys co-located via hash tags | Survives crashes, atomic with item/user keys |

**Key insight:** The existing saga.py + operations.py patterns solve all the hard problems (Lua CAS, idempotency, hash-tag co-location). The 2PC implementation should mirror these patterns exactly, just with different states and hold-key semantics.

## Common Pitfalls

### Pitfall 1: Partial PREPARE across multiple items
**What goes wrong:** Stock PREPARE for item A succeeds but item B fails, leaving item A tentatively reserved with no COMMIT/ABORT path
**Why it happens:** Preparing multiple items non-atomically creates partial state
**How to avoid:** The PREPARE Lua script for stock must handle ALL items in a single order atomically. Use a single Lua EVAL that iterates over all items, checks all have sufficient stock, then deducts all and writes all hold keys. If any item lacks stock, none are reserved (all-or-nothing).
**Warning signs:** Test with multi-item orders where the second item has insufficient stock

### Pitfall 2: Hash tag mismatch between item key and hold key
**What goes wrong:** If hold key doesn't share the same hash tag slot as the item key, Lua EVAL fails with CROSSSLOT error in Redis Cluster
**Why it happens:** Redis Cluster routes keys to slots based on hash tag content
**How to avoid:** Use `{item:<id>}:hold:<order_id>` format -- the `{item:<id>}` prefix ensures same slot as `{item:<id>}`
**Warning signs:** Works in development (single Redis) but fails in production (Redis Cluster)

### Pitfall 3: Hold key not cleaned up after COMMIT/ABORT
**What goes wrong:** Stale hold keys accumulate in Redis, consuming memory
**Why it happens:** COMMIT or ABORT fails to delete the hold key
**How to avoid:** COMMIT and ABORT Lua scripts must always DELETE the hold key as their final step. Additionally, set TTL on hold keys as a safety net (e.g., 7 days matching SAGA TTL).
**Warning signs:** Growing Redis memory over time

### Pitfall 4: Multiple items in a single PREPARE -- key co-location
**What goes wrong:** Multi-item stock PREPARE uses keys from different hash tag groups, Lua EVAL fails
**Why it happens:** Each `{item:<item_id>}` has a different hash tag, so keys for different items are on different slots
**How to avoid:** For multi-item PREPARE, you CANNOT use a single Lua script across items (different slots). Instead, prepare each item individually in its own Lua EVAL. The "all-or-nothing" semantics come from the 2PC state machine itself: if any item PREPARE fails, the coordinator ABORTs all. Each individual item's PREPARE/ABORT is atomic via Lua.
**Warning signs:** CROSSSLOT errors in cluster mode with multi-item orders

### Pitfall 5: Confusing SAGA operations with 2PC operations
**What goes wrong:** 2PC code path accidentally calls reserve_stock (SAGA function) instead of prepare_stock (2PC function)
**Why it happens:** Both exist in the same operations.py module
**How to avoid:** Clear naming convention: SAGA uses `reserve_stock`/`charge_payment`, 2PC uses `prepare_stock`/`prepare_payment`. Document which is which.
**Warning signs:** Idempotency keys collide between SAGA and 2PC paths

## Code Examples

### 2PC State Machine (tpc.py) -- modeled on saga.py
```python
# Source: Derived from orchestrator/saga.py pattern
import json
import time

TPC_STATES = {"INIT", "PREPARING", "COMMITTING", "ABORTING", "COMMITTED", "ABORTED"}

TPC_VALID_TRANSITIONS: dict[str, set[str]] = {
    "INIT": {"PREPARING"},
    "PREPARING": {"COMMITTING", "ABORTING"},
    "COMMITTING": {"COMMITTED"},
    "ABORTING": {"ABORTED"},
}

# Reuse the same Lua CAS script from saga.py
TRANSITION_LUA = """
local current = redis.call('HGET', KEYS[1], 'state')
if current ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', tostring(math.floor(redis.call('TIME')[1])))
if ARGV[3] ~= '' then redis.call('HSET', KEYS[1], ARGV[3], ARGV[4]) end
return 1
"""

async def create_tpc_record(db, order_id, user_id, items, total_cost) -> bool:
    tpc_key = f"{{tpc:{order_id}}}"
    now = str(int(time.time()))
    created = await db.hsetnx(tpc_key, "state", "INIT")
    if not created:
        return False
    await db.hset(tpc_key, mapping={
        "order_id": order_id,
        "user_id": user_id,
        "total_cost": str(total_cost),
        "items_json": json.dumps(items),
        "protocol": "2pc",
        "stock_prepared": "0",
        "payment_prepared": "0",
        "started_at": now,
        "updated_at": now,
    })
    await db.expire(tpc_key, 7 * 24 * 3600)
    return True
```

### Stock PREPARE Lua (single item, atomic deduct + hold)
```python
# Source: Derived from stock/operations.py RESERVE_STOCK_ATOMIC_LUA pattern
#
# KEYS[1] = item key       {item:<item_id>}
# KEYS[2] = hold key       {item:<item_id>}:hold:<order_id>
# ARGV[1] = quantity to reserve
# ARGV[2] = new item bytes (pre-computed, stock decremented)
# ARGV[3] = expected current raw bytes (CAS comparison)
#
# Returns: "OK", "ALREADY_PREPARED", "RETRY", or JSON error
PREPARE_STOCK_LUA = """
local item_key = KEYS[1]
local hold_key = KEYS[2]
local quantity = ARGV[1]
local new_bytes = ARGV[2]
local expected_raw = ARGV[3]

-- Idempotency: if hold key exists, already prepared
local existing_hold = redis.call('GET', hold_key)
if existing_hold then
    return 'ALREADY_PREPARED'
end

-- Check item exists
local raw = redis.call('GET', item_key)
if not raw then
    return '{"success":false,"error":"item not found"}'
end

-- CAS check
if raw ~= expected_raw then
    return 'RETRY'
end

-- Atomic: deduct stock + write hold key
redis.call('SET', item_key, new_bytes)
redis.call('SET', hold_key, quantity, 'EX', 604800)
return 'OK'
"""
```

### Stock COMMIT Lua (delete hold key, stock already deducted)
```python
# KEYS[1] = hold key  {item:<item_id>}:hold:<order_id>
# Returns: "OK" (idempotent -- succeeds even if hold already deleted)
COMMIT_STOCK_LUA = """
redis.call('DEL', KEYS[1])
return 'OK'
"""
```

### Stock ABORT Lua (restore stock + delete hold key)
```python
# KEYS[1] = item key       {item:<item_id>}
# KEYS[2] = hold key       {item:<item_id>}:hold:<order_id>
#
# Returns: "OK", "ALREADY_ABORTED" (hold key gone)
ABORT_STOCK_LUA = """
local item_key = KEYS[1]
local hold_key = KEYS[2]

-- Idempotency: if hold key gone, already aborted (or never prepared)
local hold_qty = redis.call('GET', hold_key)
if not hold_qty then
    return 'ALREADY_ABORTED'
end

-- Read current item, add back reserved quantity
local raw = redis.call('GET', item_key)
if not raw then
    -- Item deleted? Just clean up hold key
    redis.call('DEL', hold_key)
    return 'OK'
end

-- Restore stock using msgpack manipulation would be complex in Lua;
-- instead, store the full new_bytes as ARGV[1] (Python pre-computes)
-- OR: use a simpler approach -- store stock as separate hash fields
--
-- Practical approach: Python reads current stock, adds hold_qty,
-- re-encodes, passes as ARGV. Lua CAS ensures consistency.
-- (Same CAS retry loop as reserve_stock)
local new_bytes = ARGV[1]
local expected_raw = ARGV[2]
if raw ~= expected_raw then
    return 'RETRY'
end
redis.call('SET', item_key, new_bytes)
redis.call('DEL', hold_key)
return 'OK'
"""
```

### Payment follows identical pattern
```python
# Same structure as stock but with user keys:
# KEYS: {user:<user_id>}, {user:<user_id>}:hold:<order_id>
# PREPARE: deduct credit + write hold key with amount
# COMMIT: delete hold key
# ABORT: read hold, restore credit, delete hold key
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SAGA-only transactions | SAGA + 2PC selectable via env var | v2.0 (now) | Need parallel state machine implementations |
| Full operation + compensate (SAGA) | Tentative hold + commit/abort (2PC) | v2.0 (now) | Different atomicity guarantees, simpler rollback |

**Key difference from SAGA pattern:**
- SAGA `reserve_stock`: deducts stock permanently, uses idempotency key for dedup, requires `release_stock` to compensate
- 2PC `prepare_stock`: deducts stock tentatively (with hold key), `commit_stock` just cleans up hold, `abort_stock` restores stock from hold

## Open Questions

1. **Multi-item PREPARE atomicity**
   - What we know: Each item is in a different hash tag group, so a single Lua EVAL cannot span items
   - What's unclear: Should prepare_stock take a list of items and loop (calling one Lua per item), or should the coordinator call prepare_stock once per item?
   - Recommendation: Have prepare_stock handle a single item. The coordinator calls it per-item in a loop. If any fails, coordinator sends ABORT to all. This matches the 2PC protocol (coordinator drives, participants respond).

2. **TPC record key prefix**
   - What we know: SAGA uses `{saga:<order_id>}`, need a distinct prefix for 2PC
   - What's unclear: Whether to use `{tpc:<order_id>}` or reuse `{saga:<order_id>}` with a `protocol` field
   - Recommendation: Use `{tpc:<order_id>}` for clean separation. The recovery scanner (Phase 12, TPC-06) can scan both prefixes.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio (session-scoped) |
| Config file | `pytest.ini` (asyncio_mode=auto, session loop scope) |
| Quick run command | `pytest tests/test_tpc.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TPC-01 | Valid 2PC state transitions accepted | unit | `pytest tests/test_tpc.py::test_tpc_valid_transitions -x` | No -- Wave 0 |
| TPC-01 | Invalid 2PC state transitions rejected | unit | `pytest tests/test_tpc.py::test_tpc_invalid_transitions_rejected -x` | No -- Wave 0 |
| TPC-01 | CAS rejects stale state (concurrent transition) | unit | `pytest tests/test_tpc.py::test_tpc_cas_rejects_stale_state -x` | No -- Wave 0 |
| TPC-01 | Duplicate TPC record creation prevented | unit | `pytest tests/test_tpc.py::test_tpc_duplicate_creation_prevented -x` | No -- Wave 0 |
| TPC-02 | Stock PREPARE reserves items atomically | unit | `pytest tests/test_tpc.py::test_stock_prepare_reserves -x` | No -- Wave 0 |
| TPC-02 | Stock PREPARE idempotent (duplicate safe) | unit | `pytest tests/test_tpc.py::test_stock_prepare_idempotent -x` | No -- Wave 0 |
| TPC-02 | Stock COMMIT finalizes (deletes hold) | unit | `pytest tests/test_tpc.py::test_stock_commit_finalizes -x` | No -- Wave 0 |
| TPC-02 | Stock ABORT releases (restores stock) | unit | `pytest tests/test_tpc.py::test_stock_abort_releases -x` | No -- Wave 0 |
| TPC-02 | Stock PREPARE fails on insufficient stock | unit | `pytest tests/test_tpc.py::test_stock_prepare_insufficient -x` | No -- Wave 0 |
| TPC-02 | Partial prepare impossible (all-or-nothing per item) | unit | `pytest tests/test_tpc.py::test_stock_prepare_atomic -x` | No -- Wave 0 |
| TPC-03 | Payment PREPARE reserves funds atomically | unit | `pytest tests/test_tpc.py::test_payment_prepare_reserves -x` | No -- Wave 0 |
| TPC-03 | Payment PREPARE idempotent (duplicate safe) | unit | `pytest tests/test_tpc.py::test_payment_prepare_idempotent -x` | No -- Wave 0 |
| TPC-03 | Payment COMMIT finalizes (deletes hold) | unit | `pytest tests/test_tpc.py::test_payment_commit_finalizes -x` | No -- Wave 0 |
| TPC-03 | Payment ABORT releases (restores credit) | unit | `pytest tests/test_tpc.py::test_payment_abort_releases -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_tpc.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_tpc.py` -- all TPC-01, TPC-02, TPC-03 tests
- [ ] conftest.py update: add `tpc_db` fixture (can reuse orchestrator_db or add separate one)

## Sources

### Primary (HIGH confidence)
- `orchestrator/saga.py` -- existing SAGA state machine pattern (Lua CAS transitions, Redis hash records)
- `stock/operations.py` -- existing Lua CAS + idempotency pattern for stock operations
- `payment/operations.py` -- existing Lua CAS + idempotency pattern for payment operations
- `tests/test_saga.py` -- existing test patterns for state machine and operations
- `.planning/REQUIREMENTS.md` -- TPC-01, TPC-02, TPC-03 requirements definitions
- `.planning/PROJECT.md` -- constraints (Redis Cluster, Lua CAS, hash tags)

### Secondary (MEDIUM confidence)
- Redis documentation on EVAL atomicity and CROSSSLOT restrictions (well-established Redis behavior)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- directly mirrors proven saga.py + operations.py patterns
- Pitfalls: HIGH -- CROSSSLOT and multi-key atomicity are well-understood Redis Cluster constraints; idempotency patterns proven in existing code

**Research date:** 2026-03-12
**Valid until:** 2026-04-12 (stable -- patterns are internal to this codebase)
