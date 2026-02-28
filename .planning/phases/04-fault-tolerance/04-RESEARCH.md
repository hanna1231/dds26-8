# Phase 4: Fault Tolerance - Research

**Researched:** 2026-02-28
**Domain:** Circuit breakers, SAGA restart recovery, Docker container kill/recovery, exponential backoff
**Confidence:** HIGH (standard patterns; library choices confirmed against official docs)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**SAGA recovery on restart:**
- Resume forward first: on orchestrator startup, attempt to complete incomplete SAGAs from where they left off; only compensate if forward progress fails
- Startup scan blocks new checkouts until all stale SAGAs are resolved (no background recovery)
- Detailed logging for each recovered SAGA: ID, state found, action taken (resumed/compensated), outcome

**Circuit breaker policy:**
- Per-service circuit breakers: Stock and Payment each have independent breakers
- When tripped, return 503 Service Unavailable to the caller
- Half-open probe recovery: after cooldown, allow one test request through; close breaker on success
- SAGA in progress when breaker trips: compensate the SAGA (don't leave it hanging)

**Kill-recovery behavior:**
- Services (Stock, Payment) are stateless — container restarts and immediately serves requests; orchestrator handles SAGA recovery
- Docker restart policy (`restart: always` or `on-failure`) for automatic container restart
- Consistency verification lives in integration tests, not built into the system
- Fault tolerance tests use real Docker container kills (`docker kill`), not application-level simulation

**Retry & backoff strategy:**
- Forward SAGA steps (reserve stock, charge payment): max 3 retries before giving up and compensating
- Compensation steps (refund payment, restore stock): retry indefinitely until success (per SAGA-05 requirement)
- Backoff curve: exponential with random jitter to avoid thundering herd
- Max backoff cap: 30 seconds

### Claude's Discretion

- SAGA staleness timeout threshold (how old before compensate instead of resume)
- Circuit breaker failure threshold (number of consecutive failures to trip)
- Circuit breaker cooldown duration
- Exact jitter algorithm and initial backoff interval
- Internal implementation patterns (where circuit breaker state lives, retry loop structure)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FAULT-01 | System recovers when any single container (service or database) is killed | Docker `restart: always` policy + stateless service design; Redis persistence for SAGA state; `docker kill` test approach |
| FAULT-02 | On orchestrator startup, incomplete SAGAs are scanned and resolved (complete or compensate) | `redis.asyncio` `scan_iter(match="saga:*")` in `before_serving` hook; SAGA state inspection and replay of `run_checkout` / `run_compensation` |
| FAULT-03 | System remains consistent after container kill + recovery cycle | Idempotency keys already in place (Phase 2/3); compensation flag fields (`stock_restored`, `refund_done`) prevent double execution; verified by integration tests using `docker kill` + state assertion |
| FAULT-04 | Circuit breaker prevents cascade failures when downstream services are unavailable | `circuitbreaker` 2.1.3 `@circuit` decorator wrapping gRPC client functions; `expected_exception=grpc.aio.AioRpcError`; `CircuitBreakerError` triggers SAGA compensation |
</phase_requirements>

---

## Summary

Phase 4 hardens the existing Phase 3 SAGA orchestration against real-world failure modes without adding new features. Three technical domains need precise implementation: (1) a SAGA startup scanner that identifies non-terminal SAGAs in Redis and drives them to completion or compensation before the orchestrator begins serving; (2) per-service circuit breakers on the gRPC client functions in `client.py` that trip on repeated `AioRpcError` failures and trigger SAGA compensation; and (3) Docker `restart: always` policies plus bounded-retry logic on forward SAGA steps replacing the current unbounded-retry pattern.

The codebase is well-positioned for this phase. SAGA flag fields (`stock_reserved`, `payment_charged`, `refund_done`, `stock_restored`) already exist and are idempotency-safe, meaning the startup scanner can safely call `run_checkout` or `run_compensation` on recovered SAGAs without side-effect duplication. The Phase 2 Lua idempotency cache ensures replayed gRPC calls return cached results instead of executing again. The only missing pieces are: (a) the startup scan function, (b) circuit breaker wrapping on `client.py` functions, and (c) bounded retries on forward steps plus fault-tolerance integration tests that use `docker kill`.

The `circuitbreaker` library (version 2.1.3, released March 31, 2025) is the recommended choice. It natively supports async functions, is actively maintained, and provides the exact half-open single-probe behavior the user requires. It requires zero dependency infrastructure beyond pip install. Hand-rolling a circuit breaker state machine would be error-prone and is explicitly covered by this library.

**Primary recommendation:** Use `circuitbreaker==2.1.3` for circuit breakers; use `redis.asyncio` `scan_iter` in the Quart `before_serving` hook for startup SAGA recovery; add Docker `restart: always` to all services in `docker-compose.yml`.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `circuitbreaker` | 2.1.3 (Mar 2025) | Circuit breaker decorator wrapping async gRPC client functions | Actively maintained, native async support, single-probe half-open behavior, `expected_exception` callable param |
| `redis.asyncio` (bundled in `redis[hiredis]`) | 5.0.3 (already in requirements) | `scan_iter` for startup SAGA key scan | Already a project dependency; `scan_iter` is non-blocking cursor-based iteration |
| `grpc.aio` (bundled in `grpcio`) | 1.78.0 (already in requirements) | `AioRpcError` is the exception class the circuit breaker must catch | Already a project dependency |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `random` (stdlib) | built-in | Jitter calculation for backoff delays | Always — used in `retry_forever` and new bounded `retry_forward` |
| `asyncio` (stdlib) | built-in | `asyncio.sleep` for backoff waits | Already used in `grpc_server.py` `retry_forever` |
| Docker Compose `restart` policy | Compose v3 | Automatic container restart after `docker kill` | Set `restart: always` on all services in `docker-compose.yml` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `circuitbreaker` | `aiobreaker` 1.2.0 | aiobreaker last released May 2021 — effectively abandoned. `circuitbreaker` 2.1.3 released March 2025 — active. |
| `circuitbreaker` | `purgatory-circuitbreaker` | purgatory is asyncio-native but has 2 stars/1 fork — tiny community, higher maintenance risk |
| `circuitbreaker` | Hand-rolled state machine | Complex — missed edge cases around half-open probe race conditions; `circuitbreaker` solves this correctly |
| `scan_iter` | `KEYS saga:*` | `KEYS` blocks Redis while scanning entire keyspace — never use in production; `scan_iter` is cursor-based O(1) per call |

**Installation:**
```bash
pip install circuitbreaker==2.1.3
```
Add to `orchestrator/requirements.txt`:
```
circuitbreaker==2.1.3
```

---

## Architecture Patterns

### Recommended Project Structure Changes

```
orchestrator/
├── app.py           # Add startup scan call in before_serving hook
├── circuit.py       # NEW: circuit breaker instances (stock_breaker, payment_breaker)
├── client.py        # Wrap reserve_stock, release_stock, charge_payment, refund_payment with breakers
├── grpc_server.py   # Replace retry_forever on forward steps with retry_forward (bounded, 3 retries)
│                    # Catch CircuitBreakerError, transition to COMPENSATING
├── saga.py          # No changes needed
└── recovery.py      # NEW: startup SAGA scan and recovery logic
docker-compose.yml   # Add restart: always to all services
tests/
└── test_fault_tolerance.py  # NEW: Docker-kill integration tests
```

### Pattern 1: Circuit Breaker on gRPC Clients

**What:** Wrap each gRPC client function in `client.py` with a per-service `@circuit` decorator. The decorator counts `AioRpcError` exceptions as failures. After N consecutive failures (Claude's discretion: recommended 5), the breaker opens and subsequent calls immediately raise `CircuitBreakerError` without making gRPC calls. After cooldown (recommended: 30 seconds), one probe call is allowed through — if it succeeds, breaker closes.

**When to use:** All outbound gRPC calls to Stock and Payment — `reserve_stock`, `release_stock`, `charge_payment`, `refund_payment`.

**Key API (Source: https://pypi.org/project/circuitbreaker/ v2.1.3):**
```python
# circuit.py — module-level breaker instances
from circuitbreaker import CircuitBreaker

stock_breaker = CircuitBreaker(
    failure_threshold=5,        # open after 5 consecutive AioRpcErrors
    recovery_timeout=30,        # seconds in open state before half-open probe
    expected_exception=grpc.aio.AioRpcError,
    name="stock_service",
)

payment_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    expected_exception=grpc.aio.AioRpcError,
    name="payment_service",
)
```

```python
# client.py — wrap each function with the breaker instance
from circuitbreaker import CircuitBreakerError
from circuit import stock_breaker, payment_breaker

@stock_breaker
async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReserveStock(...)
    return {"success": resp.success, "error_message": resp.error_message}
```

**In grpc_server.py — handle CircuitBreakerError:**
```python
from circuitbreaker import CircuitBreakerError

try:
    result = await reserve_stock(item_id, quantity, idempotency_key)
except CircuitBreakerError:
    # Circuit open — compensate immediately, return 503 to caller
    await transition_state(db, saga_key, "STARTED", "COMPENSATING")
    await run_compensation(db, await get_saga(db, order_id))
    return {"success": False, "error_message": "service unavailable"}
```

**Half-open behavior:** After `recovery_timeout` seconds, the breaker enters half-open. The next call is allowed through. If it succeeds: breaker closes (CLOSED state). If it fails: breaker reopens (OPEN state) and resets the recovery timer.

### Pattern 2: Bounded Forward Retry (max 3 attempts)

**What:** Replace or supplement current unbounded gRPC calls on forward steps with a bounded retry loop (max 3 attempts) before giving up and triggering compensation. Transient gRPC errors (not circuit-tripping ones) deserve a short retry window.

**When to use:** `reserve_stock` and `charge_payment` in `run_checkout`. Not for compensation steps — those remain `retry_forever`.

```python
async def retry_forward(fn, max_attempts: int = 3, base: float = 0.5, cap: float = 30.0):
    """
    Retry fn up to max_attempts times with full-jitter exponential backoff.
    Returns result dict on success. Raises last exception or returns failure dict after exhaustion.
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            result = await fn()
            if result.get("success"):
                return result
            last_error = result.get("error_message", "unknown error")
        except grpc.aio.AioRpcError as exc:
            last_error = str(exc)
        if attempt < max_attempts - 1:
            delay = min(cap, base * (2 ** attempt))
            jitter = random.uniform(0, delay)  # full jitter
            await asyncio.sleep(jitter)
    return {"success": False, "error_message": last_error}
```

**Note:** `CircuitBreakerError` from an open breaker must NOT be retried — it propagates up immediately. Only `AioRpcError` and application-level failures get retried.

### Pattern 3: SAGA Startup Recovery Scanner

**What:** On orchestrator startup (in `before_serving` hook), scan all `saga:*` keys in Redis. For each SAGA not in a terminal state (`COMPLETED` or `FAILED`), determine the correct action based on current state and attempt recovery. Block the Quart app from serving until scan is complete.

**When to use:** In `app.py` `before_serving` hook, before `serve_grpc` background task is started.

**Staleness recommendation (Claude's Discretion):** SAGAs older than 5 minutes with no `updated_at` activity are considered stale. For stale non-terminal SAGAs: attempt forward completion first (consistent with locked decision "resume forward first"). Only transition to compensation if the forward attempt fails (e.g., service unavailable, insufficient stock confirmed). SAGAs started within the last 5 minutes that are in a non-terminal state likely belong to a concurrent request — log a warning and skip (they will complete on their own or timeout naturally).

**Implementation sketch:**
```python
# recovery.py
import logging
import time

NON_TERMINAL = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING"}
STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes — Claude's discretion

async def recover_incomplete_sagas(db) -> None:
    """
    Scan Redis for incomplete SAGAs and drive them to terminal state.
    Called from app.before_serving — blocks until all stale SAGAs resolved.
    """
    recovered = 0
    skipped = 0
    now = int(time.time())

    async for key in db.scan_iter(match="saga:*", count=100):
        raw = await db.hgetall(key)
        if not raw:
            continue
        saga = {k.decode(): v.decode() for k, v in raw.items()}
        state = saga.get("state", "")

        if state not in NON_TERMINAL:
            continue  # terminal — skip

        updated_at = int(saga.get("updated_at", "0"))
        age_seconds = now - updated_at

        if age_seconds < STALENESS_THRESHOLD_SECONDS:
            logging.warning(
                "SAGA %s is in %s state but only %ds old — skipping (recent)",
                saga.get("order_id"), state, age_seconds,
            )
            skipped += 1
            continue

        order_id = saga.get("order_id")
        logging.info(
            "Recovering SAGA %s: state=%s age=%ds",
            order_id, state, age_seconds,
        )

        if state == "COMPENSATING":
            # Already in compensation — drive it to FAILED
            await run_compensation(db, saga)
            logging.info("SAGA %s: compensation completed -> FAILED", order_id)
        else:
            # STARTED, STOCK_RESERVED, PAYMENT_CHARGED — attempt forward first
            from grpc_server import run_checkout
            import json
            result = await run_checkout(
                db,
                order_id=order_id,
                user_id=saga["user_id"],
                items=json.loads(saga["items_json"]),
                total_cost=int(saga["total_cost"]),
            )
            logging.info(
                "SAGA %s: forward attempt -> success=%s error=%s",
                order_id, result["success"], result.get("error_message"),
            )
        recovered += 1

    logging.info(
        "SAGA recovery complete: %d recovered, %d skipped (recent)",
        recovered, skipped,
    )
```

**Important:** `run_checkout` already handles the "SAGA already exists" path — it reads the current state and returns the cached result for COMPLETED/FAILED SAGAs, and returns "checkout already in progress" for non-terminal ones. The recovery function must call a variant that actually drives forward from the current state. See **Pitfall 3** below — `run_checkout` needs to be adapted or a separate `resume_saga` function created that starts from the existing SAGA's current state rather than assuming STARTED.

### Pattern 4: Docker Restart Policy

**What:** Add `restart: always` to all services in `docker-compose.yml`. This ensures that after a `docker kill`, Docker Compose automatically restarts the container without manual intervention.

**When to use:** All services: `order-service`, `stock-service`, `payment-service`, `orchestrator-service`, plus the Redis databases.

```yaml
# docker-compose.yml — add to each service block
services:
  stock-service:
    restart: always
    # ... existing config
  payment-service:
    restart: always
  orchestrator-service:
    restart: always
  order-service:
    restart: always
```

**`restart: always` vs `on-failure`:** `restart: always` restarts on any exit including clean exit (code 0); `on-failure` only on non-zero exit codes. For `docker kill` (which sends SIGKILL, non-zero exit), both work. `restart: always` is simpler and matches the locked decision.

### Anti-Patterns to Avoid

- **Using `KEYS saga:*` instead of `scan_iter`:** `KEYS` blocks the entire Redis event loop for the duration of the scan. In production with thousands of keys this causes noticeable latency spikes. Always use `scan_iter`.
- **Sharing one circuit breaker between Stock and Payment:** The locked decision requires independent breakers. A shared breaker means a Stock outage would block Payment calls and vice versa.
- **Catching `CircuitBreakerError` inside `retry_forward`:** Circuit open means the service is down — retrying immediately makes things worse. `CircuitBreakerError` must propagate out of retry loops, not be caught.
- **Running startup recovery as a background task:** The locked decision requires blocking. If recovery runs in background, new checkout requests can arrive before recovery completes, creating race conditions where a recovering SAGA and a new request for the same `order_id` collide.
- **Calling `run_checkout` directly from recovery for mid-state SAGAs:** `run_checkout` sees an existing non-terminal SAGA and returns "checkout already in progress" — it does NOT resume from mid-state. A dedicated `resume_saga` function is needed.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Circuit breaker state machine | Custom open/closed/half-open logic with asyncio.Lock | `circuitbreaker==2.1.3` | Half-open probe race conditions, counter resets on partial failures, thread/task safety — all solved by the library |
| Exponential backoff with jitter | Custom `random.uniform(0, cap)` formula | Use the AWS full-jitter formula in `retry_forward` (it's 3 lines — no library needed at this scale) | The formula is simple enough to inline; adding `backoff` library for 3 lines is overkill |
| Redis key scanner | Manual `SCAN` cursor loop | `db.scan_iter(match="saga:*")` | `scan_iter` handles cursor pagination automatically and is the idiomatic redis-py async API |
| Docker restart logic | Health check scripts, custom watchdogs | Docker Compose `restart: always` | The container runtime handles restart natively; custom logic adds complexity with no benefit |

**Key insight:** The circuit breaker is the only domain complex enough to justify a library. Everything else (retry loops, backoff math, Redis scanning) is simple enough to implement directly using already-available APIs.

---

## Common Pitfalls

### Pitfall 1: CircuitBreakerError vs AioRpcError Handling Confusion

**What goes wrong:** Developer catches `grpc.aio.AioRpcError` broadly in `run_checkout` and retries it — this also retries `CircuitBreakerError` if the breaker wrapping is not applied at the right layer.

**Why it happens:** `circuitbreaker` raises `CircuitBreakerError` (not `AioRpcError`) when the circuit is open. If retry loops catch `Exception` broadly, they retry against an open circuit, causing a thundering herd against the breaker.

**How to avoid:** In `retry_forward`, catch `grpc.aio.AioRpcError` specifically (for transient retries) and let `CircuitBreakerError` propagate without catching it.

**Warning signs:** Log shows rapid retry loops with "CircuitBreakerError" — means the retry loop is swallowing it.

### Pitfall 2: Startup Recovery Calling run_checkout for Mid-State SAGAs

**What goes wrong:** `run_checkout` checks if a SAGA record exists. If it does and is non-terminal, it returns `{"success": False, "error_message": "checkout already in progress"}` — it does NOT resume from mid-state. Calling `run_checkout` from the recovery scanner for `STOCK_RESERVED` SAGAs does nothing useful.

**Why it happens:** `run_checkout` is designed for idempotent external callers, not for internal recovery. The existing-SAGA branch only handles `COMPLETED` and `FAILED` terminal states.

**How to avoid:** Write a `resume_saga(db, saga)` function in `recovery.py` that inspects the current state and picks up from the correct step:
- `STARTED` → attempt stock reservation forward
- `STOCK_RESERVED` → attempt payment charge forward
- `PAYMENT_CHARGED` → attempt COMPLETED transition
- `COMPENSATING` → call `run_compensation()`

**Warning signs:** Recovery log shows "checkout already in progress" for all recovered SAGAs — means `run_checkout` is being called instead of `resume_saga`.

### Pitfall 3: SAGA Staleness Threshold Too Aggressive

**What goes wrong:** Setting the staleness threshold too low (e.g., 10 seconds) causes the startup scanner to try to recover SAGAs that are still being actively processed by concurrent requests (if the orchestrator had multiple workers — though it runs `--workers 1`). Setting it too high (e.g., 1 hour) means genuinely stuck SAGAs sit unresolved.

**Why it happens:** There's inherent ambiguity: a SAGA with `updated_at` from 60 seconds ago could be either stuck (orchestrator crashed mid-step) or in-flight (rare legitimate slow gRPC call).

**How to avoid:** 5 minutes is appropriate given the orchestrator runs single-worker and gRPC calls have a 5-second timeout. Any SAGA older than 5 minutes with no update is definitively stuck (the gRPC call would have timed out by then). Log the staleness decision for audit.

**Warning signs:** SAGAs never get recovered at startup (threshold too high) or active SAGAs get double-compensated at startup (threshold too low).

### Pitfall 4: Circuit Breaker Instances Must Be Module-Level, Not Per-Request

**What goes wrong:** Creating a new `CircuitBreaker()` instance per gRPC call (e.g., inside the `reserve_stock` function body). Each instance starts with a fresh counter — the circuit never accumulates enough failures to trip.

**Why it happens:** Mistaking the decorator for a per-call configuration.

**How to avoid:** Instantiate `stock_breaker` and `payment_breaker` once at module import time in `circuit.py`. Use them as decorators on the `client.py` functions.

**Warning signs:** Circuit never trips even when the downstream service is clearly down.

### Pitfall 5: docker kill vs docker stop for Test Realism

**What goes wrong:** Tests use `docker stop` (sends SIGTERM, waits 10 seconds for graceful shutdown). Uvicorn handles SIGTERM gracefully — the in-flight request completes before shutdown. This is a much more lenient scenario than a true crash.

**Why it happens:** Confusion between `docker stop` and `docker kill`. The locked decision specifies `docker kill` which sends SIGKILL — immediate, no cleanup.

**How to avoid:** Use `docker kill <container>` in fault-tolerance tests to simulate a real crash (SIGKILL). The container exits immediately with code 137. Docker's `restart: always` then restarts it.

**Warning signs:** Tests pass with `docker stop` but fail with `docker kill` — means the graceful shutdown was masking a recovery bug.

---

## Code Examples

Verified patterns from official sources and project conventions:

### Circuit Breaker Setup (circuitbreaker 2.1.3)

```python
# orchestrator/circuit.py
# Source: https://pypi.org/project/circuitbreaker/ v2.1.3
import grpc.aio
from circuitbreaker import CircuitBreaker

# Per-service independent circuit breakers (locked decision)
stock_breaker = CircuitBreaker(
    failure_threshold=5,          # Claude's discretion: 5 consecutive AioRpcErrors
    recovery_timeout=30,          # Claude's discretion: 30s cooldown before half-open probe
    expected_exception=grpc.aio.AioRpcError,
    name="stock_service",
)

payment_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    expected_exception=grpc.aio.AioRpcError,
    name="payment_service",
)
```

### Wrapping gRPC Client Functions

```python
# orchestrator/client.py (modified)
# Source: project convention + circuitbreaker docs
from circuitbreaker import CircuitBreakerError  # noqa — re-exported for callers
from circuit import stock_breaker, payment_breaker

@stock_breaker
async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    resp = await _stock_stub.ReserveStock(
        ReserveStockRequest(item_id=item_id, quantity=quantity, idempotency_key=idempotency_key),
        timeout=RPC_TIMEOUT,
    )
    return {"success": resp.success, "error_message": resp.error_message}

# Same pattern for release_stock, charge_payment, refund_payment
```

### SAGA Startup Recovery Scan

```python
# In app.py before_serving hook
@app.before_serving
async def startup():
    global db
    db = redis.Redis(...)
    await init_grpc_clients()
    # Block until all stale SAGAs resolved (locked decision)
    await recover_incomplete_sagas(db)
    # Only start serving after recovery is complete
    app.add_background_task(serve_grpc, db)
```

```python
# orchestrator/recovery.py
# Source: redis-py scan_iter docs + project SAGA conventions
import logging
import time
import json

NON_TERMINAL_STATES = {"STARTED", "STOCK_RESERVED", "PAYMENT_CHARGED", "COMPENSATING"}
STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes

async def resume_saga(db, saga: dict) -> None:
    """Drive a partially-completed SAGA to a terminal state from its current step."""
    from grpc_server import run_compensation
    from client import reserve_stock, charge_payment, CircuitBreakerError
    import json

    order_id = saga["order_id"]
    saga_key = f"saga:{order_id}"
    state = saga["state"]

    logging.info("Recovering SAGA %s from state=%s", order_id, state)

    if state == "COMPENSATING":
        await run_compensation(db, saga)
        return

    # Forward steps — attempt to drive to COMPLETED
    # (All gRPC calls are idempotent via Phase 2 Lua keys — safe to replay)
    try:
        if state == "STARTED":
            # Re-attempt stock reservation
            items = json.loads(saga["items_json"])
            for item in items:
                result = await reserve_stock(
                    item["item_id"], item["quantity"],
                    f"saga:{order_id}:step:reserve:{item['item_id']}"
                )
                if not result.get("success"):
                    await transition_state(db, saga_key, state, "COMPENSATING")
                    await run_compensation(db, await get_saga(db, order_id))
                    return
            await transition_state(db, saga_key, "STARTED", "STOCK_RESERVED", "stock_reserved", "1")
            state = "STOCK_RESERVED"

        if state == "STOCK_RESERVED":
            result = await charge_payment(
                saga["user_id"], int(saga["total_cost"]),
                f"saga:{order_id}:step:charge"
            )
            if not result.get("success"):
                await transition_state(db, saga_key, state, "COMPENSATING")
                await run_compensation(db, await get_saga(db, order_id))
                return
            await transition_state(db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED", "payment_charged", "1")
            state = "PAYMENT_CHARGED"

        if state == "PAYMENT_CHARGED":
            await transition_state(db, saga_key, "PAYMENT_CHARGED", "COMPLETED")

        logging.info("SAGA %s: recovery -> COMPLETED", order_id)

    except CircuitBreakerError as exc:
        # Service unavailable during recovery — compensate
        logging.error("SAGA %s: circuit open during recovery, compensating: %s", order_id, exc)
        current = await get_saga(db, order_id)
        if current and current["state"] not in ("COMPLETED", "FAILED"):
            await transition_state(db, saga_key, current["state"], "COMPENSATING")
            await run_compensation(db, current)
```

### Bounded Forward Retry (max 3 attempts)

```python
# orchestrator/grpc_server.py — new retry_forward function
import random

async def retry_forward(fn, max_attempts: int = 3, base: float = 0.5, cap: float = 30.0) -> dict:
    """
    Retry async callable fn up to max_attempts times with full-jitter backoff.
    CircuitBreakerError propagates immediately (not retried).
    Returns first success dict, or last failure dict after exhaustion.
    """
    from circuitbreaker import CircuitBreakerError

    last_result = {"success": False, "error_message": "max retries exceeded"}
    for attempt in range(max_attempts):
        try:
            result = await fn()
            if result.get("success"):
                return result
            last_result = result
        except CircuitBreakerError:
            raise  # breaker open — propagate immediately, don't retry
        except Exception as exc:
            last_result = {"success": False, "error_message": str(exc)}
        if attempt < max_attempts - 1:
            delay = min(cap, base * (2 ** attempt))
            jitter = random.uniform(0, delay)  # AWS full-jitter algorithm
            await asyncio.sleep(jitter)
    return last_result
```

### Docker Compose Restart Policy

```yaml
# docker-compose.yml
services:
  stock-service:
    restart: always
    # ... existing config unchanged

  payment-service:
    restart: always

  orchestrator-service:
    restart: always

  order-service:
    restart: always
```

### Fault Tolerance Integration Test Pattern

```python
# tests/test_fault_tolerance.py
import subprocess
import asyncio
import time

def docker_kill(service_name: str) -> None:
    """Kill a Docker Compose service container (SIGKILL — no graceful shutdown)."""
    result = subprocess.run(
        ["docker", "compose", "kill", service_name],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"docker compose kill failed: {result.stderr}"

def docker_start(service_name: str) -> None:
    """Restart a stopped Docker Compose service."""
    result = subprocess.run(
        ["docker", "compose", "start", service_name],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"docker compose start failed: {result.stderr}"

async def wait_for_service_ready(stub, timeout: float = 30.0) -> None:
    """Poll until gRPC service is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            await stub.CheckStock(...)  # or any lightweight RPC
            return
        except grpc.aio.AioRpcError:
            await asyncio.sleep(0.5)
    raise TimeoutError("Service did not recover in time")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `aiobreaker` 1.2.0 | `circuitbreaker` 2.1.3 | Mar 2025 (circuitbreaker updated) | `aiobreaker` is now abandoned; `circuitbreaker` is actively maintained with native async |
| `KEYS pattern` for Redis key scan | `SCAN cursor` / `scan_iter` | Redis ~2.8 (2013) | Non-blocking; mandatory for production |
| Manual cursor-based `SCAN` loop | `db.scan_iter(match="saga:*")` | redis-py v3+ | Idiomatic, handles cursor pagination automatically |

**Deprecated/outdated:**
- `aiobreaker`: Last release May 2021 — do not use for new projects
- `KEYS saga:*`: Blocks Redis — never use in production startup scan

---

## Open Questions

1. **`resume_saga` import circularity risk**
   - What we know: `recovery.py` needs to import from `grpc_server.py` (`run_compensation`), and `grpc_server.py` already imports from `client.py`. The chain `recovery.py → grpc_server.py → client.py → circuit.py` is linear — no cycle.
   - What's unclear: If `grpc_server.py` also imports from `recovery.py` in the future, a cycle forms.
   - Recommendation: Keep recovery logic in `recovery.py` and only import from it in `app.py`. Do not import `recovery.py` from `grpc_server.py`.

2. **How to distinguish "SAGA recovery attempted compensation itself" from "SAGA was being compensated when orchestrator crashed"**
   - What we know: Both cases show `state=COMPENSATING`. The `refund_done` and `stock_restored` flags in the SAGA record tell us how far compensation got.
   - What's unclear: No ambiguity — `run_compensation` already reads these flags and only executes undone steps. This is already handled correctly by Phase 3 implementation.
   - Recommendation: No change needed; `run_compensation` is already idempotent.

3. **Fault tolerance tests: run against Docker Compose or test gRPC servers?**
   - What we know: The locked decision says tests use real `docker kill`. This requires Docker Compose to be running. Existing `tests/test_saga.py` uses in-process gRPC servers with no Docker dependency.
   - What's unclear: Should fault tolerance tests live in `tests/` (pytest, in-process) or in a separate integration test directory that requires Docker?
   - Recommendation: Create `tests/test_fault_tolerance.py` that uses `subprocess` to call `docker compose kill/start`. Mark with `@pytest.mark.requires_docker` and a separate pytest mark that skips by default unless explicitly opted in. This keeps `pytest tests/` fast for unit/integration tests and `pytest tests/ -m requires_docker` for full fault scenarios.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (already configured) |
| Config file | `pytest.ini` (root) — `asyncio_mode = auto`, session loop scope |
| Quick run command | `pytest tests/test_fault_tolerance.py -x -k "not requires_docker"` |
| Full suite command | `pytest tests/ -x` (non-Docker) / `pytest tests/ -m requires_docker` (with Docker) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FAULT-01 | Any single container killed, state recovers | integration (Docker) | `pytest tests/test_fault_tolerance.py -m requires_docker -k "kill"` | ❌ Wave 0 |
| FAULT-02 | Orchestrator restart scans and resolves incomplete SAGAs | unit (in-process) | `pytest tests/test_fault_tolerance.py -k "test_startup_recovery"` | ❌ Wave 0 |
| FAULT-03 | Database state consistent after kill+recovery cycle | integration (Docker) | `pytest tests/test_fault_tolerance.py -m requires_docker -k "consistency"` | ❌ Wave 0 |
| FAULT-04 | Circuit breaker prevents cascade when service unavailable | unit (mock gRPC) | `pytest tests/test_fault_tolerance.py -k "test_circuit_breaker"` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/ -x -k "not requires_docker"` (fast, no Docker)
- **Per wave merge:** `pytest tests/ -x` (all non-Docker tests)
- **Phase gate:** `pytest tests/ -m requires_docker` must pass before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_fault_tolerance.py` — covers FAULT-01, FAULT-02, FAULT-03, FAULT-04
- [ ] `conftest.py` update — add `requires_docker` pytest mark registration
- [ ] `orchestrator/circuit.py` — breaker instances (code, not test)
- [ ] `orchestrator/recovery.py` — startup scanner (code, not test)
- [ ] No new framework install needed — `circuitbreaker==2.1.3` added to `orchestrator/requirements.txt`

---

## Sources

### Primary (HIGH confidence)

- https://pypi.org/project/circuitbreaker/ — version 2.1.3, March 31 2025, async support, `expected_exception`, `failure_threshold`, `recovery_timeout`, `CircuitBreakerError`, half-open single-probe behavior
- https://redis.io/docs/latest/commands/scan/ — SCAN cursor API, MATCH pattern, COUNT option, non-blocking guarantee, `scan_iter` idiomatic usage
- Project codebase (`orchestrator/saga.py`, `grpc_server.py`, `client.py`, `app.py`) — SAGA state fields, flag fields, existing `retry_forever`, `transition_state`, Quart lifecycle hooks

### Secondary (MEDIUM confidence)

- https://docs.docker.com/engine/containers/start-containers-automatically/ — `restart: always` behavior, SIGKILL on `docker kill`, container exit code 137
- https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/ — Full Jitter algorithm: `sleep = random.uniform(0, min(cap, base * 2**attempt))`
- WebSearch results confirming `aiobreaker` abandoned (last release May 2021) and `circuitbreaker` 2.1.3 is current

### Tertiary (LOW confidence)

- Purgatory circuit breaker (mardiros/purgatory) — mentioned as alternative; LOW because minimal community adoption and not verified against this project's grpc.aio patterns

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — `circuitbreaker` 2.1.3 confirmed on PyPI as of March 2025; redis-py `scan_iter` confirmed against official Redis docs
- Architecture: HIGH — patterns derived directly from existing project code structure and Phase 3 SAGA implementation
- Pitfalls: HIGH — derived from close reading of existing `run_checkout` behavior (Pitfall 2) and circuit breaker library semantics (Pitfall 1, 4)

**Research date:** 2026-02-28
**Valid until:** 2026-03-28 (stable domain — circuit breaker patterns are not fast-moving; circuitbreaker library cadence is slow)
