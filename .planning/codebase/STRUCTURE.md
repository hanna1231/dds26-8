# Codebase Structure

**Analysis Date:** 2026-02-27

## Directory Layout

```
dds26-8/
├── order/                          # Order microservice implementation
│   ├── app.py                      # Flask application and endpoints
│   ├── Dockerfile                  # Container image for order service
│   └── requirements.txt            # Python dependencies
├── payment/                        # Payment microservice implementation
│   ├── app.py                      # Flask application and endpoints
│   ├── Dockerfile                  # Container image for payment service
│   └── requirements.txt            # Python dependencies
├── stock/                          # Stock microservice implementation
│   ├── app.py                      # Flask application and endpoints
│   ├── Dockerfile                  # Container image for stock service
│   └── requirements.txt            # Python dependencies
├── test/                           # Integration and functional tests
│   ├── test_microservices.py       # Test suite with unittest
│   └── utils.py                    # Test helper functions and API client
├── k8s/                            # Kubernetes deployment manifests
│   ├── order-app.yaml              # Order service k8s deployment and service
│   ├── stock-app.yaml              # Stock service k8s deployment and service
│   ├── user-app.yaml               # Payment service k8s deployment and service
│   └── ingress-service.yaml        # Ingress configuration for k8s
├── helm-config/                    # Helm chart values for infrastructure
├── env/                            # Environment variable files
│   ├── order_redis.env             # Order service Redis connection config
│   ├── payment_redis.env           # Payment service Redis connection config
│   └── stock_redis.env             # Stock service Redis connection config
├── docker-compose.yml              # Docker compose for local development
├── gateway_nginx.conf              # Nginx configuration for API gateway
├── deploy-charts-minikube.sh        # Script to deploy Redis helm chart to minikube
├── deploy-charts-cluster.sh         # Script to deploy Redis and ingress to cloud k8s
├── requirements.txt                # Root-level test dependencies
├── README.md                       # Project documentation
└── .planning/                      # GSD planning directory
    └── codebase/                   # Codebase analysis documents
```

## Directory Purposes

**order/:**
- Purpose: Order management microservice
- Contains: Flask application code, dependencies, container image
- Key files: `app.py` (6200 bytes - largest service, handles transaction coordination)

**payment/:**
- Purpose: User payment and credit management microservice
- Contains: Flask application code, dependencies, container image
- Key files: `app.py` (3428 bytes - simple user and credit operations)

**stock/:**
- Purpose: Inventory and item management microservice
- Contains: Flask application code, dependencies, container image
- Key files: `app.py` (3599 bytes - item and stock operations)

**test/:**
- Purpose: Integration tests and test utilities
- Contains: Unittest test cases, API client helper functions
- Key files: `test_microservices.py` (147 lines, 3 test classes), `utils.py` (HTTP request wrappers)

**k8s/:**
- Purpose: Kubernetes deployment configuration
- Contains: Service, Deployment, and Ingress manifests for containerized deployment
- Key files: Four YAML files with service definitions and ingress rules

**helm-config/:**
- Purpose: Infrastructure-as-code for Helm package manager
- Contains: Chart values for Redis and other dependencies
- Generated: Yes (by helm during deployment)
- Committed: Yes

**env/:**
- Purpose: Environment variable configuration files
- Contains: Service-specific Redis connection details
- Committed: Yes (contains non-sensitive connection defaults)

## Key File Locations

**Entry Points:**
- `order/app.py`: Flask app initialization at line 20, main() at line 181-182
- `payment/app.py`: Flask app initialization at line 14, main() at line 109-110
- `stock/app.py`: Flask app initialization at line 14, main() at line 112-113
- Gateway: `gateway_nginx.conf` lines 4-26 define upstream servers and routing

**Configuration:**
- Docker: `docker-compose.yml` (55 lines) - Development environment with 7 services
- Kubernetes: `k8s/order-app.yaml`, `k8s/stock-app.yaml`, `k8s/user-app.yaml` - Production k8s configs
- Nginx: `gateway_nginx.conf` - API gateway routing rules
- Environment: `env/*.env` files - Redis connection parameters

**Core Logic:**
- Order orchestration: `order/app.py` lines 126-178 (add_item, checkout with rollback)
- Payment management: `payment/app.py` lines 82-106 (add_credit, remove_credit with validation)
- Stock management: `stock/app.py` lines 85-109 (add_stock, remove_stock with validation)
- Data serialization: All app.py files use msgspec.msgpack for Struct serialization

