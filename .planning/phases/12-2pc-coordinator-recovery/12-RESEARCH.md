# Phase 12: 2PC Coordinator & Recovery - Research

**Researched:** 2026-03-12
**Domain:** 2PC coordinator logic, WAL-pattern crash recovery, SAGA/2PC protocol switching
**Confidence:** HIGH

## Summary

Phase 12 implements the orchestrator-side 2PC coordinator that drives the two-phase commit protocol end-to-end, adds crash recovery for 2PC transactions, and wires up an env var toggle between SAGA and 2PC transaction patterns. This builds on Phase 11's participant-side operations (prepare/commit/abort in stock/payment operations.py) and Phase 10's transport adapter (gRPC/queue switching).

The coordinator must: (1) send concurrent PREPARE requests to Stock and Payment via asyncio.gather, (2) collect votes and decide COMMIT or ABORT, (3) persist the decision to Redis BEFORE sending phase-2 messages (WAL pattern), and (4) execute phase-2 (COMMIT or ABORT all participants). The recovery scanner must be extended to handle both SAGA and 2PC records by checking the `protocol` field. The `TRANSACTION_PATTERN` env var switches between calling `run_checkout` (SAGA) vs `run_2pc_checkout` (2PC) with no other code changes.

A critical gap exists: the gRPC protos and queue consumers do NOT currently expose prepare/commit/abort RPCs. Phase 12 must add these to both transport paths (gRPC protos + servicers + client wrappers, AND queue consumer dispatch tables + queue_client wrappers), then wire them through the transport adapter so the coordinator is transport-agnostic.

**Primary recommendation:** Create `run_2pc_checkout` function in `orchestrator/grpc_server.py` (or a new `orchestrator/tpc_coordinator.py`) mirroring `run_checkout`, add 2PC transport functions (prepare_stock, commit_stock, etc.) to the transport layer, extend recovery.py to handle 2PC records, and add `TRANSACTION_PATTERN` env var routing in the gRPC servicer.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TPC-04 | Orchestrator acts as 2PC coordinator with concurrent participant prepare via asyncio.gather | Create run_2pc_checkout that sends asyncio.gather(prepare_stock_all_items, prepare_payment) concurrently, collects votes, decides commit/abort |
| TPC-05 | Coordinator persists decision to Redis before sending phase-2 messages (WAL pattern) | Use transition_tpc_state to move to COMMITTING or ABORTING BEFORE sending commit/abort to participants -- this IS the WAL (Redis hash is the durable decision record) |
| TPC-06 | Recovery scanner handles 2PC transactions using protocol field in records | Extend recover_incomplete_sagas to also scan {tpc:*} keys, check protocol field, apply 2PC-specific recovery logic (re-send commit/abort based on persisted state) |
| TPC-07 | TRANSACTION_PATTERN env var toggles between SAGA and 2PC | Read TRANSACTION_PATTERN at import time, route StartCheckout RPC to run_checkout or run_2pc_checkout accordingly |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis.asyncio | existing | Async Redis for TPC records, Lua CAS transitions | Already in use |
| asyncio | stdlib | asyncio.gather for concurrent PREPARE | Built-in, proven pattern |
| grpc.aio | existing | Async gRPC for 2PC RPCs | Already in use for SAGA |
| msgspec.json | existing | JSON serialization for queue messages | Already in use for queue transport |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest + pytest-asyncio | existing | Unit tests for coordinator and recovery | Test coordinator logic, recovery paths |

**Installation:**
No new dependencies. All libraries already installed.

## Architecture Patterns

