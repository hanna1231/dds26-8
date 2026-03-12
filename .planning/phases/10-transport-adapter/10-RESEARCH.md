# Phase 10: Transport Adapter - Research

**Researched:** 2026-03-12
**Domain:** Transport abstraction layer, env-var-driven module selection, Python module aliasing
**Confidence:** HIGH

## Summary

Phase 10 creates a transport adapter that allows the orchestrator to transparently switch between gRPC (`client.py`) and queue-based (`queue_client.py`) communication via a single `COMM_MODE` environment variable. The groundwork is already perfectly laid: Phase 9 built `queue_client.py` with **identical function signatures** to `client.py` -- same function names (`reserve_stock`, `release_stock`, `check_stock`, `charge_payment`, `refund_payment`, `check_payment`), same parameter signatures, same return types (plain dicts with `success` and `error_message` keys).

The implementation is straightforward: create a `transport.py` module that reads `COMM_MODE` and conditionally imports/re-exports from either `client` (gRPC) or `queue_client` (queue). Then update the three files that currently import from `client` directly (`grpc_server.py`, `recovery.py`, `app.py`) to import from `transport` instead. The `app.py` startup/shutdown also needs conditional initialization -- gRPC mode calls `init_grpc_clients()`/`close_grpc_clients()`, queue mode calls `init_queue_client()`/`close_queue_client()` plus starts the reply listener.

**Primary recommendation:** Create `orchestrator/transport.py` as a thin re-export module using conditional import based on `os.environ.get("COMM_MODE", "grpc")`. Update callers to import from `transport` instead of `client`. Default to `grpc` for backward compatibility.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MQC-04 | Transport adapter abstraction enabling gRPC/queue swap transparently | `client.py` and `queue_client.py` already share identical signatures; `transport.py` conditionally re-exports the right set of functions based on env var |
| MQC-05 | COMM_MODE env var toggles between gRPC and queue communication | `os.environ.get("COMM_MODE", "grpc")` read at module import time; "grpc" uses `client.py`, "queue" uses `queue_client.py` |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| os | stdlib | Read COMM_MODE env var | Standard Python approach for env-var config |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| logging | stdlib | Log which transport mode is active at startup | Diagnostic visibility |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Module-level conditional import | ABC/Protocol class with subclasses | Over-engineered for 6 functions with identical signatures; module aliasing is simpler and more Pythonic |
| Read env var at import time | Dependency injection | DI adds complexity; env var at import time matches the project's existing pattern (see `client.py` reading `STOCK_GRPC_ADDR` at module level) |

**Installation:**
```bash
# No new packages needed
```

## Architecture Patterns

### Recommended Project Structure
```
orchestrator/
  transport.py        # NEW: reads COMM_MODE, re-exports functions from client or queue_client
  client.py           # UNCHANGED: gRPC transport implementation
  queue_client.py     # UNCHANGED: queue transport implementation
  reply_listener.py   # UNCHANGED: queue reply listener (only started in queue mode)
  grpc_server.py      # MODIFIED: import from transport instead of client
  recovery.py         # MODIFIED: import from transport instead of client
  app.py              # MODIFIED: import from transport, conditional init/shutdown
```

### Pattern 1: Conditional Re-export Module
**What:** `transport.py` reads `COMM_MODE` env var and re-exports the appropriate functions. Callers import from `transport` and are unaware of which backend is active.
**When to use:** Always -- this is the core pattern for MQC-04/MQC-05.
**Example:**
```python
# orchestrator/transport.py
import os
import logging

COMM_MODE = os.environ.get("COMM_MODE", "grpc")
logging.info("Transport mode: %s", COMM_MODE)

if COMM_MODE == "queue":
    from queue_client import (
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
        init_queue_client as _init_transport,
        close_queue_client as _close_transport,
    )
else:
    from client import (
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
        init_grpc_clients as _init_transport_async,
        close_grpc_clients as _close_transport_async,
    )
```

### Pattern 2: Conditional Startup/Shutdown in app.py
**What:** `app.py` startup calls different init functions depending on transport mode. Queue mode needs reply_listener background task; gRPC mode does not.
**When to use:** app.py before_serving/after_serving hooks.
**Key difference:**
- gRPC mode: `await init_grpc_clients()` (async), no reply listener needed
- Queue mode: `init_queue_client(queue_db)` (sync), must start reply listener as background task, must set up reply consumer group
**Example:**
```python
# In app.py startup:
from transport import COMM_MODE

if COMM_MODE == "queue":
    queue_db = ...  # create queue Redis connection
    init_queue_client(queue_db)
    await setup_reply_consumer_group(queue_db)
    app.add_background_task(reply_listener, queue_db, stop_event)
else:
    await init_grpc_clients()
```

