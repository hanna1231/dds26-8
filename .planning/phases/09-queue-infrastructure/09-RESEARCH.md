# Phase 9: Queue Infrastructure - Research

**Researched:** 2026-03-12
**Domain:** Redis Streams request/reply messaging with consumer groups, correlation ID routing, asyncio integration
**Confidence:** HIGH

## Summary

Phase 9 builds Redis Streams-based request/reply messaging between the orchestrator and domain services (Stock, Payment). The project already uses Redis Streams for SAGA lifecycle events (fire-and-forget via `events.py` and consumer processing via `consumers.py`), so the team has proven experience with XADD, XREADGROUP, XACK, and consumer groups. The new requirement is a **request/reply** pattern where the orchestrator sends a command and waits for a correlated reply -- this is fundamentally different from the existing fire-and-forget event publishing.

The architecture requires three components: (1) per-service command streams where the orchestrator publishes commands, (2) consumer workers in Stock/Payment that read commands and dispatch to operations module functions, and (3) a shared reply stream that the orchestrator reads, routing replies to waiting asyncio.Future objects via correlation IDs. The operations modules (extracted in Phase 8) already return plain dicts -- perfect for JSON serialization over streams.

**Primary recommendation:** Use `msgspec.json` for stream message serialization (already in use), one command stream per service (`{queue}:stock:commands`, `{queue}:payment:commands`), one reply stream (`{queue}:replies`) read by the orchestrator, and `asyncio.Future` objects keyed by correlation ID for request/reply correlation.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MQC-01 | Redis Streams command streams per service with consumer group processing | Per-service command streams with hash-tagged keys for Redis Cluster; consumer groups with XREADGROUP/XACK; existing `consumers.py` pattern proven |
| MQC-02 | Shared reply stream with correlation ID routing and asyncio.Future resolution | Single reply stream read by orchestrator reply-listener task; dict of `{correlation_id: asyncio.Future}` for request/reply pairing; timeout via `asyncio.wait_for` |
| MQC-03 | Queue consumer workers in Stock and Payment services dispatching to operations modules | Background task consumer loops (same pattern as `consumers.py`); deserialize command, call `operations.reserve_stock()` etc., serialize result to reply stream |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| redis.asyncio | 5.0.3 (already installed) | Redis Streams XADD/XREADGROUP/XACK | Already used for all Redis operations in the project |
| msgspec.json | (already installed) | JSON serialization for stream messages | Already used in `events.py` for event field encoding; fast C-based encoder |
| asyncio | stdlib | Future-based request/reply correlation, background tasks | Already used throughout for async patterns |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uuid | stdlib | Generate correlation IDs | Every queue command needs a unique correlation ID |
| logging | stdlib | Consumer loop diagnostics | Same pattern as existing `consumers.py` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| msgspec.json | msgspec.msgpack | JSON is human-readable in Redis streams, easier debugging; msgpack is faster but binary. JSON preferred for stream fields since Redis stores them as strings anyway |
| Single reply stream | Per-request reply stream | Per-request creates/destroys streams constantly, wasteful; single shared reply is simpler and sufficient for throughput |
| asyncio.Future | asyncio.Queue per request | Future is lighter-weight for single-value resolution; Queue adds unnecessary complexity |

**Installation:**
```bash
# No new packages needed -- all dependencies already installed
```

## Architecture Patterns

### Recommended Project Structure
```
orchestrator/
  queue_client.py      # Queue-based replacements for client.py functions (send command, await reply)
  reply_listener.py    # Background task: reads reply stream, resolves Futures by correlation ID

stock/
  queue_consumer.py    # Background task: reads command stream, dispatches to operations module

payment/
  queue_consumer.py    # Background task: reads command stream, dispatches to operations module
```

### Pattern 1: Command Stream Per Service
**What:** Each domain service has a dedicated command stream. The orchestrator XADDs commands to the appropriate stream. Consumer groups in each service process commands.
**When to use:** Always -- this is the core messaging pattern.
**Stream naming with Redis Cluster hash tags:**
```
{queue}:stock:commands     # All queue streams share {queue} hash tag
{queue}:payment:commands   # -> same Redis Cluster slot
{queue}:replies            # -> same Redis Cluster slot
```
**Why hash tags:** All stream keys must land on the same Redis Cluster node because XREADGROUP operates on a single key. Using `{queue}` prefix ensures all queue streams hash to the same slot, which is required since the orchestrator reads replies and the services read commands from the same cluster.

