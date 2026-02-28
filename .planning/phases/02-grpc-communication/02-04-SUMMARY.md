---
phase: 02-grpc-communication
plan: 04
subsystem: testing
tags: [pytest, pytest-asyncio, grpc, redis, integration-tests, idempotency]

# Dependency graph
requires:
  - phase: 02-grpc-communication plan 02
    provides: StockServiceServicer and PaymentServiceServicer with Lua idempotency
  - phase: 02-grpc-communication plan 03
    provides: orchestrator/client.py wrapper functions (reserve_stock, charge_payment, etc.)
provides:
  - pytest.ini with asyncio_mode=auto and session-scoped event loop configuration
  - tests/conftest.py with session-scoped Redis, Stock gRPC (50051), Payment gRPC (50052), seed data, and client fixtures
  - tests/test_grpc_integration.py covering GRPC-01 through GRPC-04
affects: [03-saga-orchestrator, any future test additions]

# Tech tracking
tech-stack:
  added: [pytest>=8.0, pytest-asyncio>=0.24]
  patterns: [session-scoped gRPC test fixtures, sys.path manipulation for multi-service imports, module cache invalidation for same-named modules]

key-files:
  created:
    - pytest.ini
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_grpc_integration.py
  modified:
    - orchestrator/requirements.txt

key-decisions:
  - "asyncio_default_test_loop_scope=session required alongside asyncio_default_fixture_loop_scope=session — without it tests run in function-scoped loops causing 'attached to a different loop' errors with session-scoped gRPC channels"
  - "Stock gRPC server on port 50051, Payment gRPC server on port 50052 in tests — avoids port collision since both services default to 50051 in production"
  - "Module cache invalidation (del sys.modules['grpc_server']) required between stock and payment grpc_server imports — Python caches by module name, not path"
  - "Session-scoped fixtures for all servers and clients — one event loop for entire test session avoids cross-fixture loop mismatch"

patterns-established:
  - "Pytest session fixtures: use @pytest_asyncio.fixture(scope='session', loop_scope='session') for all async fixtures shared across tests"
  - "Multi-service sys.path imports: insert path, import, pop path, clear module cache before next service import"

requirements-completed: [GRPC-01, GRPC-02, GRPC-03, GRPC-04]

# Metrics
duration: 2min
completed: 2026-02-28
---

# Phase 2 Plan 4: Integration Test Infrastructure Summary

**pytest integration tests proving client -> gRPC server -> Redis wiring with session-scoped fixtures for Stock (50051), Payment (50052), and idempotency deduplication verification**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-28T09:02:19Z
- **Completed:** 2026-02-28T09:04:25Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- pytest.ini configured with session-scoped asyncio mode eliminating event loop mismatch errors across all tests
- tests/conftest.py provides session-scoped Redis, real gRPC servers, test data seeding, and orchestrator client initialization
- 7 integration tests covering all 4 GRPC requirements (proto imports, server reachability, client-via-gRPC, idempotency deduplication)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create pytest configuration and test fixtures** - `c48603e` (chore)
2. **Task 2: Create integration tests covering GRPC-01 through GRPC-04** - `055dc05` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `pytest.ini` - pytest configuration: asyncio_mode=auto, session-scoped loop for fixtures and tests
- `tests/__init__.py` - Package marker for tests directory
- `tests/conftest.py` - Session-scoped fixtures: redis_db, stock_grpc_server (50051), payment_grpc_server (50052), seed_test_data, grpc_clients
- `tests/test_grpc_integration.py` - 7 integration tests: GRPC-01 (proto smoke), GRPC-02 (server reachable), GRPC-03 (client uses gRPC), GRPC-04 (idempotency deduplication + corollary), business error test
- `orchestrator/requirements.txt` - Added pytest>=8.0 and pytest-asyncio>=0.24

## Decisions Made

- Added `asyncio_default_test_loop_scope = session` to pytest.ini to match fixture loop scope — required to prevent grpc.aio channels (created in session-scoped fixtures) from being used in function-scoped test loops
- Used manual gRPC server creation in conftest fixtures rather than calling serve_grpc() to allow custom ports (50051/50052) without modifying service code
- Module cache invalidation (`del sys.modules['grpc_server']`) between stock and payment imports — both services have a module named `grpc_server`; Python's module cache requires explicit invalidation

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added asyncio_default_test_loop_scope=session to pytest.ini**
- **Found during:** Task 2 (running integration tests)
- **Issue:** Tests failed with "RuntimeError: Task got Future attached to a different loop" because the default test loop scope is "function" while fixtures use "session" scope. The grpc.aio channel created in the session-scoped fixture was bound to the session loop, but tests ran in function-scoped loops.
- **Fix:** Added `asyncio_default_test_loop_scope = session` to pytest.ini so all tests share the same session event loop as the fixtures
- **Files modified:** pytest.ini
- **Verification:** All 7 tests pass with `pytest tests/test_grpc_integration.py -x`
- **Committed in:** 055dc05 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Fix was essential for correctness — the plan mentioned asyncio_default_fixture_loop_scope but missed asyncio_default_test_loop_scope. No scope creep.

## Issues Encountered

The pytest-asyncio documentation from the RESEARCH.md (Pitfall 6) mentioned `asyncio_default_fixture_loop_scope = session` but didn't cover the matching `asyncio_default_test_loop_scope = session` setting. Both are required when using session-scoped grpc.aio fixtures.

## User Setup Required

None - tests run locally against Redis. Requires a running Redis instance on localhost:6379 (or configure via REDIS_HOST/REDIS_PORT/REDIS_PASSWORD/REDIS_DB environment variables).

## Next Phase Readiness

- All 4 GRPC requirements verified (GRPC-01 through GRPC-04)
- Phase 2 complete: proto contracts, gRPC servicers with Lua idempotency, orchestrator client, and integration tests
- Phase 3 (SAGA orchestrator) can import from orchestrator/client.py with confidence that gRPC wiring is verified

---
*Phase: 02-grpc-communication*
*Completed: 2026-02-28*

## Self-Check: PASSED

- pytest.ini: FOUND
- tests/__init__.py: FOUND
- tests/conftest.py: FOUND
- tests/test_grpc_integration.py: FOUND
- orchestrator/requirements.txt: FOUND
- 02-04-SUMMARY.md: FOUND
- Commit c48603e: FOUND
- Commit 055dc05: FOUND
