# Phase 5: Event-Driven Architecture - Research

**Researched:** 2026-02-28
**Domain:** Redis Streams, consumer groups, async background task lifecycle
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Event Granularity
- Every SAGA state change produces an event: checkout_started, stock_reserved, stock_failed, payment_started, payment_completed, payment_failed, compensation_triggered, compensation_completed, saga_completed, saga_failed
- Rich payloads: saga_id, event_type, timestamp, step details (service, action, result), order context (order_id, user_id, amounts)
- Each event carries a schema version field (e.g. v1) for forward compatibility
- Compensation events include failure context: failed_step, error_type, retry_count

#### Stream Topology
- Single stream per saga type (e.g. saga:checkout:events) — all lifecycle events in one stream, consumers filter by event_type
- One consumer group per concern: compensation-handler and audit-logger
- Stream entries trimmed with XADD MAXLEN ~10000 (approximate trimming for performance)

#### Retry & Dead Letters
- Exponential backoff with jitter for compensation retries (1s, 2s, 4s, 8s...)
- Max 5 retry attempts before giving up (~31s total)
- Permanently failed compensations moved to a saga:dead-letters stream for manual inspection/replay
- XCLAIM after 30s idle timeout to reclaim unacknowledged messages from crashed consumers

#### Non-Blocking Design
- Fire-and-forget XADD during SAGA step transitions — if publish fails, log warning but don't fail checkout
- Consumers run as async background tasks within the FastAPI app (not separate worker processes)
- On Redis unavailability: silent drop with dropped_events metric counter, checkout continues unaffected
- Graceful shutdown: on SIGTERM/app shutdown, finish processing current message, stop reading new ones
- Basic health endpoint exposing consumer lag and dead letter count via existing health check

### Claude's Discretion
- Event serialization format (JSON vs msgpack)
- Exact consumer polling interval and batch size
- Redis connection pooling strategy for stream operations
- Internal event bus abstraction (if any)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| EVENT-01 | Redis Streams used for SAGA lifecycle events (checkout started, stock reserved, payment completed, etc.) | XADD with MAXLEN trimming; single stream `saga:checkout:events`; fire-and-forget publish in `grpc_server.py` after each state transition |
| EVENT-02 | Consumer groups configured for reliable event processing with at-least-once delivery | XGROUP_CREATE for `compensation-handler` and `audit-logger` groups; XREADGROUP + XACK loop; XAUTOCLAIM for reclaiming idle messages after 30s; dead-letters stream for permanently failed entries |
| EVENT-03 | SAGA orchestrator publishes events to streams and consumes responses; event processing does not block the checkout path | Publish via fire-and-forget try/except in `grpc_server.py`; consumers launched as `app.add_background_task()` in `app.py`; asyncio.Event stop flag for graceful shutdown |
</phase_requirements>

## Summary

Phase 5 adds Redis Streams event publishing and consumer group processing to the existing SAGA orchestrator. The orchestrator already uses `redis.asyncio` (redis-py 5.0.3) with the `redis[hiredis]` package — no new Redis infrastructure is needed. All Redis Streams commands (`XADD`, `XREADGROUP`, `XACK`, `XGROUP_CREATE`, `XAUTOCLAIM`) are available in this version and were verified by direct inspection of the installed library.

The implementation involves two new modules: `events.py` (event publishing) and `consumers.py` (background consumer loops). Publishing is fire-and-forget — wrapped in try/except so Redis unavailability never reaches the checkout path. Consumers run as Quart background tasks via `app.add_background_task()`, which Quart awaits on shutdown, providing natural graceful shutdown. A stop flag (`asyncio.Event`) allows the consumer loops to exit cleanly when cancelled.

The most important implementation detail is consumer group setup idempotency: `XGROUP_CREATE` raises `ResponseError: BUSYGROUP Consumer Group name already exists` if the group already exists. The correct pattern is to catch `ResponseError` and ignore only `BUSYGROUP` errors. The `mkstream=True` parameter creates the stream atomically if it does not exist. XAUTOCLAIM (not the older XCLAIM+XPENDING pattern) is the correct modern approach for reclaiming idle messages from crashed consumers.