**IMPORTANT:** The orchestrator uses a SEPARATE Redis cluster connection from Stock/Payment. The orchestrator connects to its own Redis cluster (`orchestrator-redis`), while Stock connects to `stock-redis` and Payment to `payment-redis`. For queue streams, a SHARED Redis connection is needed -- either the orchestrator's Redis or a dedicated queue Redis. The simplest approach: use the orchestrator's Redis for all queue streams, and give Stock/Payment a second Redis connection to the orchestrator's Redis for queue operations only.

**Example:**
```python
# Command message format (XADD fields)
command_fields = {
    "correlation_id": "uuid-1234",
    "command": "reserve_stock",
    "payload": '{"item_id": "abc", "quantity": 2, "idempotency_key": "saga:key"}',
}
await db.xadd("{queue}:stock:commands", command_fields)
```

### Pattern 2: Shared Reply Stream with Correlation ID Routing
**What:** All service replies go to a single reply stream. The orchestrator runs a background listener that reads replies and resolves the matching asyncio.Future.
**When to use:** Always -- this is the reply half of request/reply.
**Example:**
```python
# Orchestrator side: send command and await reply
import asyncio
import uuid

_pending_replies: dict[str, asyncio.Future] = {}

async def send_command(queue_db, stream: str, command: str, payload: dict, timeout: float = 5.0) -> dict:
    correlation_id = str(uuid.uuid4())
    future = asyncio.get_event_loop().create_future()
    _pending_replies[correlation_id] = future

    await queue_db.xadd(stream, {
        "correlation_id": correlation_id,
        "command": command,
        "payload": msgspec.json.encode(payload).decode(),
    })

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"success": False, "error_message": "queue timeout"}
    finally:
        _pending_replies.pop(correlation_id, None)
```

### Pattern 3: Consumer Worker in Domain Services
**What:** Each domain service runs a background task that reads its command stream via XREADGROUP, deserializes the command, dispatches to the operations module, and publishes the result to the reply stream.
**When to use:** Stock and Payment services.
**Example:**
```python
# stock/queue_consumer.py
import operations
import msgspec.json

COMMAND_DISPATCH = {
    "reserve_stock": lambda db, p: operations.reserve_stock(db, p["item_id"], p["quantity"], p["idempotency_key"]),
    "release_stock": lambda db, p: operations.release_stock(db, p["item_id"], p["quantity"], p["idempotency_key"]),
    "check_stock": lambda db, p: operations.check_stock(db, p["item_id"]),
}

async def process_command(db, queue_db, command: str, payload: dict, correlation_id: str):
    handler = COMMAND_DISPATCH.get(command)
    if handler is None:
        result = {"success": False, "error_message": f"unknown command: {command}"}
    else:
        result = await handler(db, payload)

    # Publish reply
    await queue_db.xadd("{queue}:replies", {
        "correlation_id": correlation_id,
        "result": msgspec.json.encode(result).decode(),
    })
```

### Pattern 4: Reply Listener (Orchestrator Background Task)
**What:** A continuously running asyncio task in the orchestrator that reads from the reply stream and resolves pending Futures.
**When to use:** Orchestrator service only.
**Example:**
```python
# orchestrator/reply_listener.py
async def reply_listener(queue_db, stop_event: asyncio.Event):
    group = "orchestrator-replies"
    consumer = "orchestrator-1"
    stream = "{queue}:replies"

    # Create consumer group
    try:
        await queue_db.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP

    while not stop_event.is_set():
        response = await queue_db.xreadgroup(
            groupname=group, consumername=consumer,
            streams={stream: ">"}, count=50, block=1000,
        )
        if response:
            for _stream_name, messages in response:
                for msg_id, fields in messages:
                    cid = fields[b"correlation_id"].decode()
                    result_json = fields[b"result"].decode()
                    result = msgspec.json.decode(result_json.encode())

                    future = _pending_replies.get(cid)
                    if future and not future.done():
                        future.set_result(result)

                    await queue_db.xack(stream, group, msg_id)
```