### Recommended Project Structure
```
orchestrator/
  grpc_server.py          # MODIFY: add run_2pc_checkout, route via TRANSACTION_PATTERN
  tpc.py                  # EXISTS: 2PC state machine (Phase 11)
  recovery.py             # MODIFY: add 2PC recovery logic, rename to handle both protocols
  transport.py            # MODIFY: add 2PC transport functions (prepare/commit/abort)
  client.py               # MODIFY: add gRPC wrappers for prepare/commit/abort RPCs
  queue_client.py         # MODIFY: add queue wrappers for prepare/commit/abort commands
  app.py                  # MODIFY: call unified recovery (SAGA + 2PC)
protos/
  stock.proto             # MODIFY: add PrepareStock, CommitStock, AbortStock RPCs
  payment.proto           # MODIFY: add PreparePayment, CommitPayment, AbortPayment RPCs
stock/
  grpc_server.py          # MODIFY: add 2PC RPC handlers
  queue_consumer.py       # MODIFY: add 2PC command dispatch entries
  stock_pb2.py            # REGENERATE: after proto change
  stock_pb2_grpc.py       # REGENERATE: after proto change
payment/
  grpc_server.py          # MODIFY: add 2PC RPC handlers
  queue_consumer.py       # MODIFY: add 2PC command dispatch entries
  payment_pb2.py          # REGENERATE: after proto change
  payment_pb2_grpc.py     # REGENERATE: after proto change
tests/
  test_2pc_coordinator.py # NEW: coordinator + recovery tests
```

### Pattern 1: 2PC Coordinator Flow (run_2pc_checkout)
**What:** Orchestrator drives the full 2PC lifecycle: create record, concurrent prepare, persist decision, phase-2 commit/abort
**When to use:** When TRANSACTION_PATTERN=2pc

```python
# Source: Derived from run_checkout pattern in orchestrator/grpc_server.py
async def run_2pc_checkout(db, order_id, user_id, items, total_cost) -> dict:
    tpc_key = f"{{tpc:{order_id}}}"

    # Exactly-once: check existing TPC record
    existing = await get_tpc(db, order_id)
    if existing is not None:
        state = existing["state"]
        if state == "COMMITTED":
            return {"success": True, "error_message": ""}
        if state == "ABORTED":
            return {"success": False, "error_message": "transaction aborted"}
        return {"success": False, "error_message": "checkout already in progress"}

    # Create TPC record (state=INIT)
    created = await create_tpc_record(db, order_id, user_id, items, total_cost)
    if not created:
        # Race condition handling (same as SAGA)
        ...

    # Phase 1: INIT -> PREPARING
    await transition_tpc_state(db, tpc_key, "INIT", "PREPARING")

    # Concurrent PREPARE via asyncio.gather
    stock_futures = [
        prepare_stock(item["item_id"], item["quantity"], order_id)
        for item in items
    ]
    payment_future = prepare_payment(user_id, total_cost, order_id)

    results = await asyncio.gather(*stock_futures, payment_future, return_exceptions=True)

    # Collect votes
    all_yes = all(
        isinstance(r, dict) and r.get("success")
        for r in results
    )

    if all_yes:
        # WAL: persist COMMITTING decision BEFORE sending commits (TPC-05)
        await transition_tpc_state(db, tpc_key, "PREPARING", "COMMITTING")

        # Phase 2: send COMMIT to all participants
        commit_futures = [
            commit_stock(item["item_id"], order_id) for item in items
        ] + [commit_payment(user_id, order_id)]
        await asyncio.gather(*commit_futures)

        await transition_tpc_state(db, tpc_key, "COMMITTING", "COMMITTED")
        return {"success": True, "error_message": ""}
    else:
        # WAL: persist ABORTING decision BEFORE sending aborts (TPC-05)
        await transition_tpc_state(db, tpc_key, "PREPARING", "ABORTING")

        # Phase 2: send ABORT to all participants
        abort_futures = [
            abort_stock(item["item_id"], order_id) for item in items
        ] + [abort_payment(user_id, order_id)]
        await asyncio.gather(*abort_futures)

        await transition_tpc_state(db, tpc_key, "ABORTING", "ABORTED")
        # Extract error message from first failure
        error_msg = next(
            (r.get("error_message", "prepare failed") for r in results
             if isinstance(r, dict) and not r.get("success")),
            "prepare failed"
        )
        return {"success": False, "error_message": error_msg}
```

### Pattern 2: WAL (Write-Ahead Log) via TPC State Machine
**What:** The coordinator persists its COMMIT/ABORT decision to Redis BEFORE sending phase-2 messages
**When to use:** Always -- this is the crash safety guarantee

