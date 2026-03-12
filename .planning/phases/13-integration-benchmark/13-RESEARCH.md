# Phase 13: Integration & Benchmark - Research

**Researched:** 2026-03-12
**Domain:** Integration testing across 4 mode combinations (SAGA/2PC x gRPC/queue), kill-test consistency, benchmark validation
**Confidence:** HIGH

## Summary

Phase 13 is the final validation phase for v2.0. All prior phases (8-12) built the individual pieces: business logic extraction, queue infrastructure, transport adapter, 2PC state machine, and 2PC coordinator. This phase must verify that all 4 mode combinations (SAGA/gRPC, SAGA/queue, 2PC/gRPC, 2PC/queue) work correctly under normal operation, container failure, and benchmark load.

The primary challenge is that stock and payment services currently do NOT start queue consumers -- their `app.py` files only launch gRPC servers. The orchestrator already handles COMM_MODE correctly (conditionally starting queue client and reply listener), but stock/payment need matching changes. Additionally, docker-compose.yml lacks COMM_MODE and TRANSACTION_PATTERN environment variables. These are prerequisite integration bugs that must be fixed before any mode-combination testing can succeed.

The secondary challenge is adapting the kill-test script and benchmark Makefile targets to run across all 4 modes systematically. The existing `scripts/kill_test.py` and `make benchmark` targets assume the default (SAGA/gRPC) configuration.

**Primary recommendation:** Fix the queue consumer wiring in stock/payment app.py first, add env vars to docker-compose, then run existing integration tests across all 4 modes, then kill-test + benchmark.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INT-01 | All 4 mode combinations (SAGA/2PC x gRPC/queue) pass integration tests | Requires: (1) stock/payment app.py queue consumer wiring, (2) docker-compose env vars, (3) test runner that iterates 4 modes |
| INT-02 | Kill-test consistency for 2PC mode (no lost money/items after recovery) | Existing `scripts/kill_test.py` works for SAGA/gRPC; needs TRANSACTION_PATTERN=2pc env var + queue mode variants |
| INT-03 | Benchmark passes with 0 consistency violations in all modes | Existing `make benchmark` target works; needs to be run 4 times with different env var combinations |
</phase_requirements>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest + pytest-asyncio | latest | Unit/integration tests (tests/) | Already used, asyncio_mode=auto |
| docker compose | v2 | Container orchestration for integration tests | Already used via Makefile |
| aiohttp | latest | Async HTTP client for kill-test | Already used in scripts/kill_test.py |
| requests | latest | Sync HTTP client for test/test_microservices.py | Already used |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| wdm-project-benchmark | external | Consistency test + stress test | Cloned into project root, run via `make benchmark` |

### No New Dependencies Needed
This phase requires zero new libraries. All testing infrastructure exists. The work is wiring, configuration, and systematic execution.

## Architecture Patterns

### Critical Finding: Queue Consumer Wiring Gap

**stock/app.py** and **payment/app.py** currently only start gRPC servers:
```python
# stock/app.py:startup() -- CURRENT
app.add_background_task(serve_grpc, db)
# NO queue consumer startup!
```

**orchestrator/app.py** already handles COMM_MODE correctly:
```python
if COMM_MODE == "queue":
    from queue_client import init_queue_client
    from reply_listener import setup_reply_consumer_group, reply_listener
    init_queue_client(db)
    # ...
```

Stock and payment need the same COMM_MODE-conditional pattern to start their queue consumers.

### Pattern: COMM_MODE-Conditional Startup (stock/payment app.py)

```python
# stock/app.py:startup() -- NEEDED
COMM_MODE = os.environ.get("COMM_MODE", "grpc")

@app.before_serving
async def startup():
    global db, _stop_event
    # ... existing Redis init ...
    app.add_background_task(serve_grpc, db)
    if COMM_MODE == "queue":
        from queue_consumer import setup_command_consumer_group, queue_consumer
        _stop_event = asyncio.Event()
        await setup_command_consumer_group(db)  # db doubles as queue_db in simple mode
        app.add_background_task(queue_consumer, db, db, _stop_event)
```

### Pattern: Docker Compose Env Var Configuration

```yaml
# docker-compose.yml -- additions needed for orchestrator-service
environment:
  - COMM_MODE=${COMM_MODE:-grpc}
  - TRANSACTION_PATTERN=${TRANSACTION_PATTERN:-saga}

# docker-compose.yml -- additions needed for stock-service and payment-service
environment:
  - COMM_MODE=${COMM_MODE:-grpc}
```

### Pattern: 4-Mode Test Matrix

| Mode | TRANSACTION_PATTERN | COMM_MODE | Key Behavior |
|------|-------------------|-----------|--------------|
| 1 | saga | grpc | v1.0 default, must still work |
| 2 | saga | queue | SAGA over Redis Streams |
| 3 | 2pc | grpc | 2PC over gRPC |
| 4 | 2pc | queue | 2PC over Redis Streams |