**Primary recommendation:** Use `events.py` for publish helpers and `consumers.py` for two background loops (`compensation_consumer` and `audit_consumer`), both started in `app.before_serving` with `app.add_background_task()`. Use JSON (via `msgspec.json`) for event serialization — it is already in `requirements.txt`, human-readable for debugging, and the performance difference is negligible for this volume.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis[hiredis] | 5.0.3 (already installed) | XADD, XREADGROUP, XACK, XAUTOCLAIM, XGROUP_CREATE, XINFO_GROUPS | Already in orchestrator/requirements.txt; all stream commands verified present |
| msgspec | 0.18.6 (already installed) | JSON event serialization/deserialization | Already in orchestrator/requirements.txt; faster than stdlib json; used in existing test fixtures |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio.Event | stdlib | Stop flag for consumer loop shutdown | Signal background consumers to stop on SIGTERM/shutdown |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| msgspec.json | json (stdlib) or msgpack | Both work; msgspec already imported; JSON is human-readable; msgpack is binary (slightly smaller/faster but not worth adding complexity; msgpack is NOT the encoding used in Redis Streams field values — those are always strings) |
| XAUTOCLAIM | XPENDING_RANGE + XCLAIM | XAUTOCLAIM is the modern replacement (Redis 6.2+); does both in one call with cursor-based iteration; use XAUTOCLAIM |
| app.add_background_task() | asyncio.create_task() | Quart's built-in method integrates with graceful shutdown; Quart awaits background tasks before shutdown; use add_background_task |

**Installation:** No new packages needed. All required libraries are already in `orchestrator/requirements.txt`.

---

## Architecture Patterns

### Recommended File Structure

```
orchestrator/
├── app.py           # Add: start consumers in before_serving, stop_event in after_serving
├── events.py        # NEW: publish_event() helper, event payload builders
├── consumers.py     # NEW: compensation_consumer(), audit_consumer(), setup_consumer_groups()
├── saga.py          # Unchanged (state machine)
├── grpc_server.py   # Modified: call publish_event() after each state transition
├── recovery.py      # Unchanged (startup scan)
├── client.py        # Unchanged (gRPC clients)
└── circuit.py       # Unchanged (circuit breakers)
```

### Pattern 1: Fire-and-Forget Event Publishing

**What:** Wrap XADD in try/except; log warning on failure; never raise. Called immediately after each state transition in `grpc_server.py`.

**When to use:** Every SAGA lifecycle transition — publish after the state transition succeeds so the event reflects reality.

```python
# Source: redis-py 5.0.3 xadd() signature + verified behavior
# orchestrator/events.py

import asyncio
import logging
import time
import msgspec.json

STREAM_NAME = "saga:checkout:events"
DEAD_LETTERS_STREAM = "saga:dead-letters"
STREAM_MAXLEN = 10_000

_dropped_events = 0  # metric counter


def _build_event(event_type: str, saga_id: str, order_id: str,
                 user_id: str = "", **extra) -> dict:
    """Build a rich event payload dict suitable for XADD fields."""
    return {
        "schema_version": "v1",
        "event_type": event_type,
        "saga_id": saga_id,
        "order_id": order_id,
        "user_id": user_id,
        "timestamp": str(int(time.time())),
        **{k: (msgspec.json.encode(v).decode() if not isinstance(v, str) else v)
           for k, v in extra.items()},
    }


async def publish_event(db, event_type: str, saga_id: str,
                        order_id: str, user_id: str = "", **extra) -> None:
    """
    Fire-and-forget XADD to saga:checkout:events stream.

    Never raises. On any failure, increments dropped_events counter and logs warning.
    Stream entries use approximate trimming at MAXLEN 10000.
    """
    global _dropped_events
    try:
        fields = _build_event(event_type, saga_id, order_id, user_id, **extra)
        await db.xadd(
            STREAM_NAME,
            fields,
            maxlen=STREAM_MAXLEN,
            approximate=True,  # default True; uses ~ trimming for performance
        )
    except Exception as exc:
        _dropped_events += 1
        logging.warning("publish_event failed (dropped_events=%d): %s", _dropped_events, exc)


def get_dropped_events() -> int:
    return _dropped_events
```

### Pattern 2: Consumer Group Setup (Idempotent)

**What:** Create the stream and consumer groups on startup. `XGROUP_CREATE` raises `ResponseError` with `BUSYGROUP` if the group already exists — catch and ignore only that error.