### Anti-Patterns to Avoid
- **Using XREAD instead of XREADGROUP for consumers:** Loses at-least-once delivery guarantees. Always use consumer groups with XREADGROUP + XACK.
- **Creating per-request reply streams:** Massive stream churn, hard to clean up. Use a single shared reply stream with correlation IDs.
- **Blocking on XREAD in the main event loop:** Use background tasks with `app.add_background_task()` (Quart pattern already established).
- **Forgetting to ACK messages:** Messages stay in PEL forever, causing memory leaks. Always XACK after processing.
- **Using the same Redis connection for queue streams and domain data:** Stock/Payment each have their own Redis Cluster. Queue streams need a shared Redis that all services can access.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Correlation ID tracking | Custom event emitter/callback system | `dict[str, asyncio.Future]` with `asyncio.wait_for` | Standard asyncio pattern, handles timeout and cancellation correctly |
| Message serialization | Custom wire format | `msgspec.json.encode/decode` | Already used in project, fast, handles dicts natively |
| Consumer group management | Manual offset tracking | Redis XREADGROUP + XACK | Redis handles PEL, delivery counting, and consumer tracking |
| At-least-once delivery | Manual retry/dedup logic | XREADGROUP PEL + XAUTOCLAIM | Redis Streams has built-in pending entry tracking and idle message reclamation |

**Key insight:** Redis Streams consumer groups already provide the reliability primitives (PEL, XACK, XAUTOCLAIM). The only custom piece is the correlation ID -> Future mapping for request/reply, which is a straightforward dict lookup.

## Common Pitfalls

### Pitfall 1: Redis Cluster Hash Slot Routing
**What goes wrong:** Stream keys on different hash slots route to different nodes. XREADGROUP on a stream that doesn't exist on the target node fails silently or errors.
**Why it happens:** Redis Cluster partitions keys by hash slot. Without hash tags, `stock:commands` and `payment:commands` may land on different nodes.
**How to avoid:** Use `{queue}:` hash tag prefix on ALL queue stream keys so they all route to the same slot. This is the same pattern used for `{saga:*}` keys.
**Warning signs:** "MOVED" errors or empty reads from XREADGROUP.

### Pitfall 2: Separate Redis Clusters per Service
**What goes wrong:** Stock uses `stock-redis`, Payment uses `payment-redis`, Orchestrator uses `orchestrator-redis` (from `docker-compose.yml`). Queue streams must be readable by both producer and consumer.
**Why it happens:** The existing architecture uses per-domain Redis Clusters for data isolation.
**How to avoid:** Queue streams live on a shared Redis instance. Options: (a) use the orchestrator's Redis for queue streams -- Stock/Payment get an additional connection to orchestrator-redis, or (b) dedicate a separate queue Redis. Option (a) is simpler since orchestrator already has a Redis cluster. Stock/Payment need a `QUEUE_REDIS_HOST` env var pointing to orchestrator's Redis.
**Warning signs:** Consumer reads return empty because it's connected to the wrong Redis.

### Pitfall 3: Future Leaked on Timeout or Crash
**What goes wrong:** If the orchestrator sends a command but the consumer never replies (crash, bug), the asyncio.Future hangs forever unless timeout is set.
**Why it happens:** No built-in timeout on Future.set_result.
**How to avoid:** Always use `asyncio.wait_for(future, timeout=5.0)` and clean up the pending dict in a `finally` block. Match the timeout to the existing `RPC_TIMEOUT = 5.0` in `client.py`.
**Warning signs:** Hanging checkout requests.

### Pitfall 4: Bytes vs Strings in Redis Stream Fields
**What goes wrong:** redis-py with `decode_responses=False` returns bytes for both field names and values in XREADGROUP responses. Forgetting to decode causes KeyError or comparison failures.
**Why it happens:** The project uses `decode_responses=False` everywhere (confirmed in `app.py` for all services).
**How to avoid:** Always `.decode()` stream field keys and values. The existing `consumers.py` already handles this correctly (see `fields.get(b"event_type", b"").decode()`).
**Warning signs:** KeyError on `fields["correlation_id"]` -- should be `fields[b"correlation_id"]`.