### Test Execution Strategy

**Existing test infrastructure has TWO levels:**

1. **`tests/` (pytest, local):** Unit/integration tests against local Redis + in-process gRPC servers. These test SAGA and 2PC logic but do NOT test Docker Compose deployment or HTTP API. They already pass.

2. **`test/test_microservices.py` (Docker Compose, HTTP API):** Integration tests that hit the running cluster via HTTP at `localhost:8000`. These test the full stack including gateway, routing, checkout flow. **These are the ones that need to pass in all 4 modes for INT-01.**

**For INT-01:** Run `test/test_microservices.py` against Docker Compose with each of the 4 env var combinations.

**For INT-02:** Run `scripts/kill_test.py` with TRANSACTION_PATTERN=2pc (both gRPC and queue modes).

**For INT-03:** Run `make benchmark` with each of the 4 env var combinations.

### Anti-Patterns to Avoid
- **Trying to test queue mode without starting queue consumers:** Will silently timeout (5s COMMAND_TIMEOUT in queue_client.py).
- **Running kill-test without SAGA_STALENESS_SECONDS=10:** Recovery won't trigger within the test window (default is 300s).
- **Forgetting to rebuild Docker images after code changes:** `docker compose build` needed after modifying app.py.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Test matrix iteration | Custom test framework | Shell script/Makefile targets with env vars | Simple loop over 4 env var combinations |
| Consistency verification | Custom checker | wdm-project-benchmark/consistency-test | Course-provided, authoritative |
| Kill-test orchestration | New kill script | Extend existing scripts/kill_test.py | Already handles container lifecycle |

## Common Pitfalls

### Pitfall 1: Queue Consumer Not Started = Silent Timeouts
**What goes wrong:** Queue mode tests hang for 5 seconds then fail with "queue timeout" error.
**Why it happens:** stock/payment app.py don't start queue_consumer background tasks.
**How to avoid:** Wire queue consumer startup in stock/payment app.py conditioned on COMM_MODE=queue.
**Warning signs:** All queue-mode checkouts return `{"success": false, "error_message": "queue timeout"}`.

### Pitfall 2: Docker Image Cache Stale
**What goes wrong:** Code changes to app.py don't take effect in Docker Compose.
**Why it happens:** Docker uses cached layers; `docker compose up` doesn't rebuild.
**How to avoid:** Always run `docker compose build` (or `make dev-build`) after code changes.
**Warning signs:** Logs don't show expected COMM_MODE messages.

### Pitfall 3: Queue Consumer Needs Shared Redis Connection
**What goes wrong:** Queue consumer can't read from streams in simple mode.
**Why it happens:** In simple mode (shared Redis cluster), queue streams and data live on the same cluster. The queue_consumer takes separate `db` and `queue_db` params.
**How to avoid:** In simple mode, pass the same `db` connection for both params.

### Pitfall 4: Kill-Test Recovery Timing
**What goes wrong:** Consistency check runs before recovery completes.
**Why it happens:** SAGA_STALENESS_SECONDS defaults to 300s; kill-test waits only 30s.
**How to avoid:** Set SAGA_STALENESS_SECONDS=10 in docker-compose env when running kill tests.

### Pitfall 5: TRANSACTION_PATTERN Read at Import Time
**What goes wrong:** Can't switch transaction pattern without restarting containers.
**Why it happens:** `TRANSACTION_PATTERN = os.environ.get("TRANSACTION_PATTERN", "saga")` in grpc_server.py runs at import time.
**How to avoid:** Accept this behavior -- restart containers between mode switches. Don't try hot-swapping.

### Pitfall 6: Redis Streams Consumer Group Not Created
**What goes wrong:** Queue consumer fails with NOGROUP error on first read.
**Why it happens:** `setup_command_consumer_group()` wasn't called before `queue_consumer()`.
**How to avoid:** Call setup_command_consumer_group() during app startup, before starting the consumer background task.

## Code Examples

### Stock app.py Queue Consumer Wiring
```python
# Source: Inferred from orchestrator/app.py pattern + stock/queue_consumer.py
import asyncio
import os

COMM_MODE = os.environ.get("COMM_MODE", "grpc")
_stop_event = None

@app.before_serving
async def startup():
    global db, _stop_event
    # ... existing Redis init ...
    app.add_background_task(serve_grpc, db)
    if COMM_MODE == "queue":
        from queue_consumer import setup_command_consumer_group, queue_consumer
        _stop_event = asyncio.Event()
        await setup_command_consumer_group(db)
        app.add_background_task(queue_consumer, db, db, _stop_event)

@app.after_serving
async def shutdown():
    if _stop_event:
        _stop_event.set()
    await stop_grpc_server()
    await db.aclose()
```