The TPC record in Redis IS the WAL. The key insight:
- After `transition_tpc_state(db, tpc_key, "PREPARING", "COMMITTING")`, the decision is durable
- If the coordinator crashes after this transition but before sending COMMIT messages, the recovery scanner sees state=COMMITTING and re-sends COMMITs
- If the coordinator crashes before this transition (still PREPARING), recovery ABORTs (safe default)

This is exactly the 2PC protocol's presumed-abort recovery strategy.

### Pattern 3: Transport Adapter Extension for 2PC
**What:** Add prepare/commit/abort functions to transport.py, client.py, and queue_client.py
**When to use:** Required for coordinator to call participants transport-agnostically

```python
# transport.py -- add these alongside existing SAGA functions:
if COMM_MODE == "queue":
    from queue_client import (
        # existing SAGA
        reserve_stock, release_stock, check_stock,
        charge_payment, refund_payment, check_payment,
        # NEW 2PC
        prepare_stock, commit_stock, abort_stock,
        prepare_payment, commit_payment, abort_payment,
    )
else:
    from client import (
        # existing SAGA
        reserve_stock, release_stock, check_stock,
        charge_payment, refund_payment, check_payment,
        # NEW 2PC
        prepare_stock, commit_stock, abort_stock,
        prepare_payment, commit_payment, abort_payment,
    )
```

### Pattern 4: gRPC Proto Extension for 2PC
**What:** Add PrepareStock, CommitStock, AbortStock RPCs to stock.proto (and equivalents for payment)
**When to use:** Required for COMM_MODE=grpc with TRANSACTION_PATTERN=2pc

```protobuf
// stock.proto additions:
service StockService {
  // existing
  rpc ReserveStock(ReserveStockRequest) returns (StockResponse);
  rpc ReleaseStock(ReleaseStockRequest) returns (StockResponse);
  rpc CheckStock(CheckStockRequest) returns (CheckStockResponse);
  // NEW 2PC
  rpc PrepareStock(PrepareStockRequest) returns (StockResponse);
  rpc CommitStock(CommitStockRequest) returns (StockResponse);
  rpc AbortStock(AbortStockRequest) returns (StockResponse);
}

message PrepareStockRequest {
  string item_id = 1;
  int32 quantity = 2;
  string order_id = 3;  // used as hold key identifier
}

message CommitStockRequest {
  string item_id = 1;
  string order_id = 2;
}

message AbortStockRequest {
  string item_id = 1;
  string order_id = 2;
}
```

Payment proto follows the same pattern with PreparePayment, CommitPayment, AbortPayment.

### Pattern 5: Queue Consumer Extension for 2PC
**What:** Add prepare/commit/abort to COMMAND_DISPATCH in queue consumers
**When to use:** Required for COMM_MODE=queue with TRANSACTION_PATTERN=2pc

```python
# stock/queue_consumer.py -- add to COMMAND_DISPATCH:
COMMAND_DISPATCH = {
    # existing SAGA
    "reserve_stock": lambda db, p: operations.reserve_stock(db, p["item_id"], int(p["quantity"]), p["idempotency_key"]),
    "release_stock": lambda db, p: operations.release_stock(db, p["item_id"], int(p["quantity"]), p["idempotency_key"]),
    "check_stock": lambda db, p: operations.check_stock(db, p["item_id"]),
    # NEW 2PC
    "prepare_stock": lambda db, p: operations.prepare_stock(db, p["item_id"], int(p["quantity"]), p["order_id"]),
    "commit_stock": lambda db, p: operations.commit_stock(db, p["item_id"], p["order_id"]),
    "abort_stock": lambda db, p: operations.abort_stock(db, p["item_id"], p["order_id"]),
}
```

### Pattern 6: 2PC Recovery Scanner
**What:** Extend recovery.py to scan {tpc:*} keys and apply 2PC-specific recovery
**When to use:** On orchestrator startup