### Pattern 3: CircuitBreaker Handling Across Modes
**What:** gRPC mode uses `CircuitBreakerError` from `circuitbreaker` package (raised by decorated functions in `client.py`). Queue mode does not use circuit breakers -- timeout errors come back as `{"success": False, "error_message": "queue timeout"}` dict values, not exceptions.
**When to use:** `grpc_server.py` and `recovery.py` catch `CircuitBreakerError`. This still works in queue mode because `CircuitBreakerError` will simply never be raised -- the except clause is harmless.
**Key insight:** No changes needed in error handling. The `CircuitBreakerError` catch blocks in `grpc_server.py` and `recovery.py` are safe no-ops in queue mode.

### Anti-Patterns to Avoid
- **ABC/Protocol classes for transport:** Over-engineered. The two modules already share identical signatures. A thin re-export module is sufficient.
- **Runtime switching without restart:** Out of scope per REQUIREMENTS.md (OPS-03 is a future requirement). COMM_MODE is read once at import time.
- **Importing both transports unconditionally:** Would cause import errors if gRPC deps aren't available or queue_db isn't configured. Only import the selected transport.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Transport abstraction | Class hierarchy with ABC | Module-level conditional import + re-export | Both modules already have identical function signatures; no polymorphism needed |
| Init/close lifecycle | Custom lifecycle manager | Conditional blocks in existing app.py before_serving/after_serving | Matches existing Quart patterns already in use |

**Key insight:** The existing code already did the hard work. `queue_client.py` was built as a "drop-in replacement for client.py" (per its docstring). The adapter is just plumbing.

## Common Pitfalls

