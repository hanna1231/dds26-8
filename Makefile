.PHONY: dev-up dev-cluster dev-down dev-logs dev-build dev-clean test dev-status

# Start simplified topology (single shared 6-node Redis cluster) — fast local dev
dev-up: dev-build
	ORDER_REDIS_HOST=shared-redis-0 \
	STOCK_REDIS_HOST=shared-redis-0 \
	PAYMENT_REDIS_HOST=shared-redis-0 \
	ORCH_REDIS_HOST=shared-redis-0 \
	docker compose --profile simple up -d
	@echo "Waiting for shared Redis cluster to initialize..."
	@sleep 15
	@echo "Simple mode: single shared 6-node Redis cluster"
	@echo "API available at http://localhost:8000"

# Start full 3-cluster topology (18 Redis nodes + 5 app services) — mirrors production
dev-cluster: dev-build
	docker compose --profile full up -d
	@echo "Waiting for Redis clusters to initialize..."
	@sleep 15
	@echo "Full mode: 3 independent Redis clusters (18 nodes)"
	@echo "API available at http://localhost:8000"

# Build all service images
dev-build:
	docker compose build

# Stop all containers (volumes preserved — data persists across restarts)
dev-down:
	docker compose down

# Follow logs for all services (Ctrl+C to stop)
dev-logs:
	docker compose logs -f order-service stock-service payment-service orchestrator-service gateway

# Stop and remove all containers, networks, and volumes (CLEAN SLATE)
dev-clean:
	docker compose down -v --remove-orphans

# Run tests (standalone Redis, not cluster)
test:
	pytest tests/ -x -v

# Show status of all containers
dev-status:
	docker compose ps
