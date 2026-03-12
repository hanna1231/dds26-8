# Phase 8: Business Logic Extraction - Research

**Researched:** 2026-03-12
**Domain:** Python refactoring -- extract business logic from gRPC servicers into transport-agnostic modules
**Confidence:** HIGH

## Summary

Phase 8 is a pure refactoring task: move all business logic (Lua scripts, Redis calls, idempotency handling, CAS loops) out of `grpc_server.py` in both Stock and Payment services into new `operations.py` modules. The gRPC servicers become thin adapters that translate protobuf request/response objects to/from plain Python arguments and return values.

This is the foundational phase for v2.0. Both the queue consumers (Phase 9) and 2PC participants (Phase 11) need to call the same business logic without depending on gRPC types. The extraction must be behavior-preserving -- all 37 existing integration tests must pass unchanged.

The codebase is small and well-structured. Stock `grpc_server.py` is 201 lines with 3 RPC methods and 2 Lua scripts. Payment `grpc_server.py` is 186 lines with 3 RPC methods and 2 Lua scripts. The patterns are nearly identical between services, making this a straightforward mechanical extraction.

**Primary recommendation:** Create `stock/operations.py` and `payment/operations.py` that accept a Redis `db` handle and plain Python arguments (strings, ints), return plain dicts, and contain all Lua scripts and Redis logic. Servicers become one-liner delegations.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| BLE-01 | Stock service business logic extracted from gRPC servicers into shared operations module | Operations module pattern below; 3 functions to extract: `reserve_stock`, `release_stock`, `check_stock` |
| BLE-02 | Payment service business logic extracted from gRPC servicers into shared operations module | Same pattern; 3 functions to extract: `charge_payment`, `refund_payment`, `check_payment` |
</phase_requirements>

## Standard Stack

### Core

No new libraries needed. This phase uses only what is already in the project.

| Library | Version | Purpose | Already In Project |
|---------|---------|---------|-------------------|
| redis.asyncio | existing | Redis Cluster client (moved into operations.py) | Yes |
| msgspec | existing | msgpack encode/decode for StockValue/UserValue | Yes |
| json | stdlib | Idempotency result serialization | Yes |

### Supporting

No additional libraries required. This is a pure code reorganization.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain dicts as return type | dataclass/msgspec Struct | Dicts match what orchestrator `client.py` already returns -- consistency wins |
| ABC/interface for operations | Plain module functions | No interface needed yet; queue consumers and 2PC will call same functions directly |
| Dependency injection class | Module-level functions with `db` param | Functions are simpler; `db` is already passed to servicer constructor |

## Architecture Patterns

### Recommended Project Structure

```
stock/
  app.py              # HTTP routes (unchanged)
  grpc_server.py       # Thin gRPC adapter (delegates to operations)
  operations.py        # NEW: all business logic, Lua scripts, Redis calls
  stock_pb2.py         # (unchanged)
  stock_pb2_grpc.py    # (unchanged)

payment/
  app.py              # HTTP routes (unchanged)
  grpc_server.py       # Thin gRPC adapter (delegates to operations)
  operations.py        # NEW: all business logic, Lua scripts, Redis calls
  payment_pb2.py       # (unchanged)
  payment_pb2_grpc.py  # (unchanged)
```

### Pattern 1: Operations Module with Plain Python Interface

**What:** Each `operations.py` exports async functions that take a Redis db handle and primitive Python types (str, int), return plain dicts.

**When to use:** Always -- this is the only pattern for this phase.

**Example (stock/operations.py):**
```python
import json
from msgspec import msgpack, Struct


class StockValue(Struct):
    stock: int
    price: int


# Lua scripts moved here (module-level constants)
IDEMPOTENCY_ACQUIRE_LUA = """..."""
RESERVE_STOCK_ATOMIC_LUA = """..."""


async def reserve_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    """Reserve stock for an item. Returns {"success": bool, "error_message": str}."""
    item_key = f"{{item:{item_id}}}"
    ikey = f"{{item:{item_id}}}:idempotency:{idempotency_key}"
    # ... CAS loop logic moved from StockServiceServicer.ReserveStock ...
    # Returns dict instead of StockResponse protobuf


async def release_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    """Release previously reserved stock. Returns {"success": bool, "error_message": str}."""
    # ... logic moved from StockServiceServicer.ReleaseStock ...


async def check_stock(db, item_id: str) -> dict:
    """Check stock and price for an item. Returns {"success": bool, "error_message": str, "stock": int, "price": int}."""
    # ... logic moved from StockServiceServicer.CheckStock ...
```

### Pattern 2: Thin gRPC Servicer Adapter

**What:** After extraction, each gRPC method is a 2-4 line adapter: unpack protobuf fields, call operations function, pack result into protobuf response.

