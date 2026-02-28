# Phase 1: Async Foundation - Research

**Researched:** 2026-02-28
**Domain:** Flask→Quart migration, redis.asyncio, Uvicorn ASGI
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Async HTTP client:**
- Claude's choice on library (httpx, aiohttp, or similar) — this is temporary, replaced by gRPC in Phase 2
- Checkout flow remains sequential (subtract stock → pay → confirm), not concurrent
- Use a shared async HTTP client per service (connection pooling, initialized at startup, closed on shutdown)

**Uvicorn configuration:**
- Claude's discretion on worker model (single process vs multiple workers) and timeout settings
- Claude's discretion on per-service vs shared requirements.txt — decide based on dependency differences (Order needs async HTTP client, others don't)

**Code structure:**
- Keep single `app.py` per service — no module splitting (services are small, Phase 2+ adds structure naturally)
- Minimal diff: swap Flask imports for Quart, add `async def` to route handlers, no style changes or type hint additions
- Keep msgspec for serialization (Struct models, msgpack encoding) — no change
- Claude's discretion on Redis client lifecycle (module-level vs Quart before_serving/after_serving hooks)

**Error & response format:**
- Preserve exact mix of JSON (`jsonify`) and plain text (`Response`) per endpoint — no standardization
- Claude investigates what tests/benchmark actually check and matches error format accordingly
- Keep `__main__` dev mode with Quart/Uvicorn equivalent for local development outside Docker
- Claude's discretion on logging — swap gunicorn logger references for uvicorn equivalents, no format changes

### Claude's Discretion
- Async HTTP client library choice (temporary bridge to Phase 2 gRPC)
- Uvicorn worker model and timeout configuration
- Redis client initialization pattern (module-level vs app lifecycle hooks)
- Requirements.txt organization (shared vs per-service)
- Logging setup (uvicorn equivalent of current gunicorn logger)
- Error response format strictness (based on test suite analysis)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ASYNC-01 | All three services (Order, Stock, Payment) run on Quart+Uvicorn instead of Flask+Gunicorn | Flask→Quart import swap, `async def` route handlers, Uvicorn command in docker-compose |
| ASYNC-02 | Redis operations use async redis-py client (`redis.asyncio`) with hiredis acceleration | `redis.asyncio.Redis`, `pip install redis[hiredis]`, `before_serving`/`after_serving` lifecycle hooks |
| ASYNC-03 | All existing API endpoints preserve identical routes and response formats after migration | Route paths unchanged, `jsonify`/`Response` preserved, abort() behavior identical, test suite validates |
</phase_requirements>

---

## Summary

Phase 1 is a mechanical framework swap: Flask (WSGI/sync) → Quart (ASGI/async), Gunicorn → Uvicorn, sync `redis.Redis` → `redis.asyncio.Redis`. The codebase is minimal — three `app.py` files totaling ~350 lines combined — and none of the routes perform CPU-heavy work. Every route makes Redis calls and the order service additionally makes HTTP calls via `requests`.

The migration is well-defined. Quart is API-compatible with Flask — the import swap is the primary change. Adding `async def` to all route handlers and awaiting Redis calls accounts for 90% of the diff. The two non-trivial decisions are: (1) how to manage the async Redis client lifecycle (module-level fails because no event loop exists at import time; `before_serving`/`after_serving` hooks are the correct pattern), and (2) which async HTTP client to use for Order service's inter-service calls (`httpx.AsyncClient` is recommended — same encode ecosystem as Uvicorn, well-maintained, familiar API).

The test suite (`test/test_microservices.py`) uses standard `requests` against the gateway on port 8000. It checks JSON shapes and HTTP status codes, not raw response bodies. The `Response(text, status=200)` plain-text endpoints are only checked via `.status_code` — body content is not asserted. This means response format preservation is satisfied as long as status codes and JSON keys are unchanged.

**Primary recommendation:** Swap imports, add `async def` + `await` throughout, use `@app.before_serving`/`@app.after_serving` for Redis client lifecycle, replace `requests` with `httpx.AsyncClient`, replace `gunicorn` command with `uvicorn app:app --host 0.0.0.0 --port 5000 --workers 2`.

---

## Codebase Audit

Existing code analysis (critical input for planning):

### Current stack per service