```python
# Recovery logic by TPC state:
# INIT, PREPARING -> ABORT (presumed abort: no decision persisted)
# COMMITTING -> re-send COMMIT to all participants, then -> COMMITTED
# ABORTING -> re-send ABORT to all participants, then -> ABORTED
# COMMITTED, ABORTED -> terminal, skip
```

### Pattern 7: TRANSACTION_PATTERN Env Var Toggle
**What:** Read env var at import time, route checkout RPC accordingly
**When to use:** In grpc_server.py's StartCheckout handler

```python
TRANSACTION_PATTERN = os.environ.get("TRANSACTION_PATTERN", "saga")

class OrchestratorServiceServicer(...):
    async def StartCheckout(self, request, context):
        items = [{"item_id": item.item_id, "quantity": item.quantity} for item in request.items]
        if TRANSACTION_PATTERN == "2pc":
            result = await run_2pc_checkout(self.db, ...)
        else:
            result = await run_checkout(self.db, ...)
        return CheckoutResponse(...)
```

### Anti-Patterns to Avoid
- **Sending phase-2 messages before persisting decision:** Violates the WAL pattern. If coordinator crashes after sending COMMIT to Stock but before persisting, recovery cannot know whether to commit or abort Payment. Always persist COMMITTING/ABORTING state FIRST.
- **Sequential PREPARE instead of concurrent:** Defeats the purpose of 2PC's concurrent prepare phase. Use asyncio.gather for all participants simultaneously.
- **Separate recovery code paths with duplicated scanning logic:** Unify the recovery scanner to scan both {saga:*} and {tpc:*} in one pass, using the protocol field to dispatch.
- **Retry on PREPARE failure:** Unlike SAGA, in 2PC a PREPARE NO vote is final. Do NOT retry -- immediately proceed to ABORT all participants.
- **Forgetting exceptions from asyncio.gather:** With return_exceptions=True, exceptions become results. Must check isinstance(r, Exception) when collecting votes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Concurrent participant calls | Sequential await loops | asyncio.gather with return_exceptions=True | Built-in, handles exceptions cleanly |
| Decision persistence (WAL) | Custom WAL file | TPC state machine transitions in Redis | Already built in Phase 11, atomic via Lua CAS |
| Protocol routing | if/else in every function | Single env var check at entry point | Clean separation, matches COMM_MODE pattern |
| Proto code generation | Manual pb2 files | grpc_tools.protoc compiler | Correct, reproducible, avoids hand-edit errors |

**Key insight:** The TPC state machine (Phase 11) IS the WAL. Transitioning to COMMITTING/ABORTING before sending phase-2 messages provides crash recovery for free -- the recovery scanner just reads the persisted state and replays the appropriate phase-2 action.

## Common Pitfalls

### Pitfall 1: Crash between PREPARE votes and decision persistence
**What goes wrong:** Coordinator receives all YES votes but crashes before transitioning to COMMITTING. All participants have tentative holds that are never committed or aborted.
**Why it happens:** Not persisting decision atomically with vote collection.
**How to avoid:** The transition_tpc_state call to COMMITTING must happen BEFORE any COMMIT messages are sent. Recovery scanner sees PREPARING state and ABORTs (presumed abort) -- this is safe because participants' PREPARE is reversible via ABORT. Hold keys have 7-day TTL as ultimate safety net.
**Warning signs:** Stale hold keys in Redis after coordinator restart.

### Pitfall 2: asyncio.gather exception handling
**What goes wrong:** One participant throws an exception (network error, timeout), but asyncio.gather without return_exceptions=True propagates only the first exception and swallows the rest.
**Why it happens:** Default asyncio.gather behavior cancels remaining tasks on first exception.
**How to avoid:** Use `return_exceptions=True` so all results (including exceptions) are collected. Then check each result: if any is an Exception or has success=False, proceed to ABORT all.
**Warning signs:** Partial prepares with no corresponding abort.

