# Requirements: DDS26-8 Distributed Checkout System

**Defined:** 2026-03-12
**Core Value:** Checkout transactions must never lose money or item counts -- consistency is non-negotiable, even when containers crash mid-transaction.

## v2.0 Requirements

Requirements for v2.0 milestone. Each maps to roadmap phases.

### Business Logic Extraction

- [ ] **BLE-01**: Stock service business logic extracted from gRPC servicers into shared operations module
- [x] **BLE-02**: Payment service business logic extracted from gRPC servicers into shared operations module

### Two-Phase Commit

- [ ] **TPC-01**: 2PC state machine with states INIT→PREPARING→COMMITTING/ABORTING→COMMITTED/ABORTED using Lua CAS transitions
- [ ] **TPC-02**: Stock service tentative reservation Lua scripts (prepare reserves, commit finalizes, abort releases)
- [ ] **TPC-03**: Payment service tentative reservation Lua scripts (prepare reserves, commit finalizes, abort releases)
- [ ] **TPC-04**: Orchestrator acts as 2PC coordinator with concurrent participant prepare via asyncio.gather
- [ ] **TPC-05**: Coordinator persists decision to Redis before sending phase-2 messages (WAL pattern)
- [ ] **TPC-06**: Recovery scanner handles 2PC transactions using protocol field in records
- [ ] **TPC-07**: TRANSACTION_PATTERN env var toggles between SAGA and 2PC

### Message Queue Communication

- [ ] **MQC-01**: Redis Streams command streams per service with consumer group processing
- [ ] **MQC-02**: Shared reply stream with correlation ID routing and asyncio.Future resolution
- [ ] **MQC-03**: Queue consumer workers in Stock and Payment services dispatching to operations modules
- [ ] **MQC-04**: Transport adapter abstraction enabling gRPC/queue swap transparently
- [ ] **MQC-05**: COMM_MODE env var toggles between gRPC and queue communication

### Integration & Testing

- [ ] **INT-01**: All 4 mode combinations (SAGA/2PC x gRPC/queue) pass integration tests
- [ ] **INT-02**: Kill-test consistency for 2PC mode (no lost money/items after recovery)
- [ ] **INT-03**: Benchmark passes with 0 consistency violations in all modes

## Future Requirements

Deferred to future release. Tracked but not in current roadmap.

### Operational

- **OPS-01**: Dead letter queue for failed queue requests
- **OPS-02**: Graceful degradation from queue to gRPC on failure
- **OPS-03**: Runtime hot-swap of communication mode without restart

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Kafka/RabbitMQ/NATS | Redis Streams sufficient, no new infra needed |
| Distributed locks (Redlock) | Lua CAS sufficient for atomicity |
| New proto definitions for queue messages | msgspec JSON serialization over streams is simpler |
| 2PC for Order service | Order has no resources to prepare/commit; only Stock and Payment participate |
| Queue health monitoring dashboard | Not required for course submission |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| BLE-01 | Phase 8 | Pending |
| BLE-02 | Phase 8 | Complete |
| TPC-01 | Phase 11 | Pending |
| TPC-02 | Phase 11 | Pending |
| TPC-03 | Phase 11 | Pending |
| TPC-04 | Phase 12 | Pending |
| TPC-05 | Phase 12 | Pending |
| TPC-06 | Phase 12 | Pending |
| TPC-07 | Phase 12 | Pending |
| MQC-01 | Phase 9 | Pending |
| MQC-02 | Phase 9 | Pending |
| MQC-03 | Phase 9 | Pending |
| MQC-04 | Phase 10 | Pending |
| MQC-05 | Phase 10 | Pending |
| INT-01 | Phase 13 | Pending |
| INT-02 | Phase 13 | Pending |
| INT-03 | Phase 13 | Pending |

**Coverage:**
- v2.0 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0

---
*Requirements defined: 2026-03-12*
*Last updated: 2026-03-12 after roadmap creation*
