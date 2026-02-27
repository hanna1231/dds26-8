# Codebase Concerns

**Analysis Date:** 2026-02-27

## Tech Debt

**Hardcoded Credentials in Environment Configuration:**
- Issue: Redis password "redis" is hardcoded in plain text across all docker-compose environment files
- Files: `/Users/daniel/WebstormProjects/dds26-8/env/order_redis.env`, `/Users/daniel/WebstormProjects/dds26-8/env/payment_redis.env`, `/Users/daniel/WebstormProjects/dds26-8/env/stock_redis.env`
- Impact: Security risk in development and potentially production. Credentials are visible in git history and docker-compose files
- Fix approach: Move to .env files excluded from git, use secrets management for Kubernetes deployments, rotate hardcoded password

**Error Handling Antipattern:**
- Issue: Functions that should return values but call `abort()` instead, making error handling inconsistent
- Files: `order/app.py` (lines 42-53, 108-114, 117-123), `payment/app.py` (lines 33-44), `stock/app.py` (lines 34-45)
- Impact: Functions like `get_order_from_db()` have return type `OrderValue | None` but call `abort()` which raises exceptions. This creates confusion about whether the function returns None or raises
- Fix approach: Either return None and handle in caller, or remove None from type hint and consistently raise exceptions

**Weak Type Safety with String Conversions:**
- Issue: Route parameters are strings but converted to int inline throughout handlers, no validation before conversion
- Files: `order/app.py` (lines 68-73, 127, 135), `payment/app.py` (lines 59-61, 83, 86, 99), `stock/app.py` (lines 61-64, 87, 89, 101)
- Impact: Invalid numeric inputs will cause 500 errors instead of proper 400 validation errors. No bounds checking on numeric values
- Fix approach: Add input validation decorator or middleware, type hints don't prevent runtime issues with Flask string parameters

**Logging Configuration Inconsistency:**
- Issue: Debug logging enabled in production via `app.run(debug=True)` in main block that could be accidentally left in production
- Files: `order/app.py` (line 182), `payment/app.py` (line 110), `stock/app.py` (line 113)
- Impact: If Docker container is run directly (not through gunicorn), Flask debug mode is enabled exposing sensitive info and enabling arbitrary code execution
- Fix approach: Remove debug=True, use environment-based configuration for debug mode, implement proper logging setup