**Testing:**
- Test suite: `test/test_microservices.py` (147 lines)
- Test fixtures: `test/utils.py` (72 lines) with API wrapper functions
- Run command: `python -m unittest test/test_microservices.py` (from test directory)

## Naming Conventions

**Files:**
- Service apps: `{service_name}/app.py` (order/app.py, payment/app.py, stock/app.py)
- Containers: `Dockerfile` (same name in each service directory)
- Config: `{service_name}_redis.env` in env/ directory
- K8s: `{service_name}-app.yaml` for deployment, `ingress-service.yaml` for routing
- Test: `test_microservices.py` for suite, `utils.py` for helpers

**Functions:**
- Data retrieval: `get_{entity}_from_db()` (get_order_from_db, get_user_from_db, get_item_from_db)
- HTTP requests: `send_{method}_request()` (send_get_request, send_post_request)
- Endpoints: Present tense verb-first: create_order, find_order, add_item, checkout
- Rollback operations: `rollback_{entity}()` (rollback_stock)

**Variables:**
- Database connection: `db` (global redis.Redis instance)
- Request responses: `{entity}_reply` (item_reply, user_reply, stock_reply)
- Entry objects: `{entity}_entry` (order_entry, user_entry, item_entry)
- Dictionaries: Plural nouns with underscores: `items_quantities`, `removed_items`, `kv_pairs`

**Types (Structs):**
- Domain models: PascalCase with "Value" suffix: OrderValue, UserValue, StockValue
- All are msgspec.Struct subclasses with public fields

## Where to Add New Code

**New Feature (add endpoint):**
- Primary code: Add function to relevant `{service}/app.py` with `@app.post()` or `@app.get()` decorator
- Error handling: Use `abort(400, message)` for validation failures
- Database access: Call `get_{entity}_from_db()` then modify and `db.set(key, msgpack.encode(entity))`
- Tests: Add test method to `test/test_microservices.py` class and corresponding helper to `test/utils.py`
- Example: Order service checkout is in `order/app.py` lines 149-178 with test in `test/test_microservices.py` lines 77-143

**New Microservice:**
- Create directory: `{new_service}/`
- Files needed:
  - `{new_service}/app.py` with Flask app, Struct data model, endpoints
  - `{new_service}/Dockerfile` using Python 3.11+ base image
  - `{new_service}/requirements.txt` with Flask, redis, msgspec, requests, gunicorn
- Configuration:
  - Add entry to `docker-compose.yml` for service and corresponding Redis database
  - Add `env/{new_service}_redis.env` file with Redis connection details
  - Add location block to `gateway_nginx.conf` for routing
  - Add `k8s/{new_service}-app.yaml` with Service and Deployment
- Testing: Add test class to `test/test_microservices.py` using pattern in `test/utils.py`

**Utilities (shared helpers):**
- In test context: Add function to `test/utils.py` following naming pattern (POST for mutations, GET for queries)
- In service context: Add helper function in respective `app.py` (see `get_X_from_db()` pattern at lines 33-44 in each service)
- Serialization: Use existing msgspec pattern with typed Struct classes

**Cross-service communication:**
- Client code: Use requests library with try-except in `send_get_request()` or `send_post_request()` wrapper
- Error propagation: Return abort(400, message) on RequestException
- URL format: Construct using GATEWAY_URL env var for service-to-service calls
- Example: `order/app.py` line 129 shows pattern for querying Stock service

## Special Directories

**k8s/:**
- Purpose: Kubernetes manifests for container orchestration
- Generated: No (hand-written manifests)
- Committed: Yes
- Usage: `kubectl apply -f .` in k8s directory after building images

**helm-config/:**
- Purpose: Helm chart configuration for Redis and ingress installation
- Generated: No (hand-written values files)
- Committed: Yes (charts themselves generated by helm during `helm install`)
- Usage: Referenced by `deploy-charts-minikube.sh` and `deploy-charts-cluster.sh`

**env/:**
- Purpose: Environment variable files for docker-compose
- Generated: No (static configuration)
- Committed: Yes (contains non-sensitive defaults)
- Usage: Loaded via `env_file:` in docker-compose.yml for each service

**.planning/codebase/:**
- Purpose: GSD (Goal-Source-Documents) codebase analysis and planning documents
- Generated: Yes (by gsd mapping tools)
- Committed: Yes (part of planning workflow)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, STACK.md, INTEGRATIONS.md, CONCERNS.md

---

*Structure analysis: 2026-02-27*
