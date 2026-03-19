# Checkout Flow: Complete Request Lifecycle

This document traces every step of a `/checkout/<order_id>` request through the distributed system, from HTTP entry to final response. The system supports two transaction patterns (**SAGA** and **2PC**) and two communication modes (**gRPC** and **Redis Streams queues**).

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Order Service: HTTP Entry Point](#2-order-service-http-entry-point)
3. [Orchestrator: gRPC Reception](#3-orchestrator-grpc-reception)
4. [SAGA Pattern: Forward Execution](#4-saga-pattern-forward-execution)
5. [SAGA Pattern: Compensation (Rollback)](#5-saga-pattern-compensation-rollback)
6. [2PC Pattern: Prepare-Commit/Abort](#6-2pc-pattern-prepare-commitabort)
7. [Transport Layer: gRPC vs Queue Mode](#7-transport-layer-grpc-vs-queue-mode)
8. [Stock Service: Operations & Redis Writes](#8-stock-service-operations--redis-writes)
9. [Payment Service: Operations & Redis Writes](#9-payment-service-operations--redis-writes)
10. [Idempotency & Exactly-Once Semantics](#10-idempotency--exactly-once-semantics)
11. [Recovery on Startup](#11-recovery-on-startup)
12. [Events & Observability](#12-events--observability)
13. [Circuit Breaker](#13-circuit-breaker)
14. [Redis Key Reference](#14-redis-key-reference)
15. [gRPC Proto Definitions](#15-grpc-proto-definitions)
16. [End-to-End Flow Diagrams](#16-end-to-end-flow-diagrams)

---

## 1. High-Level Overview

```
Client (HTTP)
    |
    v
+--------------+     gRPC (always)      +--------------------+
|   Order      | ---------------------->|   Orchestrator      |
|   Service    |<---------------------- |   (SAGA or 2PC)     |
|  :5000       |                        |  :5000 / :50053     |
+--------------+                        +----------+---------+
                                                   |
                                      +------------+------------+
                                      |  gRPC or Queue (switch) |
                                      v                         v
                              +--------------+          +--------------+
                              |    Stock     |          |   Payment    |
                              |   Service    |          |   Service    |
                              |  :5001/:50051|          |  :5002/:50051|
                              +--------------+          +--------------+
```

**Environment variables that control behavior:**
- `TRANSACTION_PATTERN`: `saga` (default) or `2pc`
- `COMM_MODE`: `grpc` (default) or `queue`

---

## 2. Order Service: HTTP Entry Point

**File:** `order/app.py`
**Endpoint:** `POST /checkout/<order_id>`

### Step-by-step:

1. **Read order from Redis**
   - Key: `<order_id>` (a UUID)
   - Value: msgpack-encoded `OrderValue` containing `{paid, items, user_id, total_cost}`
   - If order not found: HTTP 400

2. **Aggregate duplicate items**
   ```python
   items_quantities: dict[str, int] = defaultdict(int)
   for item_id, quantity in order_entry.items:
       items_quantities[item_id] += quantity
   ```

3. **Call orchestrator via gRPC** (always gRPC, regardless of COMM_MODE)
   - Target: `ORCHESTRATOR_GRPC_ADDR` (default: `orchestrator-service:50053`)
   - Method: `StartCheckout(CheckoutRequest)`
   - Timeout: **60 seconds**
   - Payload: `order_id`, `user_id`, `items[]` (LineItem with item_id + quantity), `total_cost`

4. **Handle response**
   - On success: update order record to `paid=True` in Redis, return HTTP 200 `"Checkout successful"`
   - On failure: return HTTP 400 with `error_message` from orchestrator

---

## 3. Orchestrator: gRPC Reception

**File:** `orchestrator/grpc_server.py`
**gRPC Port:** 50053

The orchestrator's `StartCheckout` method:

1. Converts protobuf `LineItem` objects to plain dicts: `[{"item_id": str, "quantity": int}, ...]`
2. Routes based on `TRANSACTION_PATTERN`:
   - `"saga"` -> `run_checkout(order_id, user_id, items, total_cost)`
   - `"2pc"` -> `run_2pc_checkout(order_id, user_id, items, total_cost)`
3. Returns `CheckoutResponse` with `success` (bool) and `error_message` (string)

---

## 4. SAGA Pattern: Forward Execution

**File:** `orchestrator/grpc_server.py` -- `run_checkout()`

### Phase 0: Exactly-Once Check

Reads existing SAGA record from Redis key `{saga:<order_id>}`:

| Existing State | Action |
|---|---|
| `COMPLETED` | Return success immediately (idempotent replay) |
| `FAILED` | Delete SAGA record + idempotency keys, allow fresh retry |
| `ABORTED` | Delete record + idempotency keys, allow fresh retry |
| Any other state | Return error: "checkout already in progress" |
| Not found | Proceed to create new SAGA |

### Phase 1: Create SAGA Record

**Redis operation:** `HSETNX` (atomic create-if-not-exists)
**Key:** `{saga:<order_id>}`
**Fields:**

| Field | Initial Value | Purpose |
|---|---|---|
| `state` | `STARTED` | State machine position |
| `order_id` | `<order_id>` | Correlation |
| `user_id` | `<user_id>` | Payment target |
| `total_cost` | `<amount>` | Charge amount |
| `items_json` | `[{"item_id":..., "quantity":...}]` | Reservation list |
| `stock_reserved` | `0` | Compensation flag |
| `payment_charged` | `0` | Compensation flag |
| `refund_done` | `0` | Compensation flag |
| `stock_restored` | `0` | Compensation flag |
| `reserved_items_json` | `[]` | Tracks partially reserved items |
| `error_message` | `""` | Last error |
| `started_at` | timestamp | Timing |
| `updated_at` | timestamp | Timing |

**TTL:** 7 days (604800 seconds)

### Phase 2: Publish Event

Publishes `checkout_started` event to stream `{saga:events}:checkout` (fire-and-forget).

### Phase 3: Stock Reservation

For **each item** in the order:

1. Generate idempotency key: `{saga:<order_id>}:step:reserve:<item_id>`
2. Call `reserve_stock(item_id, quantity, idempotency_key)` via transport layer
3. Retry strategy: `retry_forward()` -- max **3 attempts**, exponential backoff
4. Circuit breaker checked before each call

**On success (per item):**
- Add item to `reserved_items_json` list

**On success (all items):**
- Update state: `STARTED` -> `STOCK_RESERVED`
- Set flag: `stock_reserved=1`
- Publish `stock_reserved` event

**On failure (any item):**
- Transition state to `COMPENSATING`
- Publish `stock_failed` event
- Jump to **compensation** (Section 5)

### Phase 4: Payment Charge

1. Generate idempotency key: `{saga:<order_id>}:step:charge`
2. Call `charge_payment(user_id, total_cost, idempotency_key)` via transport layer
3. Same retry strategy: `retry_forward()` with max 3 attempts

**On success:**
- Update state: `STOCK_RESERVED` -> `PAYMENT_CHARGED`
- Set flag: `payment_charged=1`
- Publish `payment_completed` event

**On failure:**
- Transition to `COMPENSATING`
- Publish `payment_failed` event
- Jump to **compensation** (Section 5)

### Phase 5: Complete

- Update state: `PAYMENT_CHARGED` -> `COMPLETED`
- Publish `saga_completed` event
- Return `CheckoutResponse(success=True)` to order service

### SAGA State Machine

```
STARTED --[reserve stock]--> STOCK_RESERVED --[charge payment]--> PAYMENT_CHARGED --> COMPLETED
   |                              |                                     |
   +--[failure]--> COMPENSATING <-+-------------[failure]---------------+
                        |
                        v
                     FAILED
```

---

## 5. SAGA Pattern: Compensation (Rollback)

**File:** `orchestrator/grpc_server.py` -- `run_compensation()`

Triggered on ANY forward step failure or `CircuitBreakerError`. Runs in **reverse order** of the forward steps.

### Step 1: Refund Payment

**Condition:** `payment_charged=1` AND `refund_done!=1`

- Idempotency key: `{saga:<order_id>}:step:refund`
- Call: `refund_payment(user_id, total_cost, idempotency_key)`
- Retry: `retry_forever()` -- **unbounded retries**, exponential backoff (cap 30s)
- On success: set flag `refund_done=1`

### Step 2: Release Stock

**Condition:** `stock_reserved=1` AND `stock_restored!=1`

For each reserved item (from `reserved_items_json` or full items list):
- Idempotency key: `{saga:<order_id>}:step:release:<item_id>`
- Call: `release_stock(item_id, quantity, idempotency_key)`
- Retry: `retry_forever()` per item
- On all success: set flag `stock_restored=1`

### Step 3: Finalize

- Update state: `COMPENSATING` -> `FAILED`
- Publish `compensation_completed` and `saga_failed` events
- Return `CheckoutResponse(success=False, error_message=<reason>)` to order service

### Why retry_forever for compensation?

Compensation **must** complete to maintain consistency. If stock was deducted, it must be restored. Bounded retries could leave the system in an inconsistent state (money charged but stock not released). `retry_forever` uses exponential backoff capped at 30 seconds to avoid overwhelming a down service.

---

## 6. 2PC Pattern: Prepare-Commit/Abort

**File:** `orchestrator/grpc_server.py` -- `run_2pc_checkout()`

### Exactly-Once Check

Same as SAGA but checks `{tpc:<order_id>}` key:

| Existing State | Action |
|---|---|
| `COMMITTED` | Return success immediately |
| `ABORTED` | Delete record, allow retry |
| Any other state | Return error: "checkout already in progress" |

### Create TPC Record

**Key:** `{tpc:<order_id>}`
**Fields:**

| Field | Initial Value |
|---|---|
| `state` | `INIT` |
| `protocol` | `2pc` |
| `order_id` | `<order_id>` |
| `user_id` | `<user_id>` |
| `total_cost` | `<amount>` |
| `items_json` | serialized items |
| `stock_prepared` | `0` |
| `payment_prepared` | `0` |
| `started_at` | timestamp |
| `updated_at` | timestamp |

**TTL:** 7 days

### Phase 1: PREPARE (Concurrent)

Transition: `INIT` -> `PREPARING`

All prepare calls run **concurrently** via `asyncio.gather()`:

- For each item: `prepare_stock(item_id, quantity, order_id)`
- Plus: `prepare_payment(user_id, total_cost, order_id)`

Each participant **votes** YES (success) or NO (failure). The orchestrator collects all votes.

### Phase 2a: COMMIT (all voted YES)

1. **Write-Ahead Log:** Atomically transition `PREPARING` -> `COMMITTING` (the decision is durable before any commit message is sent)
2. Send commits concurrently:
   - For each item: `commit_stock(item_id, order_id)`
   - Plus: `commit_payment(user_id, order_id)`
3. Transition: `COMMITTING` -> `COMMITTED`
4. Return success

### Phase 2b: ABORT (any voted NO)

1. **Write-Ahead Log:** Atomically transition `PREPARING` -> `ABORTING`
2. Send aborts concurrently:
   - For each item: `abort_stock(item_id, order_id)`
   - Plus: `abort_payment(user_id, order_id)`
3. Transition: `ABORTING` -> `ABORTED`
4. Return failure with first error message

### 2PC State Machine

```
INIT --> PREPARING --[all YES]--> COMMITTING --> COMMITTED
                  |
                  +--[any NO]---> ABORTING ----> ABORTED
```

### Key Difference from SAGA

In 2PC, nothing is permanently applied during PREPARE. Stock and payment services create **hold keys** that tentatively reserve resources. Only COMMIT makes changes permanent. ABORT restores the original state. This gives **strong consistency** -- there is no window where money is charged but stock isn't reserved.

---

## 7. Transport Layer: gRPC vs Queue Mode

**File:** `orchestrator/transport.py`

The transport module conditionally imports based on `COMM_MODE`:

```python
COMM_MODE = os.environ.get("COMM_MODE", "grpc")

if COMM_MODE == "queue":
    from queue_client import reserve_stock, release_stock, ...
else:
    from client import reserve_stock, release_stock, ...
```

Both implementations expose identical function signatures. The orchestrator's SAGA/2PC logic is completely transport-agnostic.

### gRPC Mode (`orchestrator/client.py`)

- **Stock channel:** `stock-service:50051` (insecure gRPC)
- **Payment channel:** `payment-service:50051` (insecure gRPC)
- **Timeout:** 5 seconds per RPC call
- **Circuit breaker:** Each function is wrapped with `@stock_breaker` or `@payment_breaker`
- **Flow:** Direct RPC call -> wait for response -> return result dict

### Queue Mode (`orchestrator/queue_client.py` + `orchestrator/reply_listener.py`)

**Sending a command:**

1. Generate a `correlation_id` (UUID)
2. Register an `asyncio.Future` in `pending_replies[correlation_id]`
3. `XADD` to the target command stream:
   ```
   Stream: {queue}:stock:commands  or  {queue}:payment:commands
   Fields:
     correlation_id: <uuid>
     command: "reserve_stock" | "charge_payment" | etc.
     payload: JSON-encoded {item_id, quantity, idempotency_key, ...}
   ```
   - Stream maxlen: 1,000 (approximate trimming)
4. `await asyncio.wait_for(future, timeout=5.0)`
5. On timeout: return `{"success": False, "error_message": "queue timeout"}`

**Receiving replies (background task -- `reply_listener.py`):**

1. Consumer group: `orchestrator-replies` on stream `{queue}:replies`
2. Continuously reads new messages
3. For each message: extracts `correlation_id` and `result`
4. Looks up `pending_replies[correlation_id]` -> calls `future.set_result(result)`
5. ACKs the message on the stream

**Service-side consumers (`stock/queue_consumer.py`, `payment/queue_consumer.py`):**

1. Consumer group: `stock-consumers` / `payment-consumers`
2. Consumer name: `stock-1` / `payment-1`
3. Block: 1000ms, batch size: 10
4. For each command:
   - Decode `correlation_id`, `command`, `payload`
   - Dispatch to the matching operations function
   - `XADD` result to `{queue}:replies` with the same `correlation_id`
   - `XACK` the command message

---

## 8. Stock Service: Operations & Redis Writes

**Files:** `stock/grpc_server.py`, `stock/queue_consumer.py`, `stock/operations.py`

### Data Model

- **Key:** `{item:<item_id>}`
- **Value:** msgpack-encoded `StockValue` = `{stock: int, price: int}`

### SAGA Operations

#### `reserve_stock(item_id, quantity, idempotency_key)`

Executes an **atomic Lua script** (`RESERVE_STOCK_ATOMIC_LUA`) with Compare-And-Swap:

1. Check idempotency key `{item:<item_id>}:idempotency:<key>`
   - If cached result exists -> return it (skip execution)
2. Set idempotency key to `__PROCESSING__` (30s TTL)
3. Read current stock from `{item:<item_id>}`
4. **CAS check:** compare current raw bytes against expected bytes
   - If mismatch -> return `RETRY` (another request changed the value concurrently)
   - Python caller retries with fresh read
5. If `stock < quantity` -> return error `"Insufficient stock"` (**not cached** -- stock may be replenished after compensation)
6. Compute new stock: `stock - quantity`, msgpack-encode new value
7. Atomically `SET` new value + cache success result in idempotency key (86400s TTL)

#### `release_stock(item_id, quantity, idempotency_key)` -- Compensation

1. Acquire idempotency lock (`IDEMPOTENCY_ACQUIRE_LUA`)
2. Read current stock, add back quantity
3. Write updated stock value
4. Cache result in idempotency key (86400s TTL)

### 2PC Operations

#### `prepare_stock(item_id, quantity, order_id)`

Executes `PREPARE_STOCK_LUA`:

1. Check if hold key `{item:<item_id>}:hold:<order_id>` exists -> if yes, return `ALREADY_PREPARED`
2. Read current stock
3. CAS check on raw bytes
4. If `stock < quantity` -> return error
5. Atomically: SET decremented stock value + SET hold key with quantity (7-day TTL)

The hold key records **what was tentatively reserved** so it can be restored on abort.

#### `commit_stock(item_id, order_id)`

Executes `COMMIT_STOCK_LUA`:
- Simply `DEL` the hold key (the stock deduction from prepare is now permanent)
- Idempotent: succeeds even if hold key already deleted

#### `abort_stock(item_id, order_id)`

Executes `ABORT_STOCK_LUA`:

1. Check hold key exists -> if not, return `ALREADY_ABORTED`
2. CAS check on current stock bytes
3. Atomically: SET restored stock value + DEL hold key

---

## 9. Payment Service: Operations & Redis Writes

**Files:** `payment/grpc_server.py`, `payment/queue_consumer.py`, `payment/operations.py`

### Data Model

- **Key:** `{user:<user_id>}`
- **Value:** msgpack-encoded `UserValue` = `{credit: int}`

### SAGA Operations

#### `charge_payment(user_id, amount, idempotency_key)`

Executes `CHARGE_PAYMENT_ATOMIC_LUA` -- identical pattern to `reserve_stock`:

1. Check idempotency key `{user:<user_id>}:idempotency:<key>`
2. Set `__PROCESSING__` (30s TTL)
3. CAS check on user record bytes
4. If `credit < amount` -> return error `"Insufficient credit"` (**not cached**)
5. Atomically SET decremented credit + cache result (86400s TTL)

#### `refund_payment(user_id, amount, idempotency_key)` -- Compensation

1. Acquire idempotency lock
2. Read credit, add back amount
3. Write updated credit + cache result

### 2PC Operations

#### `prepare_payment(user_id, amount, order_id)`

1. Check hold key `{user:<user_id>}:hold:<order_id>` -> `ALREADY_PREPARED` if exists
2. CAS check on user bytes
3. Atomically: SET decremented credit + SET hold key (7-day TTL)

#### `commit_payment(user_id, order_id)`

DEL hold key (charge becomes permanent).

#### `abort_payment(user_id, order_id)`

Restore credit from hold amount + DEL hold key.

---

## 10. Idempotency & Exactly-Once Semantics

The system ensures exactly-once semantics at multiple levels:

### Order Level (SAGA/TPC Record Creation)

- `HSETNX` atomically creates the SAGA/TPC record only if it doesn't exist
- If a concurrent request races, it reads the existing record and returns based on its state
- Completed/committed records return success immediately (safe replay)
- Failed/aborted records are deleted to allow retry

### Step Level (Idempotency Keys)

Every individual stock/payment operation uses an idempotency key:

| SAGA Step | Key Pattern | TTL |
|---|---|---|
| Reserve stock | `{saga:<order_id>}:step:reserve:<item_id>` | 86400s |
| Charge payment | `{saga:<order_id>}:step:charge` | 86400s |
| Refund payment | `{saga:<order_id>}:step:refund` | 86400s |
| Release stock | `{saga:<order_id>}:step:release:<item_id>` | 86400s |

**Processing sentinel:** When a request starts, the key is set to `__PROCESSING__` with a 30s TTL. If the service crashes mid-operation, the sentinel expires and a retry can proceed.

**Cached result:** On completion, the key stores the JSON-encoded result with a 24-hour TTL. Any replay returns the cached result without re-executing.

**Transient failures are NOT cached:** "Insufficient stock" and "Insufficient credit" errors are not stored in the idempotency cache, because these conditions may resolve after compensation restores resources.

### 2PC Level (Hold Keys as Idempotency)

For 2PC, the hold key itself acts as the idempotency mechanism:
- `prepare_stock` checks if `{item:<item_id>}:hold:<order_id>` exists -> `ALREADY_PREPARED`
- `commit/abort` are inherently idempotent (DEL on non-existent key is a no-op)

---

## 11. Recovery on Startup

**File:** `orchestrator/recovery.py`

On orchestrator startup, **before accepting new requests**, the recovery module scans for incomplete transactions.

### SAGA Recovery (`recover_incomplete_sagas`)

1. Scan for all `{saga:*}` keys
2. Skip if state is terminal (`COMPLETED`, `FAILED`)
3. Skip if age < `SAGA_STALENESS_SECONDS` (default: 300s / 5 minutes)
4. For each stale SAGA:
   - If `COMPENSATING` -> run compensation to completion
   - If `STARTED` / `STOCK_RESERVED` / `PAYMENT_CHARGED` -> attempt to replay forward steps using same idempotency keys (safe because of idempotent operations). If forward replay fails -> trigger compensation.

### 2PC Recovery (`recover_incomplete_tpc`)

Uses **presumed abort** strategy:

| Stale State | Recovery Action |
|---|---|
| `INIT` / `PREPARING` | Send ABORTs to all participants -> `ABORTED` |
| `COMMITTING` | Re-send COMMITs -> `COMMITTED` |
| `ABORTING` | Re-send ABORTs -> `ABORTED` |

The WAL (writing `COMMITTING`/`ABORTING` to Redis before sending phase 2 messages) is what makes this safe: if the orchestrator crashes after deciding to commit but before sending commits, recovery sees `COMMITTING` and re-sends.

---

## 12. Events & Observability

**File:** `orchestrator/events.py`

### Event Stream

- **Stream:** `{saga:events}:checkout`
- **Max length:** 10,000 (approximate trimming)
- **Publishing:** fire-and-forget (never blocks the checkout path)

### Event Types

| Event | When Published | Extra Fields |
|---|---|---|
| `checkout_started` | SAGA record created | `item_count` |
| `stock_reserved` | All items reserved | -- |
| `stock_failed` | Any item fails reservation | `failed_step`, `error_type`, `error_message` |
| `payment_completed` | Payment charged | -- |
| `payment_failed` | Payment charge fails | `failed_step`, `error_type`, `error_message` |
| `compensation_triggered` | Entering COMPENSATING state | `failed_step`, `error_type`, `retry_count` |
| `compensation_completed` | Compensation finished | -- |
| `saga_completed` | SAGA reaches COMPLETED | -- |
| `saga_failed` | SAGA reaches FAILED | -- |

### Event Schema

```json
{
  "schema_version": "v1",
  "event_type": "checkout_started",
  "saga_id": "{saga:order-123}",
  "order_id": "order-123",
  "user_id": "user-456",
  "timestamp": "1710590400"
}
```

### Event Consumers (`orchestrator/consumers.py`)

**Compensation Handler** (`compensation_consumer`):
- Consumer group: `compensation-handler`
- Listens for `compensation_triggered` events
- Autoclaims idle messages (30s min idle)
- Dead-letters to `{saga:events}:dead-letters` after 5 delivery attempts
- Reads the SAGA record, verifies COMPENSATING state, re-runs compensation

**Audit Logger** (`audit_consumer`):
- Consumer group: `audit-logger`
- Logs all events: `"SAGA_EVENT {type} order={order_id} id={msg_id}"`
- Best-effort: always ACKs, never dead-letters

---

## 13. Circuit Breaker

**File:** `orchestrator/circuit.py`

Two independent circuit breakers protect the orchestrator from cascading failures:

```
stock_breaker:   failure_threshold=5, recovery_timeout=30s
payment_breaker: failure_threshold=5, recovery_timeout=30s
```

**Behavior:**
- **Closed (normal):** Requests pass through. Count consecutive `grpc.aio.AioRpcError` failures.
- **Open (after 5 failures):** All requests immediately raise `CircuitBreakerError`. No network calls made.
- **Half-open (after 30s):** One probe request allowed through. If it succeeds -> Closed. If it fails -> Open again.

**Integration with SAGA:**
- In `retry_forward()`: `CircuitBreakerError` is raised immediately (not retried)
- In `run_checkout()`: `CircuitBreakerError` triggers compensation
- In `recovery.py`: `CircuitBreakerError` during recovery also triggers compensation

Stock failures do NOT affect the payment breaker, and vice versa.

---

## 14. Redis Key Reference

| Key Pattern | Type | Service | Purpose | TTL |
|---|---|---|---|---|
| `<order_id>` | String (msgpack) | Order | Order record (items, user, cost, paid) | None |
| `{saga:<order_id>}` | Hash | Orchestrator | SAGA state machine | 7 days |
| `{tpc:<order_id>}` | Hash | Orchestrator | 2PC state machine | 7 days |
| `{item:<item_id>}` | String (msgpack) | Stock | Stock count + price | None |
| `{item:<item_id>}:idempotency:<key>` | String (JSON) | Stock | Cached operation result | 86400s |
| `{item:<item_id>}:hold:<order_id>` | String (int) | Stock | 2PC prepare hold | 7 days |
| `{user:<user_id>}` | String (msgpack) | Payment | Credit balance | None |
| `{user:<user_id>}:idempotency:<key>` | String (JSON) | Payment | Cached operation result | 86400s |
| `{user:<user_id>}:hold:<order_id>` | String (int) | Payment | 2PC prepare hold | 7 days |
| `{queue}:stock:commands` | Stream | Transport | Stock command queue | maxlen 1000 |
| `{queue}:payment:commands` | Stream | Transport | Payment command queue | maxlen 1000 |
| `{queue}:replies` | Stream | Transport | Reply queue | maxlen 1000 |
| `{saga:events}:checkout` | Stream | Events | Checkout event log | maxlen 10000 |
| `{saga:events}:dead-letters` | Stream | Events | Failed event processing | None |

---

## 15. gRPC Proto Definitions

### orchestrator.proto (Order -> Orchestrator)

```protobuf
service OrchestratorService {
  rpc StartCheckout(CheckoutRequest) returns (CheckoutResponse);
}

message CheckoutRequest {
  string order_id = 1;
  string user_id = 2;
  repeated LineItem items = 3;
  int32 total_cost = 4;
}

message LineItem {
  string item_id = 1;
  int32 quantity = 2;
}

message CheckoutResponse {
  bool success = 1;
  string error_message = 2;
}
```

### stock.proto (Orchestrator -> Stock)

```protobuf
service StockService {
  rpc ReserveStock(ReserveStockRequest) returns (StockResponse);
  rpc ReleaseStock(ReleaseStockRequest) returns (StockResponse);
  rpc CheckStock(CheckStockRequest) returns (CheckStockResponse);
  rpc PrepareStock(PrepareStockRequest) returns (StockResponse);
  rpc CommitStock(CommitStockRequest) returns (StockResponse);
  rpc AbortStock(AbortStockRequest) returns (StockResponse);
}

message ReserveStockRequest {
  string item_id = 1;
  int32 quantity = 2;
  string idempotency_key = 3;
}

message StockResponse {
  bool success = 1;
  string error_message = 2;
}
```

### payment.proto (Orchestrator -> Payment)

```protobuf
service PaymentService {
  rpc ChargePayment(ChargePaymentRequest) returns (PaymentResponse);
  rpc RefundPayment(RefundPaymentRequest) returns (PaymentResponse);
  rpc CheckPayment(CheckPaymentRequest) returns (CheckPaymentResponse);
  rpc PreparePayment(PreparePaymentRequest) returns (PaymentResponse);
  rpc CommitPayment(CommitPaymentRequest) returns (PaymentResponse);
  rpc AbortPayment(AbortPaymentRequest) returns (PaymentResponse);
}

message ChargePaymentRequest {
  string user_id = 1;
  int32 amount = 2;
  string idempotency_key = 3;
}

message PaymentResponse {
  bool success = 1;
  string error_message = 2;
}
```

---

## 16. End-to-End Flow Diagrams

### Happy Path (SAGA + gRPC)

```
Client                Order              Orchestrator           Stock              Payment
  |                     |                     |                    |                   |
  | POST /checkout/X    |                     |                    |                   |
  |-------------------->|                     |                    |                   |
  |                     | Read order from     |                    |                   |
  |                     | Redis {X}           |                    |                   |
  |                     |                     |                    |                   |
  |                     | gRPC StartCheckout  |                    |                   |
  |                     |-------------------->|                    |                   |
  |                     |                     |                    |                   |
  |                     |                     | Create {saga:X}    |                   |
  |                     |                     | state=STARTED      |                   |
  |                     |                     |                    |                   |
  |                     |                     | Publish            |                   |
  |                     |                     | checkout_started   |                   |
  |                     |                     |                    |                   |
  |                     |                     | gRPC ReserveStock  |                   |
  |                     |                     |------------------->|                   |
  |                     |                     |                    | Lua: CAS write    |
  |                     |                     |                    | stock -= qty      |
  |                     |                     |<-------------------|                   |
  |                     |                     | success            |                   |
  |                     |                     |                    |                   |
  |                     |                     | state=STOCK_RESERVED                   |
  |                     |                     |                    |                   |
  |                     |                     | gRPC ChargePayment |                   |
  |                     |                     |------------------------------------- ->|
  |                     |                     |                    |                   |
  |                     |                     |                    |  Lua: CAS write   |
  |                     |                     |                    |  credit -= amount |
  |                     |                     |<--------------------------------------+|
  |                     |                     | success            |                   |
  |                     |                     |                    |                   |
  |                     |                     | state=COMPLETED    |                   |
  |                     |                     |                    |                   |
  |                     | CheckoutResponse    |                    |                   |
  |                     |<--------------------|                    |                   |
  |                     | success=true        |                    |                   |
  |                     |                    |                    |                   |
  |                     | Mark order.paid=true                    |                   |
  |  HTTP 200           |                     |                    |                   |
  |<--------------------|                     |                    |                   |
```

### Failure Path (SAGA + Compensation)

```
Client                Order              Orchestrator           Stock              Payment
  |                     |                     |                    |                   |
  | POST /checkout/X    |                     |                    |                   |
  |-------------------->|                     |                    |                   |
  |                     | gRPC StartCheckout  |                    |                   |
  |                     |-------------------->|                    |                   |
  |                     |                     |                    |                   |
  |                     |                     | Create {saga:X}    |                   |
  |                     |                     |                    |                   |
  |                     |                     | ReserveStock ----->| OK               |
  |                     |                     | state=STOCK_RESERVED                   |
  |                     |                     |                    |                   |
  |                     |                     | ChargePayment --------------------------->|
  |                     |                     |                    | Insufficient credit|
  |                     |                     |<-------------------------------- FAIL ----|
  |                     |                     |                    |                   |
  |                     |                     | state=COMPENSATING |                   |
  |                     |                     |                    |                   |
  |                     |                     | +-- COMPENSATION --+                   |
  |                     |                     | | (reverse order)  |                   |
  |                     |                     | |                  |                   |
  |                     |                     | | skip refund      |                   |
  |                     |                     | | (not charged)    |                   |
  |                     |                     | |                  |                   |
  |                     |                     | | ReleaseStock --->| stock += qty      |
  |                     |                     | |<-----------------| OK                |
  |                     |                     | +------------------+                   |
  |                     |                     |                    |                   |
  |                     |                     | state=FAILED       |                   |
  |                     |                     |                    |                   |
  |                     | CheckoutResponse    |                    |                   |
  |                     |<--------------------|                    |                   |
  |  HTTP 400           | success=false       |                    |                   |
  |<--------------------| "Insufficient credit"                    |                   |
```

### Happy Path (2PC + gRPC)

```
Client                Order              Orchestrator           Stock              Payment
  |                     |                     |                    |                   |
  | POST /checkout/X    |                     |                    |                   |
  |-------------------->|                     |                    |                   |
  |                     | gRPC StartCheckout  |                    |                   |
  |                     |-------------------->|                    |                   |
  |                     |                     |                    |                   |
  |                     |                     | Create {tpc:X}     |                   |
  |                     |                     | state=INIT         |                   |
  |                     |                     |                    |                   |
  |                     |                     | state=PREPARING    |                   |
  |                     |                     |                    |                   |
  |                     |                     | -- PREPARE (concurrent) --             |
  |                     |                     | PrepareStock ----->| hold + deduct     |
  |                     |                     | PreparePayment ----------------------->|
  |                     |                     |                    | hold + deduct     |
  |                     |                     |<--- YES -----------|                   |
  |                     |                     |<------------------------------ YES ----|
  |                     |                     |                    |                   |
  |                     |                     | WAL: state=COMMITTING                  |
  |                     |                     |                    |                   |
  |                     |                     | -- COMMIT (concurrent) --              |
  |                     |                     | CommitStock ------>| DEL hold key      |
  |                     |                     | CommitPayment ------------------------>|
  |                     |                     |                    | DEL hold key      |
  |                     |                     |<--- OK ------------|                   |
  |                     |                     |<------------------------------- OK ----|
  |                     |                     |                    |                   |
  |                     |                     | state=COMMITTED    |                   |
  |                     |                     |                    |                   |
  |                     | CheckoutResponse    |                    |                   |
  |                     |<--------------------|                    |                   |
  |  HTTP 200           | success=true        |                    |                   |
  |<--------------------|                     |                    |                   |
```

### Queue Mode Flow (SAGA)

```
Orchestrator           Redis Streams              Stock Service
     |                       |                         |
     | XADD {queue}:stock:commands                     |
     | {correlation_id, "reserve_stock", payload}      |
     |---------------------->|                         |
     |                       |                         |
     |  (Future registered   | XREADGROUP               |
     |   in pending_replies) | stock-consumers/stock-1  |
     |                       |------------------------>|
     |                       |                         |
     |                       |                         | Execute
     |                       |                         | reserve_stock()
     |                       |                         |
     |                       | XADD {queue}:replies    |
     |                       |<------------------------|
     |                       | {correlation_id, result} |
     |                       |                         |
     | reply_listener reads  |                         |
     | resolves Future       |                         |
     |<----------------------|                         |
     |                       |                         |
     | reserve_stock() returns                         |
```

---

## Summary of All Calls in Happy Path (SAGA + gRPC)

| Step | From | To | Protocol | Call | Redis Operation |
|------|------|----|----------|------|-----------------|
| 1 | Client | Order Service | HTTP POST | `/checkout/{order_id}` | `GET order_id` (msgpack) |
| 2 | Order Service | Orchestrator | gRPC | `StartCheckout(order_id, user_id, items, total_cost)` | -- |
| 3 | Orchestrator | -- | -- | -- | `HSETNX {saga:order_id}` (create SAGA record) |
| 4 | Orchestrator | -- | -- | -- | `XADD {saga:events}:checkout` (checkout_started) |
| 5 | Orchestrator | Stock Service | gRPC | `ReserveStock(item_id, qty, idempotency_key)` | Lua: check idem key + CAS subtract stock |
| 6 | Orchestrator | -- | -- | -- | Lua CAS: `STARTED -> STOCK_RESERVED` |
| 7 | Orchestrator | -- | -- | -- | `XADD {saga:events}:checkout` (stock_reserved) |
| 8 | Orchestrator | Payment Service | gRPC | `ChargePayment(user_id, amount, idempotency_key)` | Lua: check idem key + CAS subtract credit |
| 9 | Orchestrator | -- | -- | -- | Lua CAS: `STOCK_RESERVED -> PAYMENT_CHARGED` |
| 10 | Orchestrator | -- | -- | -- | `XADD {saga:events}:checkout` (payment_completed) |
| 11 | Orchestrator | -- | -- | -- | Lua CAS: `PAYMENT_CHARGED -> COMPLETED` |
| 12 | Orchestrator | -- | -- | -- | `XADD {saga:events}:checkout` (saga_completed) |
| 13 | Order Service | -- | -- | -- | `SET order_id` with `paid=true` |

**Total: 3 gRPC calls, ~8 Redis operations, 4 stream events**