**Insufficient Test Coverage:**
- Issue: Only 3 basic integration tests for entire system, no unit tests, no error case coverage
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`
- Impact: Most error paths untested, concurrent access issues undetected, rollback logic not verified in all scenarios
- Fix approach: Add unit tests for individual service logic, add more integration test cases for error scenarios

## Known Bugs

**URL Routing Mismatch in Test Utilities:**
- Symptoms: Tests call `/orders/create`, `/payment/create_user`, `/payment/find_user` but services define `/create`, `/create_user`, `/find_user`
- Files: `test/utils.py` (lines 48, 33, 37), `order/app.py` (line 56), `payment/app.py` (lines 47, 71)
- Trigger: Running tests - they will fail with 404 errors because gateway strips `/orders/` and `/payment/` prefixes but test URLs already include them
- Workaround: Gateway nginx config redirects incorrectly, test utils need to match actual service endpoints without prefix

**Request Parameter Ignored in Payment Service:**
- Symptoms: `add_credit_to_user()` in test utils sends amount as parameter but function `add_credit()` in payment/app.py doesn't validate it
- Files: `payment/app.py` (lines 82-91), `test/utils.py` (line 40-41)
- Trigger: Call to `/payment/add_funds/<user_id>/<amount>` - amount parameter is converted but not verified before use
- Workaround: No validation in place; trusts client input

**Concurrent Read-Modify-Write Race Condition:**
- Symptoms: Order service checkout modifies order state without transaction safety, payment service and stock service similarly vulnerable
- Files: `order/app.py` (lines 150-178), `payment/app.py` (lines 94-106), `stock/app.py` (lines 97-109)
- Trigger: Two concurrent checkout requests on same order, two concurrent payment removals, two concurrent stock removals
- Workaround: Redis doesn't prevent race conditions without WATCH/MULTI/EXEC. Current code does separate get-then-set operations

**Missing Dockerfile for Order Service:**
- Symptoms: Order service has no Dockerfile in its directory
- Files: `/Users/daniel/WebstormProjects/dds26-8/order/` - missing Dockerfile
- Trigger: docker-compose build will fail for order service
- Workaround: Generic Dockerfile pattern exists in payment and stock, can copy

## Security Considerations

**Plaintext Redis Password Storage:**
- Risk: Redis password "redis" exposed in docker-compose and environment files. Anyone with repo access has database credentials
- Files: `env/order_redis.env`, `env/payment_redis.env`, `env/stock_redis.env`, `docker-compose.yml` (lines 28, 41, 54)
- Current mitigation: None - password is in git and docker-compose
- Recommendations: Use Docker secrets, Kubernetes Secrets, environment variables from secure management systems; exclude env files from git; rotate hardcoded password

**No Input Validation:**
- Risk: String parameters from URLs passed directly to database queries and financial operations without validation
- Files: `order/app.py`, `payment/app.py`, `stock/app.py` (all route handlers)
- Current mitigation: Flask's URL routing provides basic parameter extraction only
- Recommendations: Add validation layer using marshmallow or pydantic, validate numeric types before conversion, add bounds checking

**No Authentication or Authorization:**
- Risk: Any caller can create orders, modify payments, adjust stock. No API key, JWT, or role-based access
- Files: All three service files lack authentication decorators/middleware
- Current mitigation: Assumes internal network trust (gateway is on same docker network)
- Recommendations: Implement JWT validation, API key authentication, role-based access control; validate service-to-service calls

**Debug Mode in Production:**
- Risk: Flask debug mode (if left enabled) exposes code, allows interactive debugging, enables arbitrary code execution
- Files: `order/app.py` (line 182), `payment/app.py` (line 110), `stock/app.py` (line 113)
- Current mitigation: Main block only runs in direct execution, gunicorn config doesn't enable debug
- Recommendations: Never run with debug=True in production, use environment-based feature flags, remove debug mode entirely

## Performance Bottlenecks

**Synchronous Inter-Service Calls Blocking:**
- Problem: Order service makes blocking HTTP requests to payment and stock services during checkout (lines 161, 167)
- Files: `order/app.py` (lines 108-123, 144-178)
- Cause: `requests.post()` and `requests.get()` are blocking; if downstream service is slow, order service threads exhaust
- Improvement path: Implement async HTTP client (aiohttp), add circuit breaker pattern, implement timeout handling, consider event-driven architecture

**No Connection Pooling:**
- Problem: Each service creates new Redis connection per request without pooling
- Files: `order/app.py` (lines 22-25), `payment/app.py` (lines 16-19), `stock/app.py` (lines 16-19)
- Cause: Redis client initialized once but no connection pool configured for concurrent requests
- Improvement path: Explicitly create redis.ConnectionPool, configure pool_size based on gunicorn workers (2 workers × ~50 connections), reuse pool

**No Request Timeouts:**
- Problem: HTTP requests between services have no timeout set
- Files: `order/app.py` (lines 110, 119)
- Cause: `requests.post()` and `requests.get()` default to no timeout, hanging requests consume threads indefinitely
- Improvement path: Add timeout parameter (e.g., 5 seconds), implement exponential backoff for retries, add circuit breaker for failing services

**Single-Threaded Redis Queries Without Pipelining:**
- Problem: Checkout operation makes multiple sequential Redis calls (read order, write order, etc.)
- Files: `order/app.py` (lines 42-53 for get, 136-139 for set, 174-176 for set)
- Cause: msgspec decode/encode happens per request, no batching
- Improvement path: Use Redis WATCH/MULTI/EXEC for transactional consistency, pipeline multiple commands, cache decoded values

**Unbounded List Growth in Order Items:**
- Problem: Order items list grows without limit; same item can be added multiple times
- Files: `order/app.py` (lines 134-135)
- Cause: No deduplication, no size limit, no validation
- Improvement path: Use OrderedDict or set for items, limit order size, implement item deduplication logic

## Fragile Areas

**Distributed Transaction Rollback Logic:**
- Files: `order/app.py` (lines 144-178)
- Why fragile: Manual rollback in checkout is fragile - if rollback request fails, system is in inconsistent state. No idempotency keys. If payment service fails after stock deduction but before updating order.paid, order is marked as paid but payment might fail
- Safe modification: Add idempotency keys to all operations, use saga pattern with compensating transactions, add retry logic with exponential backoff, log all rollback attempts
- Test coverage: Only one checkout success test, no rollback scenario tests (line 111-124 tests one failure case but doesn't verify rollback success)

**Message Serialization Format:**
- Files: All three services using msgspec with Struct definitions
- Why fragile: Changing field order, types, or removing fields breaks serialization. No migration strategy. Struct defines exact schema with no versioning
- Safe modification: Add schema versioning, implement forward/backward compatibility checks, never remove fields directly
- Test coverage: No serialization tests, no schema evolution tests

**Environment Variable Dependencies:**
- Files: All three services require REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB at startup (lines 18-25, 16-19, 16-19)
- Why fragile: Missing env var causes crash, no defaults, no validation. Order service also requires GATEWAY_URL
- Safe modification: Add defaults, validate on startup, provide clear error messages, document required variables
- Test coverage: Tests assume specific localhost setup, no env var validation tests

**Payment Validation Logic:**
- Files: `payment/app.py` (lines 99-101)
- Why fragile: No check that amount is positive. If amount is negative (bug in order service), credit increases. No bounds checking on total credit
- Safe modification: Validate amount > 0, add max credit limits, implement audit logging, add type hints
- Test coverage: Only tested with positive amounts (test/test_microservices.py line 71-75), no negative amount test

## Scaling Limits

**Redis Single Instance No Replication:**
- Current capacity: Single Redis instance per service with 512MB max memory limit
- Limit: Loses all data on container restart, no high availability, 512MB limit reached with ~100k orders at ~5KB each
- Scaling path: Implement Redis Sentinel for failover, Redis Cluster for sharding, persistent storage with RDB/AOF, increase memory per environment

**Fixed Gunicorn Worker Pool:**
- Current capacity: 2 workers per service as configured in docker-compose.yml (line 20)
- Limit: Maximum ~20-30 concurrent requests per service before queuing, high latency under load
- Scaling path: Increase workers (depends on available CPU cores), implement horizontal scaling with load balancer, move to async framework (FastAPI, Starlette)

**Gateway Single Instance:**
- Current capacity: Single nginx instance routing all traffic
- Limit: Single point of failure, becomes bottleneck with high throughput
- Scaling path: Deploy multiple gateway instances, add load balancer (HAProxy, AWS ELB), implement sticky sessions if needed

**No Database Persistence:**
- Current capacity: All data ephemeral in Redis, lost on container restart
- Limit: Data loss on any container failure, unsuitable for production
- Scaling path: Enable Redis persistence (RDB snapshots, AOF), use persistent volumes in Kubernetes, implement regular backups

## Dependencies at Risk

**Old Flask Version:**
- Risk: Flask 3.0.2 is from 2024 Q1, minor version behind; potential security patches missed
- Impact: Security vulnerabilities, compatibility issues with newer Python
- Migration plan: Update to latest Flask 3.1+ if compatible, pin minor versions, set up dependabot for automatic updates

**Unspecified Python Version Requirement:**
- Risk: Dockerfile uses python:3.12-slim but no .python-version or pyproject.toml pinning. Development might use 3.11
- Impact: Works locally but fails in CI/production, dependency incompatibilities
- Migration plan: Add .python-version file, pin in requirements.txt or pyproject.toml, document Python version requirement

**No Development Dependencies Listed:**
- Risk: No pyproject.toml or setup.py, no dev dependency tracking for testing, linting, type checking
- Impact: Developers install whatever, inconsistent code quality, no type checking in CI
- Migration plan: Migrate to pyproject.toml, add pytest, mypy, black, flake8 to dev dependencies

**Hardcoded Request Library Behavior:**
- Risk: requests library used without wrapper; each call could fail differently
- Impact: Inconsistent error handling, no retry logic, no circuit breaker
- Migration plan: Create requests wrapper with timeout, retry logic, and proper exception handling; consider httpx for async support

## Missing Critical Features

**No Request Validation:**
- Problem: No validation of user_id, order_id, item_id formats. Could be empty strings or invalid types
- Blocks: Cannot guarantee data integrity, security issues with malformed IDs

**No Idempotency Support:**
- Problem: Creating order twice with same params creates two orders. Retrying payment could double-charge
- Blocks: Resilience to network failures, reliable error recovery

**No Data Persistence Between Restarts:**
- Problem: All data lost when containers restart
- Blocks: Production deployment, data durability guarantees

**No Observability/Monitoring:**
- Problem: No metrics, tracing, or alerting. Errors only visible in container logs
- Blocks: Understanding system behavior under load, detecting issues in production

**No Rate Limiting:**
- Problem: Any client can make unlimited requests
- Blocks: Preventing abuse, protecting services from overload

## Test Coverage Gaps

**Missing Idempotency Tests:**
- What's not tested: Creating same order twice, same payment twice, no duplicate detection
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`
- Risk: Retries will create duplicates, double-charging issues undetected
- Priority: High

**Missing Negative/Edge Case Tests:**
- What's not tested: Negative amounts, zero amounts, null/empty IDs, very large numbers
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`
- Risk: Invalid inputs crash services or cause undefined behavior
- Priority: High

**Missing Concurrent Request Tests:**
- What's not tested: Two concurrent orders on same user, concurrent stock modifications, concurrent payments
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`
- Risk: Race conditions undetected, data corruption under concurrent load
- Priority: Critical

**Missing Rollback Verification Tests:**
- What's not tested: Verify stock actually restored after failed payment, verify order.paid = False when checkout fails
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py` (line 111-124 tests failure but doesn't verify state)
- Risk: Partial failures leave system in inconsistent state undetected
- Priority: Critical

**Missing Error Handling Tests:**
- What's not tested: Redis connection failure, downstream service timeout, malformed responses
- Files: `/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`
- Risk: Failure modes unknown, error handling code untested
- Priority: High

---

*Concerns audit: 2026-02-27*