### Pitfall 3: Proto regeneration not applied to all services
**What goes wrong:** Updated .proto file but forgot to regenerate pb2/pb2_grpc for all copies (stock, payment, orchestrator each have their own copies).
**Why it happens:** pb2 files are copied per-service, not shared.
**How to avoid:** Regenerate protos and copy to all service directories that use them. Verify by importing and checking for new request/response classes.
**Warning signs:** ImportError or AttributeError at runtime for new RPC methods.

### Pitfall 4: Recovery scanner re-enters running transactions
**What goes wrong:** Recovery scanner picks up a 2PC transaction that is still actively being coordinated (not crashed, just slow).
**Why it happens:** Staleness threshold too low or not checked.
**How to avoid:** Reuse the same STALENESS_THRESHOLD_SECONDS pattern from SAGA recovery. Only recover transactions older than the threshold (default 300s).
**Warning signs:** Double-commit or double-abort causing idempotency key conflicts.

### Pitfall 5: Queue client prepare/commit/abort signature mismatch
**What goes wrong:** Queue client wrapper for prepare_stock passes idempotency_key instead of order_id, or misses a parameter.
**Why it happens:** SAGA functions use (item_id, quantity, idempotency_key) but 2PC uses (item_id, quantity, order_id) -- different parameter names.
**How to avoid:** Match the exact signatures from stock/operations.py: prepare_stock(db, item_id, quantity, order_id), commit_stock(db, item_id, order_id), abort_stock(db, item_id, order_id). Queue payloads must use "order_id" not "idempotency_key".
**Warning signs:** KeyError in queue consumer dispatch.

### Pitfall 6: Transport adapter missing 2PC exports
**What goes wrong:** Coordinator imports prepare_stock from transport but transport.py doesn't export it.
**Why it happens:** transport.py __all__ list not updated.
**How to avoid:** Add all 6 new functions to both the import block and __all__ list in transport.py.
**Warning signs:** ImportError when coordinator tries to use 2PC with queue transport.

## Code Examples

### gRPC Client Wrappers for 2PC (client.py additions)
```python
# Source: Follows existing reserve_stock/charge_payment pattern in client.py
@stock_breaker
async def prepare_stock(item_id: str, quantity: int, order_id: str) -> dict:
    resp = await _stock_stub.PrepareStock(
        PrepareStockRequest(item_id=item_id, quantity=quantity, order_id=order_id),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

@stock_breaker
async def commit_stock(item_id: str, order_id: str) -> dict:
    resp = await _stock_stub.CommitStock(
        CommitStockRequest(item_id=item_id, order_id=order_id),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

@stock_breaker
async def abort_stock(item_id: str, order_id: str) -> dict:
    resp = await _stock_stub.AbortStock(
        AbortStockRequest(item_id=item_id, order_id=order_id),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}
```

### Queue Client Wrappers for 2PC (queue_client.py additions)
```python
# Source: Follows existing reserve_stock/charge_payment pattern in queue_client.py
async def prepare_stock(item_id: str, quantity: int, order_id: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "prepare_stock",
                              {"item_id": item_id, "quantity": quantity,
                               "order_id": order_id})

async def commit_stock(item_id: str, order_id: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "commit_stock",
                              {"item_id": item_id, "order_id": order_id})

async def abort_stock(item_id: str, order_id: str) -> dict:
    return await send_command(STOCK_COMMAND_STREAM, "abort_stock",
                              {"item_id": item_id, "order_id": order_id})
```