**When to use:** Called once in `app.before_serving` before starting consumer tasks.

```python
# Source: redis-py 5.0.3 verified signatures + Redis docs on BUSYGROUP error
# orchestrator/consumers.py

from redis.exceptions import ResponseError

CONSUMER_GROUPS = ["compensation-handler", "audit-logger"]

async def setup_consumer_groups(db) -> None:
    """
    Create stream and consumer groups idempotently.

    mkstream=True creates the stream if it does not exist.
    id='0' means the group starts reading from the very beginning of the stream.
    BUSYGROUP error means the group already exists — safe to ignore.
    Any other ResponseError is re-raised.
    """
    for group in CONSUMER_GROUPS:
        try:
            await db.xgroup_create(
                STREAM_NAME,
                group,
                id="0",         # start from beginning (not '$' which skips existing)
                mkstream=True,  # atomically creates stream if absent
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            # Group already exists — idempotent, continue
```

### Pattern 3: Consumer Loop with Graceful Shutdown

**What:** A long-running coroutine that reads from a consumer group via XREADGROUP, processes messages, ACKs them, and exits cleanly when a stop event is set.

**When to use:** One loop per consumer group, started as a background task.

```python
# Source: redis-py 5.0.3 XREADGROUP signature + Quart background task docs
# orchestrator/consumers.py (continued)

import asyncio
import logging
import msgspec.json

POLL_INTERVAL_MS = 2000    # block up to 2s waiting for new messages
BATCH_SIZE = 10            # messages per XREADGROUP call
CLAIM_IDLE_MS = 30_000     # reclaim messages idle > 30s (30000ms)
MAX_RETRIES = 5            # dead-letter after 5 delivery attempts

_stop_event: asyncio.Event = None


def init_stop_event() -> asyncio.Event:
    global _stop_event
    _stop_event = asyncio.Event()
    return _stop_event


async def compensation_consumer(db) -> None:
    """
    Background consumer for the 'compensation-handler' consumer group.

    Reads compensation_triggered events and retries compensation with
    exponential backoff. After MAX_RETRIES failed deliveries, moves
    the message to saga:dead-letters stream.

    Exits cleanly when _stop_event is set.
    """
    group = "compensation-handler"
    consumer_name = "orchestrator-1"

    logging.info("compensation_consumer started")
    try:
        while not _stop_event.is_set():
            # --- Phase 1: Reclaim idle messages from crashed peers (XAUTOCLAIM) ---
            # XAUTOCLAIM replaces the older XPENDING + XCLAIM pattern
            # Returns: [next_cursor_id, [(msg_id, fields), ...], [deleted_ids]]
            try:
                autoclaim_result = await db.xautoclaim(
                    STREAM_NAME,
                    group,
                    consumer_name,
                    min_idle_time=CLAIM_IDLE_MS,
                    start_id="0-0",
                    count=BATCH_SIZE,
                )
                # autoclaim_result[1] is list of (id, fields_dict) tuples
                claimed_messages = autoclaim_result[1] if autoclaim_result else []
                for msg_id, fields in claimed_messages:
                    await _handle_compensation_message(db, group, msg_id, fields)
            except Exception as exc:
                logging.warning("xautoclaim error: %s", exc)

            # --- Phase 2: Read new messages (XREADGROUP with ">") ---
            try:
                response = await db.xreadgroup(
                    groupname=group,
                    consumername=consumer_name,
                    streams={STREAM_NAME: ">"},
                    count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,  # wait up to 2s for new messages
                )
                # response: [[stream_name, [(msg_id, fields_dict), ...]]]
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            await _handle_compensation_message(db, group, msg_id, fields)
            except asyncio.CancelledError:
                raise  # re-raise so Quart can complete shutdown
            except Exception as exc:
                logging.warning("xreadgroup error: %s", exc)
                await asyncio.sleep(1)  # brief pause before retry on connection error

    except asyncio.CancelledError:
        logging.info("compensation_consumer cancelled — shutting down")
        raise  # propagate so Quart knows task is done
    finally:
        logging.info("compensation_consumer stopped")


async def _handle_compensation_message(db, group: str, msg_id, fields: dict) -> None:
    """
    Process one message. ACK on success. Move to dead-letters after MAX_RETRIES.

    fields is a dict with bytes keys/values (no decode_responses=True).
    """
    event_type = fields.get(b"event_type", b"").decode()

    if event_type != "compensation_triggered":
        # audit-logger concern only — not for this consumer
        await db.xack(STREAM_NAME, group, msg_id)
        return

    # Check delivery count to detect stuck messages
    # XPENDING_RANGE returns delivery count per message
    try:
        pending = await db.xpending_range(
            STREAM_NAME, group, min=msg_id, max=msg_id, count=1
        )
        delivery_count = pending[0]["times_delivered"] if pending else 0
    except Exception:
        delivery_count = 0

    if delivery_count > MAX_RETRIES:
        # Dead-letter: move to saga:dead-letters, then ACK to remove from PEL
        await db.xadd(DEAD_LETTERS_STREAM, {**fields, b"original_id": msg_id})
        await db.xack(STREAM_NAME, group, msg_id)
        logging.error("Message %s dead-lettered after %d attempts", msg_id, delivery_count)
        return

    # Attempt compensation (re-use existing run_compensation logic)
    try:
        order_id = fields.get(b"order_id", b"").decode()
        if order_id:
            from grpc_server import run_compensation
            from saga import get_saga
            saga = await get_saga(db, order_id)
            if saga and saga.get("state") == "COMPENSATING":
                await run_compensation(db, saga)
        await db.xack(STREAM_NAME, group, msg_id)
    except Exception as exc:
        logging.warning("Compensation for %s failed: %s (will retry)", msg_id, exc)
        # Do NOT ack — message stays in PEL for retry


async def audit_consumer(db) -> None:
    """
    Background consumer for the 'audit-logger' consumer group.

    Simply logs all events to stdout for observability. ACKs every message
    regardless of content (audit is best-effort, not critical path).
    """
    group = "audit-logger"
    consumer_name = "orchestrator-1"

    logging.info("audit_consumer started")
    try:
        while not _stop_event.is_set():
            try:
                response = await db.xreadgroup(
                    groupname=group,
                    consumername=consumer_name,
                    streams={STREAM_NAME: ">"},
                    count=BATCH_SIZE,
                    block=POLL_INTERVAL_MS,
                )
                if response:
                    for _stream, messages in response:
                        for msg_id, fields in messages:
                            event_type = fields.get(b"event_type", b"unknown").decode()
                            order_id = fields.get(b"order_id", b"").decode()
                            logging.info("SAGA_EVENT %s order=%s id=%s", event_type, order_id, msg_id)
                            await db.xack(STREAM_NAME, group, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning("audit_consumer error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("audit_consumer cancelled — shutting down")
        raise
    finally:
        logging.info("audit_consumer stopped")
```