**Example (stock/grpc_server.py after extraction):**
```python
from stock_pb2 import StockResponse, CheckStockResponse
from stock_pb2_grpc import StockServiceServicer as StockServiceServicerBase, add_StockServiceServicer_to_server
import operations


class StockServiceServicer(StockServiceServicerBase):
    def __init__(self, db):
        self.db = db

    async def ReserveStock(self, request, context):
        result = await operations.reserve_stock(
            self.db, request.item_id, request.quantity, request.idempotency_key
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def ReleaseStock(self, request, context):
        result = await operations.release_stock(
            self.db, request.item_id, request.quantity, request.idempotency_key
        )
        return StockResponse(success=result["success"], error_message=result["error_message"])

    async def CheckStock(self, request, context):
        result = await operations.check_stock(self.db, request.item_id)
        return CheckStockResponse(
            success=result["success"], error_message=result["error_message"],
            stock=result["stock"], price=result["price"]
        )
```

### Pattern 3: Return Type Convention

**What:** Operations functions return dicts that match the structure the orchestrator's `client.py` already uses. This ensures consistency across gRPC and future queue consumers.

**Convention:**
- Mutation operations return `{"success": bool, "error_message": str}`
- Query operations return `{"success": bool, "error_message": str, ...extra_fields}`
- This matches `orchestrator/client.py` return format exactly

### Anti-Patterns to Avoid

- **Importing protobuf types in operations.py:** The entire point is transport independence. Operations must not import `*_pb2` modules.
- **Passing gRPC `context` to operations:** Context is gRPC-specific. Operations take only primitive types.
- **Creating a base class or ABC:** Premature abstraction. Phase 9 queue consumers will just import and call the same functions.
- **Moving StockValue/UserValue Struct to a shared module:** Keep them in `operations.py` -- they are internal implementation details of each service's data layer, not cross-service types.
- **Changing the `app.py` HTTP routes:** They are out of scope. They use the global `db` directly and don't go through gRPC. Leave them alone for now.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Return type normalization | Custom result classes | Plain dicts | Matches existing `client.py` convention; no new types to maintain |
| Idempotency logic | New idempotency framework | Keep existing Lua scripts as-is | They work, are battle-tested, moving them is sufficient |
| CAS retry loops | New retry abstraction | Keep existing `while True` CAS pattern | Simple, proven, only used in 2 places |

**Key insight:** This phase is strictly about moving code, not improving it. Resist the urge to refactor the Lua scripts or CAS logic -- that introduces behavior risk.

## Common Pitfalls

### Pitfall 1: Breaking the CAS Loop Return Semantics
**What goes wrong:** Operations function returns different values than what the servicer used to return internally, causing subtle behavior changes.
**Why it happens:** The servicer currently returns protobuf `StockResponse` objects from within the CAS loop. When converting to dict returns, it's easy to miss a code path (e.g., the idempotency replay path).
**How to avoid:** Map every `return StockResponse(...)` in the original to a corresponding `return {"success": ..., "error_message": ...}` in operations.py. Count the return statements -- they must match 1:1.
**Warning signs:** Integration tests fail with unexpected error messages or missing fields.

### Pitfall 2: StockValue/UserValue Struct Duplication
**What goes wrong:** Both `app.py` and `operations.py` need the `StockValue`/`UserValue` Struct. If defined in both files, they can drift.
**Why it happens:** `app.py` already defines `StockValue` at module level for the HTTP routes. `operations.py` also needs it.
**How to avoid:** Define the Struct in `operations.py` and import it in both `grpc_server.py` and `app.py`. This makes operations.py the single source of truth. Update `app.py` to `from operations import StockValue`.
**Warning signs:** Deserialization errors due to mismatched Struct definitions.

### Pitfall 3: Test conftest.py Import Paths
**What goes wrong:** Tests import `StockServiceServicer` from `grpc_server` module. If the servicer's behavior changes even slightly (e.g., different import structure), tests break.
**Why it happens:** `conftest.py` uses `sys.path` manipulation to import `grpc_server` as a module. It constructs `StockServiceServicer(redis_db)` directly.
**How to avoid:** The servicer constructor signature stays the same (`__init__(self, db)`). The servicer still lives in `grpc_server.py`. The internal delegation to `operations.py` is invisible to tests. Verify that `grpc_server.py` can still be imported standalone (its `import operations` must resolve when `stock/` is on sys.path).
**Warning signs:** `ModuleNotFoundError: No module named 'operations'` in tests.

### Pitfall 4: Forgetting the `context` Parameter
**What goes wrong:** The gRPC servicer methods receive `(self, request, context)`. The `context` parameter is not used in any current method, but it must still be accepted.
**Why it happens:** When extracting, you might accidentally pass `context` to operations or forget it in the servicer signature.
**How to avoid:** Operations functions never accept `context`. Servicer methods keep `context` in their signature but don't forward it.

### Pitfall 5: Redis Key Hash Tag Formatting
**What goes wrong:** The item/user key format uses Redis Cluster hash tags: `{item:UUID}` and `{user:UUID}`. The curly braces must be preserved exactly.
**Why it happens:** Python f-string `f"{{item:{item_id}}}"` uses double-braces for literal braces. Easy to get wrong when moving code.
**How to avoid:** Copy the key formatting strings exactly. The idempotency key format `{item:UUID}:idempotency:KEY` must also be preserved exactly.

## Code Examples

### Stock operations.py -- Complete Function Signatures

