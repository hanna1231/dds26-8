.PHONY: dev-up dev-cluster dev-down dev-logs dev-build dev-clean test dev-status benchmark stress-init stress-test kill-test kill-test-all

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
	docker compose --profile simple --profile full down

# Follow logs for all services (Ctrl+C to stop)
dev-logs:
	docker compose logs -f order-service stock-service payment-service orchestrator-service gateway

# Stop and remove all containers, networks, and volumes (CLEAN SLATE)
dev-clean:
	docker compose --profile simple --profile full down -v --remove-orphans

# Run tests (standalone Redis, not cluster)
test:
	pytest tests/ -x -v

# Show status of all containers
dev-status:
	docker compose ps

# Clone and run the wdm-project-benchmark consistency test against live cluster
benchmark:
	@if [ ! -d "wdm-project-benchmark" ]; then \
		echo "Cloning wdm-project-benchmark..."; \
		git clone https://github.com/delftdata/wdm-project-benchmark; \
	fi
	@echo '{"ORDER_URL": "http://localhost:8000", "PAYMENT_URL": "http://localhost:8000", "STOCK_URL": "http://localhost:8000"}' > wdm-project-benchmark/urls.json
	@pip install -r wdm-project-benchmark/requirements.txt -q 2>/dev/null || true
	@echo "Running consistency test against http://localhost:8000..."
	cd wdm-project-benchmark/consistency-test && python3 run_consistency_test.py

# Initialize databases for stress test (100k items, users, orders)
# Requires: cluster running (make dev-up)
stress-init:
	@if [ ! -d "wdm-project-benchmark" ]; then \
		echo "Cloning wdm-project-benchmark..."; \
		git clone https://github.com/delftdata/wdm-project-benchmark; \
	fi
	@echo '{"ORDER_URL": "http://localhost:8000", "PAYMENT_URL": "http://localhost:8000", "STOCK_URL": "http://localhost:8000"}' > wdm-project-benchmark/urls.json
	@pip install -r wdm-project-benchmark/requirements.txt -q 2>/dev/null || true
	@echo "Populating databases (100k items, 100k users, 100k orders)..."
	cd wdm-project-benchmark/stress-test && python3 init_orders.py
	@echo "Done. Run 'make stress-test' to start Locust."

# Run Locust stress test — opens UI at http://localhost:8089
# Requires: make stress-init (run once to populate databases)
stress-test:
	@pip install -r wdm-project-benchmark/requirements.txt -q 2>/dev/null || true
	@echo "Starting Locust stress test..."
	@echo "Open http://localhost:8089 to control the test"
	cd wdm-project-benchmark/stress-test && locust -f locustfile.py --host="http://localhost:8000"

# Run kill-container consistency test for a single service
# Usage: make kill-test SERVICE=stock-service
kill-test:
	SAGA_STALENESS_SECONDS=10 \
	ORDER_REDIS_HOST=shared-redis-0 \
	STOCK_REDIS_HOST=shared-redis-0 \
	PAYMENT_REDIS_HOST=shared-redis-0 \
	ORCH_REDIS_HOST=shared-redis-0 \
	docker compose --profile simple up -d
	@sleep 20
	python scripts/kill_test.py --service $(SERVICE)

# Run kill-container tests for all services sequentially (manages cluster lifecycle internally)
kill-test-all:
	python scripts/kill_test.py --all