### Pitfall 5: Consumer Group Must Exist Before XREADGROUP
**What goes wrong:** XREADGROUP fails with NOGROUP error if the consumer group hasn't been created.
**Why it happens:** Consumer groups are not auto-created.
**How to avoid:** Call `xgroup_create(..., mkstream=True)` at service startup, catching the `BUSYGROUP` response error (same pattern as `consumers.py:setup_consumer_groups()`).
**Warning signs:** `ResponseError: NOGROUP` on first XREADGROUP call.

### Pitfall 6: Message Ordering in Consumer Groups
**What goes wrong:** If a consumer group has multiple consumers, messages may be processed out of order across consumers. For SAGA checkout, stock reserve for item A might complete before item B even though B was sent first.
**Why it happens:** Consumer groups distribute messages across consumers for parallel processing.
**How to avoid:** Use a single consumer per group in each service instance (which is what we want -- `stock-consumer-1`). Multiple service replicas each have their own consumer name. Order within a single consumer is guaranteed.
**Warning signs:** Race conditions in multi-step operations.

## Code Examples

Verified patterns from the existing codebase:

### Existing Consumer Group Setup (from consumers.py)
```python
# Source: orchestrator/consumers.py lines 32-41
async def setup_consumer_groups(db) -> None:
    for group in CONSUMER_GROUPS:
        try:
            await db.xgroup_create(
                STREAM_NAME, group, id="0", mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
```

### Existing XREADGROUP Loop (from consumers.py)
```python
# Source: orchestrator/consumers.py lines 62-78
response = await db.xreadgroup(
    groupname=group, consumername=consumer_name,
    streams={STREAM_NAME: ">"}, count=BATCH_SIZE,
    block=POLL_INTERVAL_MS,
)
if response:
    for _stream, messages in response:
        for msg_id, fields in messages:
            await _handle_message(db, group, msg_id, fields)
```

### Queue Command Message Format
```python
# Command message (orchestrator -> service)
{
    "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
    "command": "reserve_stock",  # maps to operations module function name
    "payload": '{"item_id": "abc", "quantity": 2, "idempotency_key": "{saga:order123}:step:reserve:abc"}'
}

# Reply message (service -> orchestrator)
{
    "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
    "result": '{"success": true, "error_message": ""}'
}
```

