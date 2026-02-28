---
phase: 02-grpc-communication
plan: 01
subsystem: api
tags: [grpc, protobuf, grpcio, proto3, codegen, stock, payment, orchestrator]

# Dependency graph
requires:
  - phase: 01-async-foundation
    provides: Async Quart/Uvicorn service structure that gRPC servers will run within
provides:
  - proto3 contract definitions for StockService (ReserveStock, ReleaseStock, CheckStock)
  - proto3 contract definitions for PaymentService (ChargePayment, RefundPayment, CheckPayment)
  - Generated Python stubs (pb2, pyi, grpc) in stock/, payment/, and orchestrator/ directories
  - orchestrator/ Python package marker (__init__.py) for Phase 3
affects:
  - 02-02-stock-grpc-server
  - 02-03-payment-grpc-server
  - 03-saga-orchestrator

# Tech tracking
tech-stack:
  added: [grpcio==1.78.0, protobuf>=6.31.1, grpcio-tools==1.78.0]
  patterns:
    - proto3 single-source-of-truth for inter-service contracts
    - idempotency_key on all mutation RPCs; read-only RPCs omit it
    - generated stubs committed to repo (no runtime codegen needed)

key-files:
  created:
    - protos/stock.proto
    - protos/payment.proto
    - stock/stock_pb2.py
    - stock/stock_pb2.pyi
    - stock/stock_pb2_grpc.py
    - payment/payment_pb2.py
    - payment/payment_pb2.pyi
    - payment/payment_pb2_grpc.py
    - orchestrator/__init__.py
    - orchestrator/stock_pb2.py
    - orchestrator/stock_pb2.pyi
    - orchestrator/stock_pb2_grpc.py
    - orchestrator/payment_pb2.py
    - orchestrator/payment_pb2.pyi
    - orchestrator/payment_pb2_grpc.py
  modified:
    - stock/requirements.txt
    - payment/requirements.txt
    - requirements.txt

key-decisions:
  - "grpcio-tools installed at system level (pip3) rather than per-venv — no virtualenv present in project"
  - "Stubs use absolute imports (import stock_pb2) not relative — services run from their own directory, not as installed packages"
  - "grpcio-tools goes in root requirements.txt (codegen only), not service requirements.txt (runtime only)"

patterns-established:
  - "Proto generation pattern: python3 -m grpc_tools.protoc -I protos --python_out=<svc> --pyi_out=<svc> --grpc_python_out=<svc>"
  - "Each service gets its own copy of relevant stubs; orchestrator gets both"

requirements-completed: [GRPC-01, GRPC-04]

# Metrics
duration: 2min
completed: 2026-02-28
---

# Phase 2 Plan 1: Proto Contracts and gRPC Stub Generation Summary

**proto3 contracts for StockService and PaymentService with idempotency_key on all mutations, Python stubs generated into stock/, payment/, and orchestrator/ via grpcio-tools 1.78.0**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-28T08:51:14Z
- **Completed:** 2026-02-28T08:53:04Z
- **Tasks:** 2
- **Files modified:** 18

## Accomplishments

- Defined StockService proto3 contract (ReserveStock, ReleaseStock, CheckStock) matching existing stock/app.py data model
- Defined PaymentService proto3 contract (ChargePayment, RefundPayment, CheckPayment) matching existing payment/app.py data model
- Generated and verified all Python stubs (pb2.py, pb2.pyi, pb2_grpc.py) in stock/, payment/, and orchestrator/ directories
- All mutation RPCs have idempotency_key field; read-only Check RPCs deliberately omit it per design decision
- Added orchestrator/__init__.py package marker required for Phase 3

## Task Commits

Each task was committed atomically:

1. **Task 1: Create proto files and generate Python stubs** - `93d54a3` (feat)
2. **Task 2: Add grpcio and protobuf to service requirements** - `3eda938` (chore)

## Files Created/Modified

- `protos/stock.proto` - StockService proto3 contract with ReserveStock, ReleaseStock, CheckStock
- `protos/payment.proto` - PaymentService proto3 contract with ChargePayment, RefundPayment, CheckPayment
- `stock/stock_pb2.py` - Generated protobuf message classes for Stock
- `stock/stock_pb2.pyi` - Type stubs for stock messages
- `stock/stock_pb2_grpc.py` - Generated gRPC servicer/stub classes for StockService
- `payment/payment_pb2.py` - Generated protobuf message classes for Payment
- `payment/payment_pb2.pyi` - Type stubs for payment messages
- `payment/payment_pb2_grpc.py` - Generated gRPC servicer/stub classes for PaymentService
- `orchestrator/__init__.py` - Package marker (empty)
- `orchestrator/stock_pb2.py` - Stock stubs copy for orchestrator
- `orchestrator/stock_pb2_grpc.py` - Stock gRPC stub for orchestrator to call StockService
- `orchestrator/payment_pb2.py` - Payment stubs copy for orchestrator
- `orchestrator/payment_pb2_grpc.py` - Payment gRPC stub for orchestrator to call PaymentService
- `stock/requirements.txt` - Added grpcio==1.78.0, protobuf>=6.31.1
- `payment/requirements.txt` - Added grpcio==1.78.0, protobuf>=6.31.1
- `requirements.txt` - Added grpcio-tools==1.78.0 (codegen dev dependency)

## Decisions Made

- grpcio-tools had to be installed via pip3 (system Python 3.x) since pip (default) resolved to a pipx venv without grpc_tools module accessible; using python3 for all generation commands
- Committed generated stubs to repo: stubs are deterministic outputs from checked-in .proto files; avoids requiring grpcio-tools at container build time
- Absolute imports in generated _grpc files (e.g., `import stock_pb2`) are correct for this project — each service runs from its own directory, not as an installed package

## Deviations from Plan

None - plan executed exactly as written. The only deviation was using `python3` instead of `python` CLI (python not available on this macOS system), which is a trivial environment difference, not a code change.

## Issues Encountered

- `pip` (mapped to pipx venv Python 3.13) installed grpcio-tools successfully but `python3 -m grpc_tools.protoc` failed — different Python installations. Resolved by installing with `pip3` which uses the system Python 3 that matches the `python3` binary. All subsequent commands succeeded.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Proto contracts and stubs are ready; Plans 02 and 03 can implement gRPC servers against StockServiceServicer and PaymentServiceServicer
- orchestrator/ package is initialized with both service stubs available for Plan 03 (SAGA orchestrator)
- Generated stubs verified to import cleanly from all three directories

---
*Phase: 02-grpc-communication*
*Completed: 2026-02-28*