```python
# stock/operations.py

async def reserve_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    """Atomically reserve stock using CAS loop with Lua script.

    Returns: {"success": bool, "error_message": str}
    """

async def release_stock(db, item_id: str, quantity: int, idempotency_key: str) -> dict:
    """Release previously reserved stock with idempotency.

    Returns: {"success": bool, "error_message": str}
    """

async def check_stock(db, item_id: str) -> dict:
    """Read current stock and price for an item.

    Returns: {"success": bool, "error_message": str, "stock": int, "price": int}
    """
```

### Payment operations.py -- Complete Function Signatures

```python
# payment/operations.py

async def charge_payment(db, user_id: str, amount: int, idempotency_key: str) -> dict:
    """Atomically charge user credit using CAS loop with Lua script.

    Returns: {"success": bool, "error_message": str}
    """

async def refund_payment(db, user_id: str, amount: int, idempotency_key: str) -> dict:
    """Refund credit to user with idempotency.

    Returns: {"success": bool, "error_message": str}
    """

async def check_payment(db, user_id: str) -> dict:
    """Read current credit for a user.

    Returns: {"success": bool, "error_message": str, "credit": int}
    """
```

### Servicer After Extraction -- Verification Checklist

After extraction, each `grpc_server.py` should have:
- Zero Lua script strings (moved to operations.py)
- Zero `redis.call` or `db.eval` or `db.get` or `db.set` calls (moved to operations.py)
- Zero `msgpack.encode/decode` calls (moved to operations.py)
- Zero `json.dumps/loads` calls (moved to operations.py)
- No `StockValue`/`UserValue` Struct definition (moved to operations.py)
- Each RPC method is 3-5 lines: unpack request, call operations, pack response

## State of the Art

This phase uses no external libraries and introduces no new patterns. It is a mechanical code reorganization following standard Python module extraction.

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| All logic in gRPC servicer | Extract to operations module | Phase 8 (now) | Enables queue consumers and 2PC participants to reuse logic |

## Open Questions

1. **Should `app.py` HTTP routes also delegate to `operations.py`?**
   - What we know: `app.py` has its own simpler logic for HTTP routes (create, find, add/subtract stock). These do NOT use idempotency or Lua scripts.
   - What's unclear: Whether to refactor these now or defer.
   - Recommendation: OUT OF SCOPE for this phase. The HTTP routes use different patterns (no idempotency, no CAS) and are not needed by queue consumers or 2PC. Refactoring them adds risk with no benefit for Phase 8 goals. Only update `app.py` imports if StockValue/UserValue moves to operations.py.

2. **Should the `db` parameter be the first argument or use a different pattern?**
   - What we know: Passing `db` as first arg to every function is simple and explicit.
   - What's unclear: Whether a class-based approach would be cleaner.
   - Recommendation: Use `db` as first argument. It mirrors how the servicer currently works (`self.db`). A class would add unnecessary abstraction at this stage. Queue consumers in Phase 9 will also have a `db` handle to pass.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (existing) |
| Config file | `tests/conftest.py` (session-scoped fixtures) |
| Quick run command | `pytest tests/test_grpc_integration.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BLE-01 | Stock gRPC servicer delegates to operations.py | integration | `pytest tests/test_grpc_integration.py -x -q` | Yes (existing tests validate behavior preservation) |
| BLE-02 | Payment gRPC servicer delegates to operations.py | integration | `pytest tests/test_grpc_integration.py -x -q` | Yes (existing tests validate behavior preservation) |
| BLE-01 | No Lua/Redis calls in stock servicer | structural | `grep -c "db\.\|eval\|redis" stock/grpc_server.py` returns 0 (manual check) | N/A -- structural verification |
| BLE-02 | No Lua/Redis calls in payment servicer | structural | `grep -c "db\.\|eval\|redis" payment/grpc_server.py` returns 0 (manual check) | N/A -- structural verification |

### Sampling Rate
- **Per task commit:** `pytest tests/test_grpc_integration.py -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green + structural grep verification on both servicers

### Wave 0 Gaps
None -- existing test infrastructure covers all phase requirements. The existing integration tests exercise all 6 RPC methods (ReserveStock, ReleaseStock, CheckStock, ChargePayment, RefundPayment, CheckPayment) through the gRPC server, which is exactly what this refactoring must preserve.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of `stock/grpc_server.py` (201 lines), `payment/grpc_server.py` (186 lines)
- Direct codebase analysis of `stock/app.py`, `payment/app.py` (HTTP routes)
- Direct codebase analysis of `orchestrator/client.py` (return value conventions)
- Direct codebase analysis of `tests/conftest.py` (test infrastructure, import patterns)
- Proto definitions in `protos/stock.proto`, `protos/payment.proto`

### Secondary (MEDIUM confidence)
- None needed -- this is purely internal refactoring

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new libraries, pure reorganization of existing code
- Architecture: HIGH - operations module pattern is straightforward Python, directly informed by codebase analysis
- Pitfalls: HIGH - identified from actual import patterns in conftest.py and key formatting in existing code

**Research date:** 2026-03-12
**Valid until:** No expiry -- this is project-specific structural analysis, not library research
