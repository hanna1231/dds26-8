# Coding Conventions

**Analysis Date:** 2026-02-27

## Naming Patterns

**Files:**
- Lowercase with underscores for modules: `app.py`, `test_microservices.py`, `utils.py`
- Service apps use simple names matching service domain: `order/app.py`, `payment/app.py`, `stock/app.py`

**Functions:**
- Snake_case for all functions: `get_order_from_db()`, `create_order()`, `add_item_to_order()`
- Helper/utility functions use descriptive verbs: `get_*`, `create_*`, `add_*`, `remove_*`, `send_post_request()`, `send_get_request()`
- Private/internal functions follow same convention (no leading underscore used in codebase)

**Variables:**
- Snake_case consistently used: `order_id`, `user_id`, `item_id`, `total_cost`, `status_code`, `kv_pairs`, `removed_items`
- Constants in UPPER_SNAKE_CASE: `DB_ERROR_STR`, `REQ_ERROR_STR`, `GATEWAY_URL`, `REDIS_HOST`, `REDIS_PORT`
- Type-annotated variables: `entry: bytes`, `order_entry: OrderValue`, `item_reply: dict`

**Types:**
- Classes use PascalCase: `OrderValue`, `UserValue`, `StockValue`
- All data classes inherit from `msgspec.Struct` for serialization
- Type hints used extensively with union types: `OrderValue | None`, `dict[str, int]`, `list[tuple[str, int]]`

## Code Style

**Formatting:**
- No explicit formatter configured (eslint/prettier not applicable - this is Python)
- Standard Python conventions followed: 4-space indentation
- Line length not strictly enforced (some lines reach ~100 chars)
- Blank lines used to separate logical sections within functions

**Linting:**
- No linting configuration file detected
- Code follows PEP 8 conventions by convention
- Import organization is consistent across all files

## Import Organization

**Order:**
1. Standard library imports: `logging`, `os`, `atexit`, `random`, `uuid`, `collections`
2. Third-party imports: `redis`, `requests`, `msgspec`, `flask`
3. Type imports are inline with standard imports (no separate TYPE_CHECKING blocks)

**Path Aliases:**
- No path aliases used
- Relative imports for test utilities: `import utils as tu` in `test_microservices.py`
- Direct environment variable access via `os.environ['KEY']`

## Error Handling

**Patterns:**
- Exception-specific handling for known errors: `except redis.exceptions.RedisError:`, `except requests.exceptions.RequestException:`
- Uses Flask's `abort()` function to return HTTP error responses with status codes
- Validation happens after database lookups with conditional checks: `if entry is None: abort(...)`
- Custom error messages in string constants: `DB_ERROR_STR = "DB error"`, `REQ_ERROR_STR = "Requests error"`
- HTTP 400 status code consistently used for all errors (bad requests, validation, out of stock)
- No try-except blocks without clear recovery or error propagation

**Error Messages:**
- Descriptive messages include context: `f"Order: {order_id} not found!"`, `f"Item: {item_id} does not exist!"`
- Double error checking in some paths: first try-except wraps Redis operations, then null check on deserialized data

## Logging

**Framework:** Flask's built-in `app.logger` (configured for gunicorn in production)

**Patterns:**
- Debug logging used for significant operations: `app.logger.debug(f"Checking out {order_id}")`
- Logging setup for production uses gunicorn logger: `gunicorn_logger = logging.getLogger('gunicorn.error')`
- Log level set from gunicorn configuration in production (not in development)
- Information logging not extensively used; focus is on debug and error levels

## Comments

**When to Comment:**
- Comments explain non-obvious logic and state transitions
- Comments document data flow in complex operations (e.g., checkout with rollback)
- Inline comments explain purpose: `# The removed items will contain the items that we already have successfully subtracted stock from`
- Comments mark logical sections in test utilities file with comment blocks

**JSDoc/TSDoc:**
- Not applicable - no TypeScript/JavaScript files in main services
- Python docstrings not extensively used; type hints provide documentation
- Function signatures serve as inline documentation with full type annotations

## Function Design

**Size:** Functions typically 5-20 lines; longest is `checkout()` at ~30 lines with complex orchestration logic

**Parameters:**
- String parameters for identifiers: `order_id: str`, `user_id: str`, `item_id: str`
- Integer parameters for quantities/amounts: `quantity: int`, `amount: int`, `price: int`
- Explicit type conversions in function bodies: `int(quantity)`, `int(amount)` despite type hints
- No default parameters; all parameters required

**Return Values:**
- JSON responses via Flask's `jsonify()`: returns dict
- HTTP responses via Flask's `Response()`: returns HTTP 200 with message
- Abort calls return error responses (function exits via Flask error handling)
- Typed return hints: `-> OrderValue | None`, `-> dict`, `-> Response`

## Module Design

**Exports:**
- Flask app exported as `app` in each service module
- Route handlers defined at module level as decorated functions
- Helper functions (non-routes) defined before or after routes with no explicit exports

**Barrel Files:**
- No barrel files (index.ts pattern) - not applicable to this Python codebase
- Utils.py acts as a test helper module providing wrapper functions around HTTP calls

## Constants and Configuration

**Configuration:**
- Environment variables read at module level and assigned to module-level variables
- No config file parsing; direct `os.environ['KEY']` access
- Redis connection established at module load time: `db: redis.Redis = redis.Redis(...)`
- Database connection reference stored in module-level variable accessible to all routes

**Serialization:**
- msgpack binary serialization used exclusively for data persistence
- Struct classes (msgspec.Struct) enforce type safety for serialized data
- Type parameter passed to decoder: `msgpack.decode(entry, type=OrderValue)`

---

*Convention analysis: 2026-02-27*
