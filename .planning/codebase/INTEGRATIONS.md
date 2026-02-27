# External Integrations

**Analysis Date:** 2026-02-27

## APIs & External Services

**Inter-Service Communication:**
- Order Service to Stock Service - HTTP GET/POST requests via gateway
  - SDK/Client: `requests` library (requests 2.31.0)
  - Endpoints: `/stock/find/<item_id>`, `/stock/subtract/<item_id>/<amount>`, `/stock/add/<item_id>/<amount>`
  - Gateway URL: Environment variable `GATEWAY_URL` (`http://gateway:80` in docker-compose, `http://gateway:80` in order service)

- Order Service to Payment Service - HTTP POST requests via gateway
  - SDK/Client: `requests` library (requests 2.31.0)
  - Endpoints: `/payment/pay/<user_id>/<amount>`
  - Gateway URL: Same as above

- Stock Service - Standalone, no outbound service calls

- Payment Service - Standalone, no outbound service calls

## Data Storage

**Databases:**
- Redis 7.2-bookworm - In-memory key-value store
  - Three separate instances for service isolation:
    - `order-db` - Order service storage (defined in `docker-compose.yml`)
    - `stock-db` - Stock service storage (defined in `docker-compose.yml`)
    - `payment-db` - Payment service storage (defined in `docker-compose.yml`)
  - Connection Details (per service env files in `/Users/daniel/WebstormProjects/dds26-8/env/`):
    - Port: 6379
    - Password: "redis" (defined in docker-compose command)
    - Database: 0
  - Client: redis-py 5.0.3
  - Data serialization: msgspec MessagePack (msgspec 0.18.6)

**File Storage:**
- Not applicable - All data persisted in Redis

**Caching:**
- Built into Redis - Data structures stored directly

## Authentication & Identity

**Auth Provider:**
- Custom implementation - No external identity provider
- User identification via UUID string (`uuid.uuid4()`)
- Each service maintains its own user/entity namespace:
  - Payment service: UserValue structs with UUID keys
  - Stock service: StockValue structs with UUID keys
  - Order service: OrderValue structs with UUID keys
- No API key validation or OAuth/JWT tokens implemented

## Monitoring & Observability

**Error Tracking:**
- Not configured - No external error tracking service integrated

**Logs:**
- Flask development logger (app.logger)
- Gunicorn error logger in production (gunicorn.error)
- Log level: INFO (set in docker-compose: `--log-level=info`)
- Log level in K8s: Application default (DEBUG calls present in code)
- Access logs: Nginx access logs written to `/var/log/nginx/server.access.log` and `/var/log/nginx/access.log` (defined in `gateway_nginx.conf`)
- No centralized logging system configured

## CI/CD & Deployment

**Hosting:**
- Docker Compose - Local development
- Kubernetes - Production deployments
- Cloud agnostic - Can deploy to any K8s cluster (AWS EKS, Google GKE, Azure AKS, etc.)

**CI Pipeline:**
- Not configured - No CI/CD service detected

## Environment Configuration

**Required env vars for local development (docker-compose):**
- `GATEWAY_URL` - Order service needs gateway URL
- `REDIS_HOST` - Service hostname for Redis connection
- `REDIS_PORT` - Redis port number
- `REDIS_PASSWORD` - Redis authentication password
- `REDIS_DB` - Redis database index

**Secrets location:**
- Development: Plain text in `/Users/daniel/WebstormProjects/dds26-8/env/*.env` files
- Production: K8s environment variables in deployment manifests (K8s would use Secrets in real scenario)
- Current docker-compose uses hardcoded password "redis"

## Webhooks & Callbacks

**Incoming:**
- Not configured - No webhook endpoints listening

**Outgoing:**
- Not configured - Only synchronous HTTP calls to other services

## Service Communication Pattern

**Architecture:**
- Synchronous request-response via HTTP/REST
- Order service acts as orchestrator:
  - Calls stock service to verify item exists and deduct stock
  - Calls payment service to process payment
  - Implements rollback logic if stock or payment operations fail
- No message queue or async event system
- No service discovery - Services reference each other by hostname (defined in docker-compose service names)

## API Gateway

**Nginx Reverse Proxy:**
- Config file: `/Users/daniel/WebstormProjects/dds26-8/gateway_nginx.conf`
- Listening port: 80 (exposed as 8000 in docker-compose)
- Routes requests to backend services:
  - `/orders/` → order-service:5000
  - `/payment/` → payment-service:5000
  - `/stock/` → stock-service:5000
- Worker connections: 2048
- No rate limiting configured
- No authentication at gateway level

---

*Integration audit: 2026-02-27*
