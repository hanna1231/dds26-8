# Architecture

**Analysis Date:** 2026-02-27

## Pattern Overview

**Overall:** Distributed Microservices Architecture with API Gateway

**Key Characteristics:**
- Three independent microservices (Order, Payment, Stock) with isolated Redis databases
- Nginx reverse proxy gateway routing requests to services
- Synchronous REST API communication between services
- Transactional coordination at the Order service layer
- Stateless service instances with persistent data in Redis

## Layers

**API Gateway Layer:**
- Purpose: Unified entry point for all client requests, routing to appropriate microservices
- Location: `gateway_nginx.conf` (Docker container definition in `docker-compose.yml` lines 4-13)
- Contains: Nginx configuration with upstream servers and location-based routing
- Depends on: Order, Payment, Stock services running on specified ports
- Used by: All external clients

**Order Service:**
- Purpose: Manages order creation, item addition, checkout, and orchestrates cross-service transactions
- Location: `/Users/daniel/WebstormProjects/dds26-8/order/app.py`
- Contains: Flask application, REST endpoints, transaction coordination logic, rollback handlers
- Depends on: Stock service (for inventory checks), Payment service (for payment processing), Order Redis database
- Used by: Client requests via API gateway

**Payment Service:**
- Purpose: Manages user accounts, credit balance, and payment operations
- Location: `/Users/daniel/WebstormProjects/dds26-8/payment/app.py`
- Contains: Flask application, user CRUD operations, credit management endpoints
- Depends on: Payment Redis database
- Used by: Order service during checkout operations

**Stock Service:**
- Purpose: Manages inventory for items, tracks stock levels and item pricing
- Location: `/Users/daniel/WebstormProjects/dds26-8/stock/app.py`
- Contains: Flask application, item creation, stock adjustment endpoints
- Depends on: Stock Redis database
- Used by: Order service during item addition and checkout

**Data Layer:**
- Purpose: Persistent storage for each service's domain data
- Location: Three Redis instances (order-db, stock-db, payment-db in `docker-compose.yml`)
- Contains: Serialized Struct objects (OrderValue, UserValue, StockValue) stored as msgpack-encoded binary
- Depends on: None
- Used by: Each respective service

## Data Flow

**Create Order Flow:**

1. Client calls `POST /orders/create/{user_id}` through gateway
2. Order service receives request at `create_order()` function
3. Order service generates UUID for order_id
4. Creates new OrderValue struct (paid=False, items=[], user_id, total_cost=0)
5. Serializes OrderValue using msgpack into Redis
6. Returns order_id to client

**Add Item to Order Flow:**

1. Client calls `POST /orders/addItem/{order_id}/{item_id}/{quantity}` through gateway
2. Order service fetches current order from Redis using `get_order_from_db()`
3. Order service queries Stock service: `GET /stock/find/{item_id}`
4. Stock service returns item price
5. Order service updates order's items list and total_cost
6. Serializes updated OrderValue back to Redis
7. Returns success response with updated total_cost

**Checkout (Transaction) Flow:**

1. Client calls `POST /orders/checkout/{order_id}` through gateway
2. Order service fetches order from Redis using `get_order_from_db()`
3. Deduplicates items by accumulating quantities per item_id in dictionary
4. For each unique item:
   - Calls Stock service: `POST /stock/subtract/{item_id}/{quantity}`
   - Tracks successfully subtracted items in `removed_items` list
   - If any stock subtraction fails: calls `rollback_stock()` to reverse prior subtractions
5. If all stock subtractions succeed:
   - Calls Payment service: `POST /payment/pay/{user_id}/{total_cost}`
   - If payment fails: calls `rollback_stock()` to restore inventory
6. If payment succeeds:
   - Updates order.paid = True in Redis
   - Returns success response
7. On any failure at any stage, rolls back all successfully completed stock operations

**State Management:**
- Order state: Stored in Redis with two key fields: `paid` (boolean) and `items` (list of tuples)
- User credit state: Stored in Redis as integer, mutations are immediate
- Stock state: Stored in Redis as integer, mutations are immediate with validation before commit
- No distributed transaction coordination (2PC): Service uses application-level compensating transactions (rollback) for atomicity

## Key Abstractions

**OrderValue (Struct):**
- Purpose: Represents a single order with all associated data
- Examples: `order/app.py` lines 35-39
- Pattern: msgpack-serializable Struct containing paid (bool), items (list), user_id (str), total_cost (int)
- Used to enforce schema on stored data and enable type checking

**UserValue (Struct):**
- Purpose: Represents user payment account with credit balance
- Examples: `payment/app.py` lines 29-30
- Pattern: Simple Struct with single field `credit: int`
- Used for serialization to Redis

**StockValue (Struct):**
- Purpose: Represents inventory item with stock count and price
- Examples: `stock/app.py` lines 29-31
- Pattern: Struct with two integer fields: stock and price

**Service Helper Functions:**
- `get_X_from_db()`: Retrieves, deserializes, and validates entity existence from Redis
- `send_get_request()` / `send_post_request()`: Wraps requests with error handling, aborts on RequestException
- `rollback_stock()`: Iterates through items list and posts add operations to reverse subtractions

## Entry Points

**Order Service:**
- Location: `order/app.py` lines 56-178
- Triggers: HTTP POST/GET requests routed by gateway
- Responsibilities:
  - Create orders
  - Query orders
  - Add items to orders with price calculation
  - Execute transactional checkout with cross-service coordination

**Payment Service:**
- Location: `payment/app.py` lines 47-106
- Triggers: HTTP POST/GET requests (direct calls from Order service, or via gateway)
- Responsibilities:
  - Create user accounts
  - Query user credit balances
  - Add/remove credit with validation

**Stock Service:**
- Location: `stock/app.py` lines 48-109
- Triggers: HTTP POST/GET requests (direct calls from Order service, or via gateway)
- Responsibilities:
  - Create items with pricing
  - Query item details
  - Add/subtract stock with validation

## Error Handling

**Strategy:** Flask `abort()` with HTTP status codes (400 for client errors)

**Patterns:**
- Database errors: `abort(400, DB_ERROR_STR)` on Redis exceptions (lines in all services)
- Request errors: `abort(400, REQ_ERROR_STR)` on network/HTTP failures (order/app.py lines 108-123)
- Business logic violations: `abort(400, custom message)` for:
  - Missing entities: "Order: {id} not found!"
  - Insufficient stock: "Item: {item_id} stock cannot get reduced below zero!"
  - Insufficient credit: "User: {user_id} credit cannot get reduced below zero!"
  - Out of stock during checkout: "Out of stock on item_id: {item_id}"
  - Out of credit during checkout: "User out of credit"

**No try-catch blocks in endpoints:** Errors propagate as Flask exceptions, returning 400 with error message body

## Cross-Cutting Concerns

**Logging:**
- Framework: Flask logger (`app.logger.debug()`)
- Used in: Payment service checkout (payment/app.py line 96), Stock service stock removal (stock/app.py line 102), Order service checkout (order/app.py lines 151, 177)
- Pattern: Debug-level logging for operation tracking during critical operations

**Validation:**
- Immediate: Negative balance checks (payment service line 100, stock service line 103)
- Remote: Item existence check via Stock service query (order service line 129)
- Schema: Type validation through msgpack deserialization (all services)

**Authentication:** Not implemented - All endpoints publicly accessible

**Authorization:** Not implemented - Any user can operate on any order/user/item

---

*Architecture analysis: 2026-02-27*