| Service | File | Lines | Redis ops | HTTP ops | Sync patterns to remove |
|---------|------|-------|-----------|----------|------------------------|
| order | `order/app.py` | ~183 | `db.get`, `db.set`, `db.mset` | `requests.get`, `requests.post` | `redis.Redis`, `requests`, `atexit`, gunicorn logger |
| stock | `stock/app.py` | ~117 | `db.get`, `db.set`, `db.mset` | none | `redis.Redis`, `atexit`, gunicorn logger |
| payment | `payment/app.py` | ~114 | `db.get`, `db.set`, `db.mset` | none | `redis.Redis`, `atexit`, gunicorn logger |

### Current gunicorn command (docker-compose.yml)
```
gunicorn -b 0.0.0.0:5000 -w 2 --timeout 30 --log-level=info app:app
```
Services bind to `:5000`, gateway (nginx) proxies on `:5000`. Port must stay 5000.

### Test suite behavior (test/test_microservices.py + test/utils.py)
- Hits gateway at `http://127.0.0.1:8000` via nginx
- Checks JSON keys: `item_id`, `stock`, `price`, `user_id`, `credit`, `order_id`, `paid`, `items`, `total_cost`
- Checks status code ranges: `200-299` for success, `400-499` for failure
- Does NOT assert response body text for `Response(text, 200)` endpoints
- Conclusion: plain-text Response bodies can be preserved as-is (no risk from status-code-only assertions)

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| quart | 0.20.1 | Async Flask replacement, ASGI framework | Flask API-compatible, by same maintainers, drop-in for WSGI→ASGI migration |
| uvicorn | latest (0.34.x) | ASGI server replacing Gunicorn | Lightweight, standard ASGI runner, same encode ecosystem |
| redis | 5.0.x (existing) | Provides `redis.asyncio` submodule | Already in requirements; asyncio support added in 4.2+, no upgrade needed |
| hiredis | 3.x | C-extension parser for redis-py | Auto-used when installed via `redis[hiredis]`, zero code changes |
| httpx | 0.27.x | Async HTTP client for Order service inter-service calls | Maintained by encode (Uvicorn/Starlette authors), async-native, familiar API |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| msgspec | 0.18.6 (existing) | Struct serialization — no change | Unchanged across migration |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| httpx | aiohttp | aiohttp is fine but heavier; httpx is lighter and encode-ecosystem aligned |
| httpx | aiohttp | Both are temporary (replaced by gRPC in Phase 2) — httpx preferred for simplicity |
| uvicorn standalone | gunicorn + uvicorn workers | Gunicorn+UvicornWorker is marginally more robust for process management but adds complexity; standalone uvicorn with `--workers` is sufficient for this phase |

**Installation (per service):**
```bash
# Stock and Payment (no HTTP client needed):
pip install quart uvicorn redis[hiredis] msgspec

# Order (needs async HTTP client):
pip install quart uvicorn redis[hiredis] msgspec httpx
```

---

## Architecture Patterns

### Recommended Project Structure

No structural change — keep single `app.py` per service per CONTEXT.md decision.

```
order/
├── app.py          # Quart app (was Flask)
├── requirements.txt
└── Dockerfile

stock/
├── app.py          # Quart app (was Flask)
├── requirements.txt
└── Dockerfile

payment/
├── app.py          # Quart app (was Flask)
├── requirements.txt
└── Dockerfile
```

### Pattern 1: Import Swap

**What:** Replace Flask with Quart imports — everything else keeps the same name.
**When to use:** First change in every `app.py`.

```python
# BEFORE
from flask import Flask, jsonify, abort, Response

# AFTER
from quart import Quart, jsonify, abort, Response

# BEFORE
app = Flask("order-service")

# AFTER
app = Quart("order-service")
```