### Queue Client Functions (replacing orchestrator/client.py for queue mode)
```python
# orchestrator/queue_client.py
# These functions have IDENTICAL signatures to client.py functions
# so the SAGA orchestrator can call them interchangeably

async def reserve_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    return await send_command(
        queue_db,
        "{queue}:stock:commands",
        "reserve_stock",
        {"item_id": item_id, "quantity": quantity, "idempotency_key": idempotency_key},
    )

async def release_stock(item_id: str, quantity: int, idempotency_key: str) -> dict:
    return await send_command(
        queue_db,
        "{queue}:stock:commands",
        "release_stock",
        {"item_id": item_id, "quantity": quantity, "idempotency_key": idempotency_key},
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Polling XREAD without consumer groups | XREADGROUP with consumer groups | Redis 5.0+ (2018) | Reliable delivery, PEL tracking, multi-consumer support |
| Manual message ID tracking | XAUTOCLAIM for idle message recovery | Redis 6.2+ (2021) | Automatic reclaim of crashed consumer messages |
| Separate message broker (RabbitMQ/Kafka) | Redis Streams (same infra) | Project decision | No new infrastructure, same redis-py client |

**Deprecated/outdated:**
- `aioredis` library: Merged into `redis-py` 4.2+. The project correctly uses `redis.asyncio`.

## Open Questions

1. **Which Redis instance hosts queue streams?**
   - What we know: Each service has its own Redis Cluster. Queue streams need a shared Redis accessible to all services.
   - What's unclear: Whether to use orchestrator's Redis (simplest) or a dedicated queue Redis (cleanest separation).
   - Recommendation: Use orchestrator's Redis for queue streams in Phase 9. Stock/Payment get an additional env var `QUEUE_REDIS_HOST` pointing to orchestrator's Redis. This avoids adding new infrastructure.

2. **Stream trimming strategy for command/reply streams?**
   - What we know: Event streams use `maxlen=10_000` with `approximate=True` (from `events.py`).
   - What's unclear: Optimal maxlen for command/reply streams. Commands are processed and ACKed quickly, so entries accumulate less than events.
   - Recommendation: Use `maxlen=1_000, approximate=True` for command streams and reply stream. Commands are consumed within seconds, so 1K entries is more than sufficient.

3. **How does SAGA checkout wire to queue transport in this phase?**
   - What we know: Phase 9 success criteria says "manual wiring, no toggle yet." Phase 10 adds the transport adapter and COMM_MODE toggle.
   - What's unclear: Whether to modify `grpc_server.py:run_checkout()` directly or create a parallel entry point.
   - Recommendation: Create a separate test/integration path that imports `queue_client` instead of `client` for Phase 9 validation. The actual toggle wiring happens in Phase 10.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (already configured) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MQC-01 | Command streams with consumer group processing | integration | `pytest tests/test_queue_infrastructure.py::test_command_stream_consumer_group -x` | No - Wave 0 |
| MQC-01 | XADD command to service stream | unit | `pytest tests/test_queue_infrastructure.py::test_xadd_command -x` | No - Wave 0 |
| MQC-02 | Reply stream with correlation ID routing | integration | `pytest tests/test_queue_infrastructure.py::test_reply_correlation -x` | No - Wave 0 |
| MQC-02 | Timeout on missing reply | unit | `pytest tests/test_queue_infrastructure.py::test_reply_timeout -x` | No - Wave 0 |
| MQC-03 | Stock consumer dispatches to operations | integration | `pytest tests/test_queue_infrastructure.py::test_stock_consumer_dispatch -x` | No - Wave 0 |
| MQC-03 | Payment consumer dispatches to operations | integration | `pytest tests/test_queue_infrastructure.py::test_payment_consumer_dispatch -x` | No - Wave 0 |
| MQC-03 | SAGA checkout over queue transport | integration | `pytest tests/test_queue_infrastructure.py::test_saga_checkout_over_queue -x` | No - Wave 0 |
| MQC-03 | Consumer ACK after processing | unit | `pytest tests/test_queue_infrastructure.py::test_consumer_ack -x` | No - Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_queue_infrastructure.py -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_queue_infrastructure.py` -- covers MQC-01, MQC-02, MQC-03
- [ ] Test fixtures for queue Redis connection, stream setup/teardown
- [ ] No new framework install needed -- pytest-asyncio already configured

## Sources

### Primary (HIGH confidence)
- Existing codebase: `orchestrator/consumers.py`, `orchestrator/events.py` -- proven Redis Streams patterns with XREADGROUP, XADD, XACK, consumer groups
- Existing codebase: `stock/operations.py`, `payment/operations.py` -- operations modules return plain dicts, ready for queue dispatch
- Existing codebase: `orchestrator/client.py` -- defines the function signatures that queue_client.py must match
- [Redis Streams documentation](https://redis.io/docs/latest/develop/data-types/streams/) -- authoritative reference for consumer groups, PEL, XAUTOCLAIM
- [redis-py Stream Examples](https://redis.readthedocs.io/en/stable/examples/redis-stream-example.html) -- XADD, XREADGROUP, XACK API signatures

### Secondary (MEDIUM confidence)
- [XREADGROUP command reference](https://redis.io/docs/latest/commands/xreadgroup/) -- consumer group routing in cluster mode
- [Redis Cluster hash slot routing for streams](https://github.com/redis/lettuce/issues/2140) -- hash tag requirements confirmed

### Tertiary (LOW confidence)
- None -- all patterns verified against existing codebase and official docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries needed, all patterns proven in existing codebase
- Architecture: HIGH -- request/reply over Redis Streams is well-documented; project already has consumer group infrastructure
- Pitfalls: HIGH -- identified from codebase analysis (bytes handling, separate Redis clusters, hash tags) and official docs

**Research date:** 2026-03-12
**Valid until:** 2026-04-12 (stable technology, no fast-moving dependencies)