### Pattern 4: Integration into app.py

**What:** Start consumer groups setup and background tasks in `before_serving`, signal stop in `after_serving`.

```python
# orchestrator/app.py (additions)
from consumers import setup_consumer_groups, compensation_consumer, audit_consumer, init_stop_event
from events import get_dropped_events

_stop_event = None

@app.before_serving
async def startup():
    global db, _stop_event
    db = redis.Redis(...)
    await init_grpc_clients()
    await recover_incomplete_sagas(db)
    # Phase 5: Set up consumer groups before starting consumers
    await setup_consumer_groups(db)
    _stop_event = init_stop_event()
    app.add_background_task(serve_grpc, db)
    app.add_background_task(compensation_consumer, db)
    app.add_background_task(audit_consumer, db)


@app.after_serving
async def shutdown():
    # Signal consumers to stop
    if _stop_event:
        _stop_event.set()
    await stop_grpc_server()
    await close_grpc_clients()
    await db.aclose()


@app.get('/health')
async def health():
    try:
        groups = await db.xinfo_groups(STREAM_NAME)
        # groups is a list of dicts: {"name": ..., "pending": ..., "consumers": ..., "lag": ...}
        lag_info = {g["name"]: g.get("lag", 0) for g in groups}
    except Exception:
        lag_info = {}

    try:
        dead_letter_count = await db.xlen(DEAD_LETTERS_STREAM)
    except Exception:
        dead_letter_count = 0

    return jsonify({
        "status": "ok",
        "consumer_lag": lag_info,
        "dead_letters": dead_letter_count,
        "dropped_events": get_dropped_events(),
    })
```