### Makefile Targets for 4-Mode Testing
```makefile
# Run integration tests in all 4 modes
test-all-modes:
	@for tp in saga 2pc; do \
		for cm in grpc queue; do \
			echo "=== Mode: TRANSACTION_PATTERN=$$tp COMM_MODE=$$cm ==="; \
			TRANSACTION_PATTERN=$$tp COMM_MODE=$$cm $(MAKE) dev-clean dev-up; \
			sleep 5; \
			cd test && python -m pytest test_microservices.py -v; cd ..; \
		done; \
	done

# Run benchmark in all 4 modes
benchmark-all-modes:
	@for tp in saga 2pc; do \
		for cm in grpc queue; do \
			echo "=== Benchmark: TRANSACTION_PATTERN=$$tp COMM_MODE=$$cm ==="; \
			TRANSACTION_PATTERN=$$tp COMM_MODE=$$cm $(MAKE) dev-clean dev-up; \
			sleep 5; \
			$(MAKE) benchmark; \
		done; \
	done
```

### Docker Compose Env Var Additions
```yaml
# For orchestrator-service:
environment:
  - COMM_MODE=${COMM_MODE:-grpc}
  - TRANSACTION_PATTERN=${TRANSACTION_PATTERN:-saga}

# For stock-service and payment-service:
environment:
  - COMM_MODE=${COMM_MODE:-grpc}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SAGA/gRPC only | 4 mode combinations | v2.0 (Phases 8-12) | Need integration testing across all modes |
| Manual gRPC-only testing | Automated multi-mode test runner | Phase 13 | Systematic validation |

**Key insight:** v1.0 had one mode (SAGA/gRPC) and all tests validated that single mode. v2.0 has 4 modes, and the test infrastructure must validate all 4.

## Open Questions

1. **Queue consumer `queue_db` parameter in cluster mode**
   - What we know: `queue_consumer(db, queue_db, stop_event)` takes separate connections for data ops vs stream ops. In simple mode both are the same connection.
   - What's unclear: In full/cluster mode, does the queue Redis need to be a separate cluster, or do all services share one Redis cluster for streams?
   - Recommendation: For Phase 13, test only in simple mode (shared Redis). Cluster mode is the same architecture, just different Redis connection targets.

2. **Benchmark timing with queue mode**
   - What we know: Queue mode adds latency (XADD -> consumer poll -> XADD reply -> listener poll) vs direct gRPC call.
   - What's unclear: Whether benchmark will timeout or produce false failures due to increased latency.
   - Recommendation: Run benchmark first, observe if timeouts occur. If so, may need to tune COMMAND_TIMEOUT or consumer POLL_INTERVAL_MS.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (unit/integration), unittest (Docker integration) |
| Config file | pytest.ini |
| Quick run command | `pytest tests/ -x -v` |
| Full suite command | `pytest tests/ -v && cd test && python -m pytest test_microservices.py -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INT-01 | All 4 modes pass integration tests | integration (Docker) | `cd test && python -m pytest test_microservices.py -v` (x4 modes) | test/test_microservices.py exists |
| INT-02 | Kill-test 0 consistency violations (2PC) | e2e (Docker) | `TRANSACTION_PATTERN=2pc python scripts/kill_test.py --all` | scripts/kill_test.py exists |
| INT-03 | Benchmark 0 consistency violations (all modes) | e2e (Docker) | `make benchmark` (x4 modes) | wdm-project-benchmark/ exists |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -v` (local tests, <30s)
- **Per wave merge:** Full Docker integration across all 4 modes
- **Phase gate:** All 4 modes green on integration tests, kill-test, and benchmark

### Wave 0 Gaps
- [ ] `stock/app.py` -- needs COMM_MODE-conditional queue consumer wiring
- [ ] `payment/app.py` -- needs COMM_MODE-conditional queue consumer wiring
- [ ] `docker-compose.yml` -- needs COMM_MODE and TRANSACTION_PATTERN env vars
- [ ] Makefile -- needs multi-mode test/benchmark targets (optional, can use shell commands)

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection: orchestrator/app.py, orchestrator/transport.py, orchestrator/grpc_server.py, stock/app.py, payment/app.py, stock/queue_consumer.py, payment/queue_consumer.py
- Direct codebase inspection: docker-compose.yml, Makefile, scripts/kill_test.py
- Direct codebase inspection: tests/conftest.py, test/test_microservices.py, test/utils.py

### Secondary (MEDIUM confidence)
- wdm-project-benchmark/consistency-test/run_consistency_test.py (external tool, read directly)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries already in use, no new dependencies
- Architecture: HIGH - direct code inspection reveals exact gaps (queue consumer wiring, env vars)
- Pitfalls: HIGH - derived from actual code patterns (timeout values, import-time reads, consumer group setup)

**Research date:** 2026-03-12
**Valid until:** 2026-04-01 (stable -- no external dependency changes expected)