### 2PC Recovery Logic
```python
# Source: Derived from recovery.py resume_saga pattern
TPC_NON_TERMINAL_STATES = {"INIT", "PREPARING", "COMMITTING", "ABORTING"}

async def resume_tpc(db, tpc: dict) -> None:
    order_id = tpc["order_id"]
    tpc_key = f"{{tpc:{order_id}}}"
    state = tpc["state"]
    items = json.loads(tpc["items_json"])
    user_id = tpc["user_id"]
    total_cost = int(tpc["total_cost"])

    if state in ("INIT", "PREPARING"):
        # Presumed abort: no decision was persisted
        await transition_tpc_state(db, tpc_key, state, "ABORTING")
        # Send ABORT to all participants (idempotent, safe even if never prepared)
        abort_futures = [
            abort_stock(item["item_id"], order_id) for item in items
        ] + [abort_payment(user_id, order_id)]
        await asyncio.gather(*abort_futures, return_exceptions=True)
        await transition_tpc_state(db, tpc_key, "ABORTING", "ABORTED")

    elif state == "COMMITTING":
        # Decision was COMMIT: re-send COMMITs to all participants
        commit_futures = [
            commit_stock(item["item_id"], order_id) for item in items
        ] + [commit_payment(user_id, order_id)]
        await asyncio.gather(*commit_futures, return_exceptions=True)
        await transition_tpc_state(db, tpc_key, "COMMITTING", "COMMITTED")

    elif state == "ABORTING":
        # Decision was ABORT: re-send ABORTs to all participants
        abort_futures = [
            abort_stock(item["item_id"], order_id) for item in items
        ] + [abort_payment(user_id, order_id)]
        await asyncio.gather(*abort_futures, return_exceptions=True)
        await transition_tpc_state(db, tpc_key, "ABORTING", "ABORTED")
```

### Unified Recovery Scanner
```python
# Source: Extends existing recover_incomplete_sagas in recovery.py
async def recover_all(db) -> None:
    """Recover both SAGA and 2PC transactions on startup."""
    await recover_incomplete_sagas(db)  # existing SAGA recovery
    await recover_incomplete_tpc(db)    # new 2PC recovery

async def recover_incomplete_tpc(db) -> None:
    """Scan Redis for incomplete TPC records and drive to terminal state."""
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="{tpc:*", count=100):
        try:
            raw = await db.hgetall(key)
        except Exception:
            continue
        if not raw:
            continue
        tpc = {k.decode(): v.decode() for k, v in raw.items()}
        state = tpc.get("state", "")

        if state not in TPC_NON_TERMINAL_STATES:
            continue

        updated_at = int(tpc.get("updated_at", "0"))
        if now - updated_at < STALENESS_THRESHOLD_SECONDS:
            skipped += 1
            continue

        await resume_tpc(db, tpc)
        recovered += 1

    logging.info("TPC recovery complete: %d recovered, %d skipped", recovered, skipped)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SAGA-only checkout | SAGA + 2PC switchable via env var | v2.0 (this phase) | Coordinator routes to different transaction implementations |
| SAGA-only recovery scanner | Unified recovery for SAGA + 2PC | v2.0 (this phase) | Recovery scans both {saga:*} and {tpc:*} key prefixes |
| Transport adapter (SAGA only) | Transport adapter with 2PC functions | v2.0 (this phase) | 6 new functions in transport layer |
| gRPC protos (SAGA only) | gRPC protos with 2PC RPCs | v2.0 (this phase) | 6 new RPCs across stock.proto and payment.proto |

## Open Questions

1. **Where to put run_2pc_checkout**
   - What we know: run_checkout is in grpc_server.py, which also contains the gRPC servicer
   - What's unclear: Whether to add run_2pc_checkout to grpc_server.py or create a separate tpc_coordinator.py
   - Recommendation: Add to grpc_server.py for consistency with run_checkout. The file is already the "checkout logic" file, not just gRPC plumbing. Alternatively, could extract both to a coordinator.py, but that's more refactoring than necessary.

2. **Phase-2 retry strategy for commit/abort**
   - What we know: SAGA uses retry_forever for compensation. 2PC commit/abort should also retry until success since the decision is already persisted.
   - What's unclear: Whether to reuse retry_forever or use asyncio.gather with its own retry wrapper
   - Recommendation: Reuse retry_forever for individual commit/abort calls during recovery. For the happy path, a single attempt with error logging is sufficient (recovery handles retries on restart).

3. **PREPARING state recovery -- transition through ABORTING**
   - What we know: TPC_VALID_TRANSITIONS only allows PREPARING -> ABORTING (not PREPARING -> ABORTED directly)
   - What's unclear: Whether the recovery scanner should transition PREPARING -> ABORTING -> ABORTED in two steps
   - Recommendation: Yes, two transitions to respect the state machine. First transition to ABORTING (which is valid from PREPARING), send aborts, then transition to ABORTED. For INIT state, transition INIT -> PREPARING first, then PREPARING -> ABORTING -> ABORTED.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio (session-scoped) |
| Config file | `pytest.ini` (asyncio_mode=auto, session loop scope) |
| Quick run command | `pytest tests/test_2pc_coordinator.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TPC-04 | Concurrent PREPARE via asyncio.gather, all YES -> COMMIT | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_all_prepare_yes_commits -x` | No -- Wave 0 |
| TPC-04 | Any PREPARE NO -> ABORT all | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_prepare_no_aborts -x` | No -- Wave 0 |
| TPC-04 | PREPARE exception -> ABORT all | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_prepare_exception_aborts -x` | No -- Wave 0 |
| TPC-04 | Exactly-once: existing COMMITTED record returns success | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_exactly_once -x` | No -- Wave 0 |
| TPC-05 | COMMITTING state persisted before COMMIT messages sent | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_wal_commit_persisted -x` | No -- Wave 0 |
| TPC-05 | ABORTING state persisted before ABORT messages sent | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_wal_abort_persisted -x` | No -- Wave 0 |
| TPC-06 | Recovery: PREPARING state -> ABORT all participants | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_preparing_aborts -x` | No -- Wave 0 |
| TPC-06 | Recovery: COMMITTING state -> COMMIT all participants | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_committing_commits -x` | No -- Wave 0 |
| TPC-06 | Recovery: ABORTING state -> ABORT all participants | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_aborting_aborts -x` | No -- Wave 0 |
| TPC-06 | Recovery skips SAGA records (protocol field check) | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_skips_saga -x` | No -- Wave 0 |
| TPC-07 | TRANSACTION_PATTERN=saga uses SAGA path | unit | `pytest tests/test_2pc_coordinator.py::test_pattern_toggle_saga -x` | No -- Wave 0 |
| TPC-07 | TRANSACTION_PATTERN=2pc uses 2PC path | unit | `pytest tests/test_2pc_coordinator.py::test_pattern_toggle_2pc -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_2pc_coordinator.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_2pc_coordinator.py` -- all TPC-04, TPC-05, TPC-06, TPC-07 tests
- [ ] Proto regeneration for stock.proto and payment.proto (add 2PC RPCs)