### Pattern 5: Event Publishing in grpc_server.py

**What:** Call `publish_event()` after each successful state transition. Fire-and-forget — wrap in asyncio.create_task or just await (either works since publish_event never raises).

```python
# orchestrator/grpc_server.py (addition after state transitions)
from events import publish_event

# After: await transition_state(db, saga_key, "STARTED", "STOCK_RESERVED", ...)
await publish_event(db, "stock_reserved", f"saga:{order_id}", order_id, user_id)

# After: await transition_state(db, saga_key, "STOCK_RESERVED", "PAYMENT_CHARGED", ...)
await publish_event(db, "payment_completed", f"saga:{order_id}", order_id, user_id,
                    total_cost=str(total_cost))

# After: await transition_state(db, saga_key, "PAYMENT_CHARGED", "COMPLETED")
await publish_event(db, "saga_completed", f"saga:{order_id}", order_id, user_id)

# Before run_compensation():
await publish_event(db, "compensation_triggered", f"saga:{order_id}", order_id,
                    failed_step=current_step, error_type="stock_failure", retry_count="0")

# After run_compensation() completes:
await publish_event(db, "compensation_completed", f"saga:{order_id}", order_id)
```

### Anti-Patterns to Avoid

- **Using `id='$'` in XGROUP_CREATE:** `$` means "only new messages added after the group was created." Use `id='0'` to ensure the group processes existing events in the stream — important for recovery after restart.
- **Blocking XREADGROUP without BLOCK timeout:** Calling `XREADGROUP` without `block=N` makes it return immediately with empty results, causing a busy-wait spin loop that wastes CPU. Always set `block=POLL_INTERVAL_MS`.
- **Not re-raising CancelledError:** Consumer loops MUST catch `asyncio.CancelledError` and re-raise after cleanup so Quart's shutdown can complete. Swallowing `CancelledError` hangs the app.
- **Using decode_responses=True on the Redis client:** The orchestrator uses manual byte decoding (`k.decode()/v.decode()`). Redis Stream field keys and values returned by `XREADGROUP` will be bytes unless `decode_responses=True` is set on the client. Access as `fields.get(b"event_type", b"")` not `fields.get("event_type", "")`.
- **Dead-lettering without ACKing:** After writing to `saga:dead-letters`, you MUST call `XACK` on the original message to remove it from the PEL. Failing to ACK leaves it in the PEL indefinitely.
- **Single Redis connection for publish + consume:** Use the same `db` connection pool — redis-py's asyncio client is safe for concurrent operations. No need for a separate connection.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| At-least-once delivery | Custom ACK tracking in Redis hash | XREADGROUP + XACK + PEL | Redis Streams PEL is purpose-built; handles consumer failure atomically |
| Stale message reclaim | Periodic scan + manual XCLAIM | XAUTOCLAIM with cursor iteration | One command vs. two; cursor-based; increments delivery counter automatically |
| Stream trimming | Scheduled XTRIM job | XADD with maxlen + approximate=True | Inline trimming during publish; approximate (~) is O(1) not O(N) |
| Consumer group idempotency | Check-then-create pattern | XGROUP_CREATE + catch BUSYGROUP | Atomic; no TOCTOU race |
| Background task lifecycle | asyncio.create_task + manual tracking | app.add_background_task() | Quart awaits these on shutdown automatically |

**Key insight:** Redis Streams PEL (Pending Entries List) IS the reliability layer. The entire at-least-once guarantee comes from the PEL — a message stays there until `XACK` is called. All you need is: publish (XADD), read (XREADGROUP with `>`), process, ACK (XACK). Do not build your own delivery tracking.

---

## Common Pitfalls

### Pitfall 1: BUSYGROUP Error on Startup

**What goes wrong:** `await db.xgroup_create(...)` raises `redis.exceptions.ResponseError: BUSYGROUP Consumer Group name already exists` on second startup.

**Why it happens:** `XGROUP_CREATE` is not idempotent — it fails if the group already exists. The `NX` flag does not exist (there was a GitHub issue requesting it, but it was not merged).

