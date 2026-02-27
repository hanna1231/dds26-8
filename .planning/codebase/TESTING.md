# Testing Patterns

**Analysis Date:** 2026-02-27

## Test Framework

**Runner:**
- unittest (Python standard library)
- Config: No explicit config file; uses default unittest discovery

**Assertion Library:**
- unittest.TestCase built-in assertions: `self.assertIn()`, `self.assertEqual()`, `self.assertTrue()`

**Run Commands:**
```bash
python -m unittest discover -s test -p "test_*.py"  # Run all tests
python -m unittest test.test_microservices.TestMicroservices  # Run specific test class
python -m unittest test.test_microservices.TestMicroservices.test_stock  # Run specific test
```

## Test File Organization

**Location:**
- Separate directory: `test/` at project root
- Co-located with source services: `order/`, `payment/`, `stock/` at same level

**Naming:**
- Test module: `test_microservices.py` (follows `test_*.py` pattern)
- Test class: `TestMicroservices` (inherits from `unittest.TestCase`)
- Test methods: `test_stock()`, `test_payment()`, `test_order()` (descriptive names starting with `test_`)

**Structure:**
```
test/
├── test_microservices.py    # Integration tests for all three services
└── utils.py                 # HTTP helper functions for tests
```

## Test Structure

**Suite Organization:**
```python
class TestMicroservices(unittest.TestCase):

    def test_stock(self):
        # Setup: create item with price
        item: dict = tu.create_item(5)
        self.assertIn('item_id', item)

        # Test operation and verify result
        add_stock_response = tu.add_stock(item_id, 50)
        self.assertTrue(200 <= int(add_stock_response) < 300)

        # Verify state changed correctly
        stock_after_add: int = tu.find_item(item_id)['stock']
        self.assertEqual(stock_after_add, 50)
```

**Patterns:**
- No setUp() or tearDown() methods (services and Redis state persist between tests)
- Test methods create fresh data for each test (new users, items, orders)
- Assertions check both response format (assertIn for keys) and values (assertEqual)
- Helper functions wrap HTTP calls; tests use helper functions exclusively
- Tests verify both happy path and error conditions (e.g., over-subtract fails)

## Mocking

**Framework:** None - tests use real HTTP calls to running services

**Patterns:**
```python
# Direct HTTP calls via helper functions
item_reply = send_get_request(f"{GATEWAY_URL}/stock/find/{item_id}")
if item_reply.status_code != 200:
    abort(400, f"Item: {item_id} does not exist!")
```

**What to Mock:**
- Nothing currently mocked; all tests are integration tests hitting real services
- Tests assume services running at `http://127.0.0.1:8000`

**What NOT to Mock:**
- External HTTP calls to other microservices (intentionally test service integration)
- Redis database operations (use real Redis instance)
- Flask app behavior (use real Flask routes)

## Fixtures and Factories

**Test Data:**
```python
def test_order(self):
    # Factory pattern: create fresh entities for each test
    user: dict = tu.create_user()
    order: dict = tu.create_order(user_id)
    item1: dict = tu.create_item(5)

    # Bulk creation helper available in services
    # tu.batch_init_users(n, starting_money)
    # tu.batch_init_items(n, starting_stock, item_price)
```

**Location:**
- No fixture files; test data created dynamically via service endpoints
- Helper functions in `test/utils.py` provide factory-like creation functions
- Batch creation endpoints available in services for performance/scale testing

## Coverage

**Requirements:** No coverage requirements enforced

**View Coverage:**
```bash
python -m coverage run -m unittest discover -s test
python -m coverage report -m
python -m coverage html  # Generate HTML report
```

## Test Types

**Unit Tests:**
- Not used in this codebase; all tests are integration tests
- Individual service functions not tested in isolation

**Integration Tests:**
- Full scope: test complete workflows across all three services
- Tests verify:
  - Individual service operations (stock operations, payment operations)
  - Cross-service interactions (order service calls stock and payment services)
  - Distributed transaction behavior (rollback on payment failure)
  - Data consistency after operations
- Examples in `test_microservices.py`:
  - `test_stock()`: Tests create, find, add, subtract operations
  - `test_payment()`: Tests user creation, credit management, payment
  - `test_order()`: Tests complete order checkout with multi-service coordination and rollback

**E2E Tests:**
- Integration tests serve as E2E tests (no separate E2E framework)
- Each test method represents a complete user journey
- `test_order()` includes ~10 service calls testing the full checkout flow

## Common Patterns

**Async Testing:**
- Not used - services are synchronous Flask apps with blocking Redis calls

**Error Testing:**
```python
def test_order(self):
    # Test failure path: out of stock
    subtract_stock_response = tu.subtract_stock(item_id, 200)
    self.assertTrue(tu.status_code_is_failure(int(subtract_stock_response)))

    # Test rollback: checkout fails if payment insufficient
    checkout_response = tu.checkout_order(order_id).status_code
    self.assertTrue(tu.status_code_is_failure(checkout_response))

    # Verify rollback occurred: stock not reduced
    stock_after_subtract: int = tu.find_item(item_id1)['stock']
    self.assertEqual(stock_after_subtract, 15)  # Unchanged after failed checkout
```

**Response Testing:**
```python
# Test response structure
item: dict = tu.create_item(5)
self.assertIn('item_id', item)  # Verify key exists

# Test status codes
response = tu.add_stock(item_id, 50)
self.assertTrue(tu.status_code_is_success(response))

# Test response bodies
order: dict = tu.find_order(order_id)
self.assertEqual(order['user_id'], user_id)
```

## Test Utilities

**Location:** `test/utils.py`

**Purpose:** HTTP wrapper functions providing a test-friendly API

**Functions by Service:**

Stock Service:
- `create_item(price: int) -> dict` - Create item, returns `{'item_id': str}`
- `find_item(item_id: str) -> dict` - Get item details, returns `{'stock': int, 'price': int}`
- `add_stock(item_id: str, amount: int) -> int` - Add stock, returns status code
- `subtract_stock(item_id: str, amount: int) -> int` - Reduce stock, returns status code

Payment Service:
- `create_user() -> dict` - Create user, returns `{'user_id': str}`
- `find_user(user_id: str) -> dict` - Get user details, returns `{'user_id': str, 'credit': int}`
- `add_credit_to_user(user_id: str, amount: float) -> int` - Add credit, returns status code
- `payment_pay(user_id: str, amount: int) -> int` - Charge user, returns status code

Order Service:
- `create_order(user_id: str) -> dict` - Create order, returns `{'order_id': str}`
- `find_order(order_id: str) -> dict` - Get order details
- `add_item_to_order(order_id: str, item_id: str, quantity: int) -> int` - Add item to order, returns status code
- `checkout_order(order_id: str) -> requests.Response` - Process checkout, returns full Response object

Status Helpers:
- `status_code_is_success(status_code: int) -> bool` - True if 200-299
- `status_code_is_failure(status_code: int) -> bool` - True if 400-499

## Service Test Dependencies

**Services Required:**
- All three microservices must be running (order, payment, stock)
- Redis instance must be running and accessible
- Services configured to connect to same Redis instance
- Nginx gateway configured to route requests (or direct port access if not testing through gateway)

**Configuration Assumptions:**
- All services listening on `http://127.0.0.1:8000`
- Redis on default host/port as configured in service environment variables
- No authentication required for test calls

---

*Testing analysis: 2026-02-27*
