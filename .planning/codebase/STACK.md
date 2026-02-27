# Technology Stack

**Analysis Date:** 2026-02-27

## Languages

**Primary:**
- Python 3.12 - All microservices (order, payment, stock)

**Secondary:**
- Bash - Deployment scripts
- Nginx configuration language - API gateway

## Runtime

**Environment:**
- Docker containerization for local and production deployment
- Kubernetes (K8s) for orchestrated deployments
- Minikube for local K8s testing

**Package Manager:**
- pip - Python package management
- Lockfile: No lockfile present (requirements.txt used directly)

## Frameworks

**Core:**
- Flask 3.0.2 - REST API framework for all three microservices (`/Users/daniel/WebstormProjects/dds26-8/order/app.py`, `/Users/daniel/WebstormProjects/dds26-8/payment/app.py`, `/Users/daniel/WebstormProjects/dds26-8/stock/app.py`)
- Gunicorn 21.2.0 - WSGI HTTP server for production serving (specified in docker-compose.yml and K8s deployments)

**Testing:**
- unittest - Built-in Python testing framework (`/Users/daniel/WebstormProjects/dds26-8/test/test_microservices.py`)
- No external test runner configured

**Build/Dev:**
- Docker Engine - Container runtime
- docker-compose 3.x - Local development orchestration (`/Users/daniel/WebstormProjects/dds26-8/docker-compose.yml`)
- Nginx 1.25-bookworm - API gateway and reverse proxy (`/Users/daniel/WebstormProjects/dds26-8/gateway_nginx.conf`)

## Key Dependencies

**Critical:**
- redis 5.0.3 - In-memory data store for all services (order, payment, stock databases)
- requests 2.31.0 - HTTP client library for inter-service communication

**Serialization:**
- msgspec 0.18.6 - Fast MessagePack serialization for data persistence in Redis (used in all three services)

**Infrastructure:**
- Redis 7.2-bookworm - Database service (three instances: order-db, stock-db, payment-db in docker-compose)

## Configuration

**Environment:**
- Env files for service configuration stored in `/Users/daniel/WebstormProjects/dds26-8/env/` directory:
  - `order_redis.env` - Order service Redis connection
  - `payment_redis.env` - Payment service Redis connection
  - `stock_redis.env` - Stock service Redis connection
- Environment variables required per service:
  - `REDIS_HOST` - Redis instance hostname
  - `REDIS_PORT` - Redis port (default 6379)
  - `REDIS_PASSWORD` - Redis authentication (default "redis")
  - `REDIS_DB` - Redis database index (default 0)
  - `GATEWAY_URL` - Gateway endpoint for inter-service communication (set to `http://gateway:80` in docker-compose)

**Build:**
- Dockerfile per service uses Python 3.12-slim base image
- Build configuration: `FROM python:3.12-slim`
- WORKDIR: `/home/flask-app`

## Platform Requirements

**Development:**
- Docker and docker-compose installed for local development
- Python 3.12+ (for local development without Docker)

**Production:**
- Kubernetes cluster (managed cloud provider or self-hosted)
- Helm 3+ for package management
- Nginx ingress controller (deployed via Helm)
- Redis cluster or managed Redis service
- Kubectl configured to target desired K8s cluster

**Kubernetes Deployment:**
- K8s manifests in `/Users/daniel/WebstormProjects/dds26-8/k8s/` directory:
  - order-app.yaml - Order service deployment and service
  - stock-app.yaml - Stock service deployment and service
  - user-app.yaml - Payment service deployment and service (labeled as user-app)
  - ingress-service.yaml - Nginx ingress configuration
- Helm values for Redis and Nginx in `/Users/daniel/WebstormProjects/dds26-8/helm-config/` directory
- Resource limits per pod: 1 CPU, 1Gi memory (for order service in K8s config)

---

*Stack analysis: 2026-02-27*
