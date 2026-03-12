---
phase: 11
slug: 2pc-state-machine-participants
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio (session-scoped) |
| **Config file** | `pytest.ini` (asyncio_mode=auto, session loop scope) |
| **Quick run command** | `pytest tests/test_tpc.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_tpc.py -x`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 1 | TPC-01 | unit | `pytest tests/test_tpc.py::test_tpc_valid_transitions -x` | ❌ W0 | ⬜ pending |
| 11-01-02 | 01 | 1 | TPC-01 | unit | `pytest tests/test_tpc.py::test_tpc_invalid_transitions_rejected -x` | ❌ W0 | ⬜ pending |
| 11-01-03 | 01 | 1 | TPC-01 | unit | `pytest tests/test_tpc.py::test_tpc_cas_rejects_stale_state -x` | ❌ W0 | ⬜ pending |
| 11-01-04 | 01 | 1 | TPC-01 | unit | `pytest tests/test_tpc.py::test_tpc_duplicate_creation_prevented -x` | ❌ W0 | ⬜ pending |
| 11-02-01 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_prepare_reserves -x` | ❌ W0 | ⬜ pending |
| 11-02-02 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_prepare_idempotent -x` | ❌ W0 | ⬜ pending |
| 11-02-03 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_commit_finalizes -x` | ❌ W0 | ⬜ pending |
| 11-02-04 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_abort_releases -x` | ❌ W0 | ⬜ pending |
| 11-02-05 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_prepare_insufficient -x` | ❌ W0 | ⬜ pending |
| 11-02-06 | 02 | 1 | TPC-02 | unit | `pytest tests/test_tpc.py::test_stock_prepare_atomic -x` | ❌ W0 | ⬜ pending |
| 11-03-01 | 03 | 1 | TPC-03 | unit | `pytest tests/test_tpc.py::test_payment_prepare_reserves -x` | ❌ W0 | ⬜ pending |
| 11-03-02 | 03 | 1 | TPC-03 | unit | `pytest tests/test_tpc.py::test_payment_prepare_idempotent -x` | ❌ W0 | ⬜ pending |
| 11-03-03 | 03 | 1 | TPC-03 | unit | `pytest tests/test_tpc.py::test_payment_commit_finalizes -x` | ❌ W0 | ⬜ pending |
| 11-03-04 | 03 | 1 | TPC-03 | unit | `pytest tests/test_tpc.py::test_payment_abort_releases -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_tpc.py` — stubs for TPC-01, TPC-02, TPC-03
- [ ] `tests/conftest.py` — add tpc_db fixture (reuse orchestrator_db or add separate)

*Existing infrastructure covers framework setup (pytest.ini, conftest.py already exist).*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
