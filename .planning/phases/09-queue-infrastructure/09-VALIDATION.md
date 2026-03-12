---
phase: 9
slug: queue-infrastructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (already configured) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_queue_infrastructure.py -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_queue_infrastructure.py -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | MQC-01 | unit | `pytest tests/test_queue_infrastructure.py::test_xadd_command -x` | ❌ W0 | ⬜ pending |
| 09-01-02 | 01 | 1 | MQC-01 | integration | `pytest tests/test_queue_infrastructure.py::test_command_stream_consumer_group -x` | ❌ W0 | ⬜ pending |
| 09-01-03 | 01 | 1 | MQC-02 | integration | `pytest tests/test_queue_infrastructure.py::test_reply_correlation -x` | ❌ W0 | ⬜ pending |
| 09-01-04 | 01 | 1 | MQC-02 | unit | `pytest tests/test_queue_infrastructure.py::test_reply_timeout -x` | ❌ W0 | ⬜ pending |
| 09-02-01 | 02 | 1 | MQC-03 | integration | `pytest tests/test_queue_infrastructure.py::test_stock_consumer_dispatch -x` | ❌ W0 | ⬜ pending |
| 09-02-02 | 02 | 1 | MQC-03 | integration | `pytest tests/test_queue_infrastructure.py::test_payment_consumer_dispatch -x` | ❌ W0 | ⬜ pending |
| 09-02-03 | 02 | 2 | MQC-03 | integration | `pytest tests/test_queue_infrastructure.py::test_saga_checkout_over_queue -x` | ❌ W0 | ⬜ pending |
| 09-02-04 | 02 | 1 | MQC-03 | unit | `pytest tests/test_queue_infrastructure.py::test_consumer_ack -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_queue_infrastructure.py` — stubs for MQC-01, MQC-02, MQC-03
- [ ] Test fixtures for queue Redis connection, stream setup/teardown
- [ ] No new framework install needed — pytest-asyncio already configured

*Existing infrastructure covers framework requirements.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