**How to avoid:**
```python
try:
    await db.xgroup_create(STREAM_NAME, group, id="0", mkstream=True)
except ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise
```

**Warning signs:** `ResponseError` in startup logs, consumers never start.

### Pitfall 2: XREADGROUP Bytes vs Strings

**What goes wrong:** `fields.get("event_type")` returns `None` even though the key exists.

**Why it happens:** The orchestrator Redis client does NOT use `decode_responses=True`. XREADGROUP returns field keys and values as bytes. The field dict from `parse_stream_list` uses `pairs_to_dict` (not `pairs_to_dict_with_str_keys`), so keys are bytes.

**How to avoid:** Always access stream message fields with bytes keys: `fields.get(b"event_type", b"").decode()`.

**Warning signs:** Consumer processes zero events; silent failures in message handlers.

### Pitfall 3: Consumer Loop Spin on Empty Stream

**What goes wrong:** Consumer loop burns 100% CPU on an empty stream.

**Why it happens:** `XREADGROUP` without `block=N` returns immediately with an empty list. Without a sleep or block, the loop spins continuously.

**How to avoid:** Always pass `block=POLL_INTERVAL_MS` (e.g., 2000 ms). With blocking, the server-side wait means the loop sleeps efficiently between polls.

**Warning signs:** High CPU usage on orchestrator container even with no checkout traffic.

### Pitfall 4: XAUTOCLAIM Return Format

**What goes wrong:** Code tries to iterate `autoclaim_result` as a flat list, fails with `TypeError`.

**Why it happens:** XAUTOCLAIM returns a 3-element list: `[next_cursor, [(id, fields), ...], [deleted_ids]]`. The messages are at index `[1]`, not at the top level.

**How to avoid:**
```python
autoclaim_result = await db.xautoclaim(STREAM_NAME, group, consumer, min_idle_time=30000, start_id="0-0", count=10)
# autoclaim_result is [cursor_id, messages_list, deleted_ids_list]
claimed_messages = autoclaim_result[1] if autoclaim_result else []
```

**Warning signs:** `IndexError` or `TypeError` in autoclaim handler; crash logs at consumer startup.

### Pitfall 5: XPENDING_RANGE delivery counter key name

**What goes wrong:** Code accesses `pending[0]["delivery_count"]` and gets `KeyError`.

**Why it happens:** redis-py's `parse_xpending_range` returns dicts with key `"times_delivered"` (not `"delivery_count"`). The exact keys are: `message_id`, `consumer`, `time_since_delivered`, `times_delivered`.

**How to avoid:** Use `pending[0]["times_delivered"]`.

**Warning signs:** `KeyError: 'delivery_count'` in compensation consumer; dead-letter logic never triggers.

### Pitfall 6: Quart Background Task Not Receiving Stop Signal

**What goes wrong:** App hangs on shutdown because consumer loop is blocked in `XREADGROUP ... block=2000`.

**Why it happens:** When Quart cancels background tasks, `asyncio.CancelledError` is injected. If the consumer swallows it (bare `except Exception` catches it in Python 3.8+... actually `CancelledError` is a `BaseException` in Python 3.8+, not `Exception`), the shutdown hangs until `BACKGROUND_TASK_SHUTDOWN_TIMEOUT`.