Source: [Quart Flask Migration Guide](https://quart.palletsprojects.com/en/latest/how_to_guides/flask_migration/)

### Pattern 2: Async Route Handlers

**What:** All route functions must be `async def`; Redis awaits added.
**When to use:** Every `@app.get` / `@app.post` decorated function.

```python
# BEFORE
@app.get('/find/<item_id>')
def find_item(item_id: str):
    item_entry: StockValue = get_item_from_db(item_id)
    return jsonify({"stock": item_entry.stock, "price": item_entry.price})

# AFTER
@app.get('/find/<item_id>')
async def find_item(item_id: str):
    item_entry: StockValue = await get_item_from_db(item_id)
    return jsonify({"stock": item_entry.stock, "price": item_entry.price})
```

Note: Helper functions called from routes must also become `async def` with `await`.

### Pattern 3: Redis Client Lifecycle via before_serving/after_serving

**What:** Initialize async Redis client inside Quart's lifecycle hooks, not at module level.
**Why:** `redis.asyncio.Redis` created at import time (module level) fails — there is no running event loop at that point. Quart lifecycle hooks run inside the event loop.
**When to use:** Replace `redis.Redis(...)` module-level declaration and `atexit.register`.

```python
# BEFORE (sync, module-level — breaks with asyncio)
import redis
db: redis.Redis = redis.Redis(host=os.environ['REDIS_HOST'], ...)
def close_db_connection():
    db.close()
atexit.register(close_db_connection)

# AFTER (async, lifecycle hooks)
import redis.asyncio as redis

db: redis.Redis = None  # placeholder

@app.before_serving
async def startup():
    global db
    db = redis.Redis(
        host=os.environ['REDIS_HOST'],
        port=int(os.environ['REDIS_PORT']),
        password=os.environ['REDIS_PASSWORD'],
        db=int(os.environ['REDIS_DB'])
    )

@app.after_serving
async def shutdown():
    await db.aclose()
```

Source: [Quart Startup/Shutdown Guide](https://quart.palletsprojects.com/en/latest/how_to_guides/startup_shutdown/), [redis.asyncio examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)

### Pattern 4: Async Redis Operations

**What:** All Redis calls become `await`-ed.
**When to use:** Every `db.get`, `db.set`, `db.mset` call.

```python
# BEFORE
entry: bytes = db.get(order_id)
db.set(key, value)
db.mset(kv_pairs)

# AFTER
entry: bytes = await db.get(order_id)
await db.set(key, value)
await db.mset(kv_pairs)
```

Exception handling: `redis.exceptions.RedisError` works identically in async client.

### Pattern 5: Shared httpx AsyncClient (Order service only)

**What:** Replace `requests` with a shared `httpx.AsyncClient` initialized at startup.
**When to use:** Order service only — `send_post_request` and `send_get_request` helpers.

```python
# BEFORE
import requests

def send_post_request(url: str):
    try:
        response = requests.post(url)
    except requests.exceptions.RequestException:
        abort(400, REQ_ERROR_STR)
    return response

# AFTER
import httpx

http_client: httpx.AsyncClient = None

@app.before_serving
async def startup():
    global db, http_client
    db = redis.Redis(...)
    http_client = httpx.AsyncClient()

@app.after_serving
async def shutdown():
    await db.aclose()
    await http_client.aclose()

async def send_post_request(url: str):
    try:
        response = await http_client.post(url)
    except httpx.RequestError:
        abort(400, REQ_ERROR_STR)
    return response

async def send_get_request(url: str):
    try:
        response = await http_client.get(url)
    except httpx.RequestError:
        abort(400, REQ_ERROR_STR)
    return response
```

Source: [httpx Async Support](https://www.python-httpx.org/async/)

### Pattern 6: Uvicorn command replacing Gunicorn

**What:** Replace gunicorn command in docker-compose.yml.
**When to use:** Each service's `command:` in docker-compose.

```yaml
# BEFORE
command: gunicorn -b 0.0.0.0:5000 -w 2 --timeout 30 --log-level=info app:app

# AFTER
command: uvicorn app:app --host 0.0.0.0 --port 5000 --workers 2 --log-level info
```

Source: [Uvicorn Deployment](https://www.uvicorn.org/deployment/)

### Pattern 7: Dev mode `__main__` block

**What:** Replace Flask dev server with Uvicorn programmatic run.
**When to use:** `if __name__ == '__main__':` block in each app.py.

```python
# BEFORE
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)

# AFTER — option A: use Quart's built-in (Hypercorn-backed) dev server
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)

# AFTER — option B: run via uvicorn programmatically
if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
```

Recommendation: Option A (Quart's `app.run()`) for simplicity — it uses Hypercorn internally, which is ASGI-compatible. Option B if you want parity with production server.

### Pattern 8: Logging — replace gunicorn logger reference

**What:** Remove the gunicorn logger wiring in the `else:` block.
**When to use:** The `else:` block at bottom of each `app.py`.

```python
# BEFORE
else:
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

# AFTER — remove the else block entirely.
# Quart's app.logger uses standard Python logging.
# Uvicorn handles its own logging via --log-level flag.
# No wiring needed: app.logger.debug() works out of the box.
```

Source: Quart uses standard Python loggers named `quart.app` and `quart.serving`. No handler wiring required when running under Uvicorn.

### Anti-Patterns to Avoid

- **Module-level async Redis client:** `db = redis.asyncio.Redis(...)` at import time will raise `RuntimeError: no running event loop`. Always initialize inside `@app.before_serving`.
- **`atexit.register` with async cleanup:** `atexit` callbacks are sync; they cannot `await db.aclose()`. Replace entirely with `@app.after_serving`.
- **`requests` inside async routes:** Blocks the event loop. Replace with `httpx.AsyncClient`. Even a single sync I/O call inside `async def` blocks all concurrent requests.
- **New `httpx.AsyncClient()` per request:** Creating a new client per request defeats connection pooling. One shared client, initialized at startup.
- **`--reload` with `--workers`:** Uvicorn disallows these together. Use `--reload` for dev, `--workers` for production.
- **Missing `await` on Redis calls:** Python will not error immediately — it returns a coroutine object, not the result. The bug is silent until a type error downstream. Search for `RuntimeWarning: coroutine 'XX' was never awaited`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async Redis connection pool | Custom pool manager | `redis.asyncio.Redis` with default pool | redis-py manages pool internally; hiredis auto-detected |
| Async HTTP client | Custom aiohttp wrapper | `httpx.AsyncClient` | Connection pooling, timeout, error handling built-in |
| ASGI server | Custom asyncio server | Uvicorn | HTTP/1.1, HTTP/2, lifespan protocol all handled |
| Service lifecycle hooks | `atexit` with asyncio | `@app.before_serving` / `@app.after_serving` | Quart's ASGI lifespan hooks run inside the event loop |

**Key insight:** The entire migration is swapping library calls. Nothing custom needs building. Every "hard" problem (connection pooling, event loop integration, process management) is solved by the standard stack.

---

## Common Pitfalls

### Pitfall 1: Module-level `redis.asyncio.Redis` initialization
**What goes wrong:** `RuntimeError: no running event loop` on service startup, before any request is served.
**Why it happens:** Python executes module-level code synchronously at import time. `redis.asyncio.Redis` needs an event loop for certain internal setup. Even if it doesn't fail immediately, the connection is created outside the app's event loop.
**How to avoid:** Always initialize `redis.asyncio.Redis` inside `@app.before_serving`.
**Warning signs:** `RuntimeError: no running event loop` in startup logs; or connection errors on first request.

### Pitfall 2: Forgetting to await helper functions
**What goes wrong:** Helper functions like `get_order_from_db` also call Redis — they must be `async def` and awaited by callers.
**Why it happens:** Route handler is correctly `async def`, but calls a non-async helper that contains Redis calls. The helper can't `await` without being `async`.
**How to avoid:** Make every function that touches `db` into `async def`. Trace the full call graph.
**Warning signs:** `RuntimeWarning: coroutine 'get_order_from_db' was never awaited`; routes return None or wrong type.

### Pitfall 3: `abort()` inside async context
**What goes wrong:** In current code, `abort()` is called directly in helper functions (e.g., `return abort(400, DB_ERROR_STR)`). In Quart, `abort()` raises an `HTTPException` — this behavior is identical to Flask and does NOT need `await`. But if code is restructured to use `await abort()`, it will fail.
**How to avoid:** Keep `abort()` calls as-is — no `await` needed. Quart's `abort()` is synchronous (raises exception).
**Warning signs:** `TypeError: object NoneType can't be used in 'await' expression`.

### Pitfall 4: `requests.exceptions.RequestException` vs `httpx.RequestError`
**What goes wrong:** `except requests.exceptions.RequestException` is replaced by `except httpx.RequestError` — if wrong exception class is used, HTTP failures are not caught.
**Why it happens:** httpx has a different exception hierarchy than requests.
**How to avoid:** `httpx.RequestError` is the base class for all httpx request exceptions (ConnectError, TimeoutException, etc.).
**Warning signs:** Unhandled exceptions on network failures in checkout flow.

### Pitfall 5: Port mismatch
**What goes wrong:** Current gunicorn binds to `:5000`. Nginx gateway proxies to `service:5000`. Uvicorn must also bind to `:5000`.
**Why it happens:** Uvicorn defaults to port `8000` if `--port` is not specified.
**How to avoid:** Always set `--port 5000` in Uvicorn command. Current `__main__` block uses port 8000 — update to 5000 for consistency.
**Warning signs:** Gateway returns 502 Bad Gateway.

### Pitfall 6: `gunicorn` still in requirements.txt
**What goes wrong:** Dockerfile installs gunicorn but it's never used; wastes image layer space. More importantly, if gunicorn is removed but requirements.txt isn't updated, Docker build fails.
**How to avoid:** Remove `gunicorn` from each service's requirements.txt, add `quart`, `uvicorn`, `redis[hiredis]`. For Order: also add `httpx`.
**Warning signs:** Docker build succeeds but container fails on `uvicorn: not found`.

---

## Code Examples

Verified patterns from official sources:

### Complete async Redis initialization (before_serving/after_serving)
```python
# Source: https://quart.palletsprojects.com/en/latest/how_to_guides/startup_shutdown/
# Source: https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html

import redis.asyncio as redis

db: redis.Redis = None

@app.before_serving
async def startup():
    global db
    db = redis.Redis(
        host=os.environ['REDIS_HOST'],
        port=int(os.environ['REDIS_PORT']),
        password=os.environ['REDIS_PASSWORD'],
        db=int(os.environ['REDIS_DB'])
    )

@app.after_serving
async def shutdown():
    await db.aclose()
```

### Complete async helper function with abort
```python
# Pattern: async helper + abort (abort does NOT need await)
async def get_item_from_db(item_id: str) -> StockValue | None:
    try:
        entry: bytes = await db.get(item_id)
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    entry: StockValue | None = msgpack.decode(entry, type=StockValue) if entry else None
    if entry is None:
        abort(400, f"Item: {item_id} not found!")
    return entry
```

### async route handler example
```python
@app.post('/subtract/<item_id>/<amount>')
async def remove_stock(item_id: str, amount: int):
    item_entry: StockValue = await get_item_from_db(item_id)
    item_entry.stock -= int(amount)
    app.logger.debug(f"Item: {item_id} stock updated to: {item_entry.stock}")
    if item_entry.stock < 0:
        abort(400, f"Item: {item_id} stock cannot get reduced below zero!")
    try:
        await db.set(item_id, msgpack.encode(item_entry))
    except redis.exceptions.RedisError:
        return abort(400, DB_ERROR_STR)
    return Response(f"Item: {item_id} stock updated to: {item_entry.stock}", status=200)
```

### httpx shared client (Order service)
```python
# Source: https://www.python-httpx.org/async/
import httpx

http_client: httpx.AsyncClient = None

@app.before_serving
async def startup():
    global db, http_client
    db = redis.Redis(...)
    http_client = httpx.AsyncClient()

@app.after_serving
async def shutdown():
    await db.aclose()
    await http_client.aclose()

async def send_post_request(url: str):
    try:
        response = await http_client.post(url)
    except httpx.RequestError:
        abort(400, REQ_ERROR_STR)
    return response

async def send_get_request(url: str):
    try:
        response = await http_client.get(url)
    except httpx.RequestError:
        abort(400, REQ_ERROR_STR)
    return response
```

### Updated requirements.txt (Stock/Payment)
```
quart==0.20.1
uvicorn==0.34.0
redis[hiredis]==5.0.3
msgspec==0.18.6
```

### Updated requirements.txt (Order)
```
quart==0.20.1
uvicorn==0.34.0
redis[hiredis]==5.0.3
msgspec==0.18.6
httpx==0.27.0
```

### Updated docker-compose command
```yaml
command: uvicorn app:app --host 0.0.0.0 --port 5000 --workers 2 --log-level info
```

---

## Recommendations for Claude's Discretion Items

### Redis client initialization pattern
**Recommendation:** Use `@app.before_serving` / `@app.after_serving` hooks.
**Rationale:** Module-level initialization fails with asyncio (no event loop at import time). Lifecycle hooks are the Quart-idiomatic solution. The `@app.while_serving` pattern (combined hook) is also valid but `before/after` split is more readable for this use case.

### Uvicorn worker model
**Recommendation:** `--workers 2` matching current gunicorn `-w 2`.
**Rationale:** Keeps behavior identical to current setup. Single process would reduce throughput; more workers would increase memory. 2 workers is the tested baseline. No `--reload` in production.

### Requirements.txt organization
**Recommendation:** Keep per-service `requirements.txt` files. Update each individually.
**Rationale:** Order service needs `httpx`, others don't. Shared requirements would require a conditional or bloat Stock/Payment with unused dependencies. Per-service files match current structure.

### Async HTTP client choice
**Recommendation:** `httpx.AsyncClient`.
**Rationale:** Maintained by encode (same team as Uvicorn/Starlette). Familiar `response.json()`, `response.status_code` API. `httpx.RequestError` is clean exception hierarchy. This client is temporary (replaced in Phase 2 by gRPC) — httpx is the lowest-friction choice.

### Logging setup
**Recommendation:** Remove the `else:` gunicorn logger block entirely. Use `app.logger` as-is.
**Rationale:** Quart's `app.logger` uses Python's standard logging module (`quart.app` logger). Uvicorn handles its own log output via `--log-level`. No handler wiring needed. Existing `app.logger.debug(...)` calls in routes work without changes.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Separate `aioredis` library | `redis.asyncio` submodule built into redis-py | redis-py 4.2 (2022) | No separate install needed |
| `atexit.register` for cleanup | `@app.before_serving` / `@app.after_serving` | Quart 0.x (ASGI lifespan) | Async-safe cleanup |
| Flask WSGI with Gunicorn | Quart ASGI with Uvicorn | Quart stable 2020+ | Full async event loop |

**Deprecated/outdated:**
- `aioredis` standalone package: Abandoned (merged into redis-py). Do NOT use.
- `flask-async` extension: Flask 2.0+ added async view support but it runs in a thread pool, not a true async loop. Not appropriate for this migration.
- Gunicorn + UvicornWorker for this phase: Valid in production but adds complexity. Standalone `uvicorn --workers` is sufficient.

---

## Open Questions

1. **`abort()` return value handling in helper functions**
   - What we know: Current code does `return abort(400, ...)` in some helpers, `abort(400, ...)` (no return) in others. In Flask/Quart, `abort()` raises `HTTPException` — the `return` is never executed.
   - What's unclear: Whether Quart's behavior is 100% identical to Flask's for `abort()` called inside a non-route function.
   - Recommendation: Keep existing `return abort(...)` patterns unchanged — they work identically in Quart (the return is unreachable, but harmless).

2. **`batch_init` route performance**
   - What we know: `batch_init` uses `db.mset(kv_pairs)` with potentially large dicts. With async, each call is awaited once.
   - What's unclear: Whether `mset` with large payloads behaves differently in async context vs sync.
   - Recommendation: No change needed — `await db.mset(kv_pairs)` is a single Redis command regardless of dict size. If performance degrades, investigate pipeline batching (out of scope for Phase 1).

3. **Uvicorn version pinning**
   - What we know: Uvicorn 0.34.x is current as of research date.
   - What's unclear: Exact latest version at time of implementation.
   - Recommendation: Pin to `uvicorn>=0.30.0` in requirements.txt for flexibility, or pin exact version found via `pip install uvicorn` during implementation.

---

## Sources

### Primary (HIGH confidence)
- [Quart Flask Migration Guide](https://quart.palletsprojects.com/en/latest/how_to_guides/flask_migration/) — import changes, async/await requirements
- [Quart Startup/Shutdown Guide](https://quart.palletsprojects.com/en/latest/how_to_guides/startup_shutdown/) — before_serving, after_serving, while_serving hooks
- [redis-py asyncio examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) — async client patterns, aclose(), connection pool ownership
- [redis.io redis-py guide](https://redis.io/docs/latest/develop/clients/redis-py/) — redis[hiredis] install, connection parameters
- [httpx Async Support](https://www.python-httpx.org/async/) — AsyncClient usage, lifecycle, shared instance
- [Uvicorn Deployment](https://www.uvicorn.org/deployment/) — workers, host/port, logging options
- Project source code: `order/app.py`, `stock/app.py`, `payment/app.py`, `docker-compose.yml`, `test/test_microservices.py`, `test/utils.py`

### Secondary (MEDIUM confidence)
- [Quart Deployment Tutorial](https://quart.palletsprojects.com/en/latest/tutorials/deployment/) — Uvicorn mentioned as supported ASGI server
- WebSearch: gunicorn logger → uvicorn logger pattern (confirmed: remove else block, use app.logger directly)
- WebSearch: httpx exception hierarchy (httpx.RequestError as base) — verified with httpx docs

### Tertiary (LOW confidence)
- None — all critical claims verified with official documentation.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified against official docs; versions confirmed on PyPI/official sources
- Architecture patterns: HIGH — patterns derived from official Quart and redis-py docs; cross-verified with actual source code
- Pitfalls: HIGH — derived from official migration guide warnings and redis.asyncio lifecycle docs; validated against codebase

**Research date:** 2026-02-28
**Valid until:** 2026-04-28 (stable libraries; Quart/redis-py/uvicorn have slow API churn)
