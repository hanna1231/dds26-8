---
phase: 10
slug: transport-adapter
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_transport_adapter.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_transport_adapter.py -x`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 10-01-01 | 01 | 1 | MQC-04 | unit | `pytest tests/test_transport_adapter.py::test_grpc_mode_exports -x` | ❌ W0 | ⬜ pending |
| 10-01-02 | 01 | 1 | MQC-04 | unit | `pytest tests/test_transport_adapter.py::test_signature_parity -x` | ❌ W0 | ⬜ pending |
| 10-01-03 | 01 | 1 | MQC-05 | integration | `pytest tests/test_transport_adapter.py::test_checkout_grpc_mode -x` | ❌ W0 | ⬜ pending |
| 10-01-04 | 01 | 1 | MQC-05 | integration | `pytest tests/test_transport_adapter.py::test_checkout_queue_mode -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_transport_adapter.py` — stubs for MQC-04, MQC-05
- [ ] No new fixtures needed beyond existing conftest.py + test_queue_infrastructure.py patterns

*Existing infrastructure covers fixture requirements.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