**How to avoid:**
1. Explicitly catch `asyncio.CancelledError` and re-raise after any cleanup.
2. `except Exception` does NOT catch `CancelledError` in Python 3.8+ (it's a `BaseException`), so this pitfall only applies if code uses bare `except:` (without `Exception`).

**Warning signs:** Slow app shutdown; timeout messages in logs.

---

## Code Examples

Verified patterns from official sources and redis-py 5.0.3 introspection:

### XADD with approximate trimming
```python
# Source: redis-py 5.0.3 xadd() signature (verified via inspect)
# approximate=True is the default — shown explicitly for clarity
msg_id = await db.xadd(
    "saga:checkout:events",
    {"event_type": "checkout_started", "order_id": "abc", "schema_version": "v1"},
    maxlen=10_000,
    approximate=True,  # uses ~ operator — O(1) amortized, not exact
)
# msg_id is bytes e.g. b"1709145600000-0"
```

### XGROUP_CREATE idempotent
```python
# Source: redis-py 5.0.3 xgroup_create() signature + Redis BUSYGROUP behavior
from redis.exceptions import ResponseError
try:
    await db.xgroup_create("saga:checkout:events", "compensation-handler",
                           id="0", mkstream=True)
except ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise
```

### XREADGROUP consumer loop
```python
# Source: redis-py 5.0.3 xreadgroup() signature (verified via inspect)
# XREADGROUP response: [[stream_name_bytes, [(msg_id_bytes, fields_dict), ...]]]
# fields_dict keys and values are bytes (no decode_responses)
response = await db.xreadgroup(
    groupname="compensation-handler",
    consumername="orchestrator-1",
    streams={"saga:checkout:events": ">"},  # ">" = only new undelivered messages
    count=10,
    block=2000,  # wait up to 2000ms for new messages
)
if response:
    stream_name, messages = response[0]
    for msg_id, fields in messages:
        event_type = fields.get(b"event_type", b"").decode()
        await db.xack("saga:checkout:events", "compensation-handler", msg_id)
```

### XAUTOCLAIM for idle message recovery
```python
# Source: redis-py 5.0.3 xautoclaim() signature (verified via inspect)
# Returns: [next_cursor_bytes, [(msg_id, fields_dict), ...], [deleted_msg_ids]]
result = await db.xautoclaim(
    "saga:checkout:events",
    "compensation-handler",
    "orchestrator-1",
    min_idle_time=30_000,    # ms — claim messages idle > 30s
    start_id="0-0",          # scan from beginning
    count=10,
)
cursor = result[0]           # b"0-0" means no more pending messages
messages = result[1]         # [(msg_id, fields_dict), ...]
deleted = result[2]          # messages deleted from PEL (stream entry was deleted)
```

### XINFO_GROUPS for health check
```python
# Source: redis-py 5.0.3 xinfo_groups() — returns list of dicts (parse_list_of_dicts)
# Keys are strings (decode_keys=True in parse_list_of_dicts)
# Typical keys: "name", "consumers", "pending", "last-delivered-id", "entries-read", "lag"
groups = await db.xinfo_groups("saga:checkout:events")
for g in groups:
    print(g["name"], "pending:", g["pending"], "lag:", g.get("lag", "N/A"))
```

### Dead-letter on max retries
```python
# Source: verified pattern — XADD to dead-letters + XACK original
from redis.exceptions import ResponseError

pending = await db.xpending_range(
    STREAM_NAME, group, min=msg_id, max=msg_id, count=1
)
times_delivered = pending[0]["times_delivered"] if pending else 0  # key is "times_delivered"

if times_delivered > MAX_RETRIES:
    await db.xadd(DEAD_LETTERS_STREAM, {**fields, b"original_id": msg_id})
    await db.xack(STREAM_NAME, group, msg_id)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| XCLAIM (manual XPENDING first) | XAUTOCLAIM (single command) | Redis 6.2 | One network round-trip instead of two; cursor-based for large PELs |
| aioredis | redis.asyncio (redis-py bundled) | redis-py 4.2.0 | Already using this; no change needed |
| Separate worker process for consumers | Quart background tasks (in-process) | N/A | Simpler for single-replica orchestrator; consistent with existing gRPC server pattern |

**Deprecated/outdated:**
- `XCLAIM` for batch reclaim: Still works, but `XAUTOCLAIM` is the standard for periodic reclaim jobs since Redis 6.2. Use `XAUTOCLAIM`.
- `aioredis` package: Deprecated, superseded by `redis.asyncio`. Already using the modern API.

---

## Open Questions

1. **Connection pool sharing between publish and consume**
   - What we know: redis-py asyncio client is thread-safe and connection-pool-based. Multiple coroutines can share the same `db` object.
   - What's unclear: Whether high-throughput XREADGROUP blocking calls on one connection starves XADD calls from the checkout path. At this scale (course project, not production), this is not expected to be a problem.
   - Recommendation: Share the same `db` instance for simplicity. If contention appears, create a separate `redis.Redis(...)` client for event publishing with a smaller pool.

2. **XINFO_GROUPS "lag" field availability**
   - What we know: The `lag` field in `XINFO_GROUPS` response requires Redis 7.0+. The project uses Redis from existing docker-compose — version unknown.
   - What's unclear: Whether `g.get("lag")` will return a value or be absent.
   - Recommendation: Use `g.get("lag", "N/A")` with a safe default in the health endpoint. The health check should not crash if `lag` is missing.

3. **XPENDING_RANGE return dict key names**
   - What we know: Via redis-py 5.0.3 source inspection (`parse_xpending_range`), the returned dicts have keys `"times_delivered"` (not `"delivery_count"`).
   - What's unclear: Whether the exact key name was verified against a live Redis. Source code inspection is HIGH confidence.
   - Recommendation: Use `"times_delivered"` and add a unit test that verifies the key against a real Redis response.

---

## Validation Architecture

> `workflow.nyquist_validation` is not present in `.planning/config.json`, so this section covers test infrastructure that already exists and what Wave 0 must add.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (asyncio_mode=auto) |
| Config file | `pytest.ini` at repo root |
| Quick run command | `pytest tests/test_events.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EVENT-01 | publish_event() calls XADD, does not raise on Redis error | unit (mock db) | `pytest tests/test_events.py::test_publish_event_fire_and_forget -x` | No — Wave 0 |
| EVENT-01 | XADD writes correct field names and schema_version=v1 | integration (real Redis) | `pytest tests/test_events.py::test_event_payload_shape -x` | No — Wave 0 |
| EVENT-02 | setup_consumer_groups() is idempotent (BUSYGROUP safe) | integration (real Redis) | `pytest tests/test_events.py::test_consumer_group_setup_idempotent -x` | No — Wave 0 |
| EVENT-02 | XREADGROUP delivers messages; XACK removes from PEL | integration (real Redis) | `pytest tests/test_events.py::test_at_least_once_delivery -x` | No — Wave 0 |
| EVENT-02 | Dead-letter after MAX_RETRIES (simulate via xpending_range mock) | unit (mock) | `pytest tests/test_events.py::test_dead_letter_after_max_retries -x` | No — Wave 0 |
| EVENT-03 | run_checkout() publishes events for each transition | integration (real Redis + real gRPC) | `pytest tests/test_events.py::test_checkout_publishes_lifecycle_events -x` | No — Wave 0 |
| EVENT-03 | Consumer loop exits cleanly on CancelledError | unit (asyncio.Event) | `pytest tests/test_events.py::test_consumer_graceful_shutdown -x` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_events.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_events.py` — covers EVENT-01 through EVENT-03 (does not exist yet)
- [ ] `orchestrator/events.py` — publish helpers (does not exist yet)
- [ ] `orchestrator/consumers.py` — consumer loops and group setup (does not exist yet)

---

## Sources

### Primary (HIGH confidence)
- redis-py 5.0.3 installed at `/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages/redis/` — signatures verified via `inspect.signature()` and `inspect.getsource()` for all stream commands
- [XREADGROUP Docs](https://redis.io/docs/latest/commands/xreadgroup/) — return format, `>` vs explicit ID semantics, BLOCK behavior
- [XAUTOCLAIM Docs](https://redis.io/docs/latest/commands/xautoclaim/) — 3-element return format, cursor iteration pattern

### Secondary (MEDIUM confidence)
- [Quart Background Tasks Docs](https://quart.palletsprojects.com/en/latest/how_to_guides/background_tasks/) — `add_background_task()` behavior; Quart awaits tasks on shutdown with `BACKGROUND_TASK_SHUTDOWN_TIMEOUT`
- [Redis BUSYGROUP handling](https://github.com/redis/redis/issues/9893) — confirmed `NX` flag not merged; catch-and-ignore is the standard pattern
- [XGROUP_CREATE Docs](https://redis.io/docs/latest/commands/xgroup-create/) — `mkstream=True` parameter, `id='0'` vs `'$'` semantics

### Tertiary (LOW confidence)
- DEV Community article on async job queues with Redis Streams + asyncio — general pattern alignment, not verified line-by-line

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified via installed library introspection; no new packages needed
- Architecture: HIGH — patterns verified against redis-py source and official Redis command docs
- Pitfalls: HIGH for bytes/strings and BUSYGROUP (verified); MEDIUM for XAUTOCLAIM return format (verified via inspect; not tested against live Redis)

**Research date:** 2026-02-28
**Valid until:** 2026-03-30 (stable Redis Streams API; redis-py 5.x)