## Sources

### Primary (HIGH confidence)
- `orchestrator/grpc_server.py` -- existing run_checkout pattern (SAGA coordinator)
- `orchestrator/tpc.py` -- 2PC state machine (Phase 11, TPC-01)
- `orchestrator/recovery.py` -- existing SAGA recovery scanner pattern
- `orchestrator/transport.py` -- transport adapter pattern
- `orchestrator/client.py` -- gRPC client wrapper pattern
- `orchestrator/queue_client.py` -- queue client wrapper pattern
- `stock/operations.py` -- prepare_stock/commit_stock/abort_stock implementations (Phase 11)
- `payment/operations.py` -- prepare_payment/commit_payment/abort_payment implementations (Phase 11)
- `stock/queue_consumer.py` -- queue dispatch pattern
- `protos/stock.proto`, `protos/payment.proto` -- current proto definitions
- `.planning/REQUIREMENTS.md` -- TPC-04, TPC-05, TPC-06, TPC-07 definitions

### Secondary (MEDIUM confidence)
- 2PC protocol specification (textbook): presumed-abort recovery, WAL before phase-2 messages
- `.planning/phases/11-2pc-state-machine-participants/11-RESEARCH.md` -- Phase 11 design decisions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use, no new dependencies
- Architecture: HIGH -- directly extends proven patterns (run_checkout, transport adapter, recovery scanner)
- Pitfalls: HIGH -- 2PC protocol well understood; crash recovery semantics are textbook; codebase patterns proven
- Transport gap: HIGH -- verified by reading proto files and queue consumers that 2PC RPCs/commands do not yet exist

**Research date:** 2026-03-12
**Valid until:** 2026-04-12 (stable -- patterns are internal to this codebase)