### Pitfall 1: Circular Import
**What goes wrong:** `transport.py` imports from `client.py`, which imports from `circuit.py`, which imports `grpc.aio`. If gRPC is not installed, queue mode fails at import time.
**Why it happens:** Python evaluates all imports at module load time.
**How to avoid:** Since gRPC IS installed in this project (it's the v1.0 transport), this is not an issue. Both `client.py` and `queue_client.py` will always be importable. The conditional import in `transport.py` only imports from one, but both CAN be imported.
**Warning signs:** ImportError on startup.

### Pitfall 2: Forgetting to Update recovery.py
**What goes wrong:** `recovery.py` imports `reserve_stock`, `charge_payment`, `CircuitBreakerError` directly from `client`. If not updated, recovery always uses gRPC regardless of COMM_MODE.
**Why it happens:** `recovery.py` is not on the main checkout path and easy to forget.
**How to avoid:** Search for ALL `from client import` statements in the orchestrator directory. There are exactly three files: `grpc_server.py`, `recovery.py`, `app.py`.
**Warning signs:** Queue mode tests pass for checkout but fail on recovery.

### Pitfall 3: Queue Mode Missing Reply Listener
**What goes wrong:** Queue commands hang (timeout after 5s) because the reply listener background task was never started.
**Why it happens:** `app.py` currently does not start the reply listener. Queue mode init must explicitly start it.
**How to avoid:** Queue mode startup must: (1) create queue_db connection, (2) call `init_queue_client(queue_db)`, (3) call `setup_reply_consumer_group(queue_db)`, (4) start `reply_listener(queue_db, stop_event)` as background task.
**Warning signs:** All queue commands return `{"success": False, "error_message": "queue timeout"}`.

### Pitfall 4: Init Function Signature Mismatch
**What goes wrong:** `init_grpc_clients()` is async and takes optional addr params. `init_queue_client()` is sync and takes a queue_db param. They cannot be unified behind a single `init_transport()` function trivially.
**Why it happens:** Different transports need different setup resources.
**How to avoid:** Don't try to unify init/close into transport.py. Keep init/close logic in app.py with conditional blocks. Only the six domain functions (reserve/release/check stock, charge/refund/check payment) go through transport.py.
**Warning signs:** Trying to make `transport.py` handle init leads to passing Redis connections through env vars or other hacks.

### Pitfall 5: Tests Importing from client Directly
**What goes wrong:** Existing tests (`conftest.py`, `test_grpc_integration.py`) import `from client import init_grpc_clients`. These should NOT be changed -- they test gRPC mode specifically.
**Why it happens:** Over-zealous refactoring.
**How to avoid:** Existing gRPC tests keep their direct `client` imports. New transport adapter tests should test both modes by setting `COMM_MODE` env var and importing `transport`.
**Warning signs:** Breaking existing passing tests.

## Code Examples

### Transport Module (transport.py)
```python
# orchestrator/transport.py
"""
Transport adapter -- re-exports domain service client functions from either
the gRPC client (client.py) or queue client (queue_client.py) based on
COMM_MODE environment variable.

COMM_MODE=grpc (default): uses gRPC transport
COMM_MODE=queue: uses Redis Streams transport
"""
import os
import logging

COMM_MODE = os.environ.get("COMM_MODE", "grpc")
logging.info("Transport adapter: COMM_MODE=%s", COMM_MODE)

if COMM_MODE == "queue":
    from queue_client import (
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
    )
else:
    from client import (
        reserve_stock,
        release_stock,
        check_stock,
        charge_payment,
        refund_payment,
        check_payment,
    )

# Re-export for callers
__all__ = [
    "COMM_MODE",
    "reserve_stock",
    "release_stock",
    "check_stock",
    "charge_payment",
    "refund_payment",
    "check_payment",
]
```

### Updated grpc_server.py Import
```python
# BEFORE:
from client import reserve_stock, release_stock, charge_payment, refund_payment

# AFTER:
from transport import reserve_stock, release_stock, charge_payment, refund_payment
```

### Updated recovery.py Import
```python
# BEFORE:
from client import reserve_stock, charge_payment, CircuitBreakerError

# AFTER:
from transport import reserve_stock, charge_payment
from circuitbreaker import CircuitBreakerError  # keep direct import, it's a type not transport
```

### Updated app.py Startup
```python
from transport import COMM_MODE

@app.before_serving
async def startup():
    global db, _stop_event, _queue_db
    # ... Redis cluster setup unchanged ...

    if COMM_MODE == "queue":
        from queue_client import init_queue_client
        from reply_listener import setup_reply_consumer_group, reply_listener
        _queue_db = ...  # queue Redis connection
        init_queue_client(_queue_db)
        await setup_reply_consumer_group(_queue_db)
        _stop_event = asyncio.Event()
        app.add_background_task(reply_listener, _queue_db, _stop_event)
    else:
        from client import init_grpc_clients
        await init_grpc_clients()

    # ... rest of startup (recovery, consumers, grpc server) ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Direct `from client import` in all files | `from transport import` for domain functions | Phase 10 | Single env var switches all transport |
| gRPC-only recovery.py | Transport-agnostic recovery.py | Phase 10 | Recovery works in both modes |

**No deprecated patterns** -- this phase creates a new abstraction over existing working code.

## Open Questions

1. **Queue Redis connection in app.py**
   - What we know: Queue mode needs a separate Redis connection for streams (orchestrator Redis, not domain-specific Redis). Phase 9 tests use db=4 for queue.
   - What's unclear: In production (Redis Cluster), is there a separate queue Redis or does the orchestrator share its cluster? Phase 9 research says "use the orchestrator's Redis for all queue streams."
   - Recommendation: Use the existing `db` (orchestrator's Redis cluster connection) as `queue_db` in production. The streams already use `{queue}` hash tags for cluster compatibility.

2. **CircuitBreakerError in queue mode**
   - What we know: `grpc_server.py` and `recovery.py` catch `CircuitBreakerError`. Queue functions never raise this.
   - What's unclear: Should queue mode have its own error type for timeouts?
   - Recommendation: No. Queue timeouts return error dicts (not exceptions), which the existing `if not result.get("success")` checks already handle. The `CircuitBreakerError` catch is a harmless no-op in queue mode.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/test_transport_adapter.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MQC-04 | Transport adapter re-exports correct functions based on COMM_MODE | unit | `pytest tests/test_transport_adapter.py::test_grpc_mode_exports -x` | Wave 0 |
| MQC-04 | Both modes have identical function signatures | unit | `pytest tests/test_transport_adapter.py::test_signature_parity -x` | Wave 0 |
| MQC-05 | COMM_MODE=grpc uses gRPC transport end-to-end | integration | `pytest tests/test_transport_adapter.py::test_checkout_grpc_mode -x` | Wave 0 |
| MQC-05 | COMM_MODE=queue uses queue transport end-to-end | integration | `pytest tests/test_transport_adapter.py::test_checkout_queue_mode -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_transport_adapter.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_transport_adapter.py` -- covers MQC-04, MQC-05
- [ ] No new fixtures needed beyond existing conftest.py + test_queue_infrastructure.py patterns

## Sources

### Primary (HIGH confidence)
- Direct code inspection of `orchestrator/client.py` -- 6 functions with exact signatures
- Direct code inspection of `orchestrator/queue_client.py` -- 6 functions with matching signatures
- Direct code inspection of `orchestrator/grpc_server.py` -- imports `reserve_stock, release_stock, charge_payment, refund_payment` from `client`
- Direct code inspection of `orchestrator/recovery.py` -- imports `reserve_stock, charge_payment, CircuitBreakerError` from `client`
- Direct code inspection of `orchestrator/app.py` -- imports `init_grpc_clients, close_grpc_clients` from `client`
- Phase 9 research confirming queue_client was designed as drop-in replacement

### Secondary (MEDIUM confidence)
- None needed -- this is pure codebase analysis, no external libraries involved

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new libraries, pure Python module pattern
- Architecture: HIGH - both transport modules already exist with matching signatures; adapter is thin re-export
- Pitfalls: HIGH - identified from direct code inspection of all import sites

**Research date:** 2026-03-12
**Valid until:** 2026-04-12 (stable -- internal architecture, no external dependencies)
