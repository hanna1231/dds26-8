---
phase: 12
slug: 2pc-coordinator-recovery
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio (session-scoped) |
| **Config file** | `pytest.ini` (asyncio_mode=auto, session loop scope) |
| **Quick run command** | `pytest tests/test_2pc_coordinator.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_2pc_coordinator.py -x`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 12-01-01 | 01 | 0 | TPC-04 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_all_prepare_yes_commits -x` | ❌ W0 | ⬜ pending |
| 12-01-02 | 01 | 0 | TPC-04 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_prepare_no_aborts -x` | ❌ W0 | ⬜ pending |
| 12-01-03 | 01 | 0 | TPC-04 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_prepare_exception_aborts -x` | ❌ W0 | ⬜ pending |
| 12-01-04 | 01 | 0 | TPC-04 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_exactly_once -x` | ❌ W0 | ⬜ pending |
| 12-01-05 | 01 | 0 | TPC-05 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_wal_commit_persisted -x` | ❌ W0 | ⬜ pending |
| 12-01-06 | 01 | 0 | TPC-05 | unit | `pytest tests/test_2pc_coordinator.py::test_2pc_wal_abort_persisted -x` | ❌ W0 | ⬜ pending |
| 12-01-07 | 01 | 0 | TPC-06 | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_preparing_aborts -x` | ❌ W0 | ⬜ pending |
| 12-01-08 | 01 | 0 | TPC-06 | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_committing_commits -x` | ❌ W0 | ⬜ pending |
| 12-01-09 | 01 | 0 | TPC-06 | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_aborting_aborts -x` | ❌ W0 | ⬜ pending |
| 12-01-10 | 01 | 0 | TPC-06 | unit | `pytest tests/test_2pc_coordinator.py::test_recovery_skips_saga -x` | ❌ W0 | ⬜ pending |
| 12-01-11 | 01 | 0 | TPC-07 | unit | `pytest tests/test_2pc_coordinator.py::test_pattern_toggle_saga -x` | ❌ W0 | ⬜ pending |
| 12-01-12 | 01 | 0 | TPC-07 | unit | `pytest tests/test_2pc_coordinator.py::test_pattern_toggle_2pc -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_2pc_coordinator.py` — stubs for TPC-04, TPC-05, TPC-06, TPC-07
- [ ] Proto regeneration for stock.proto and payment.proto (add 2PC RPCs)

*Existing infrastructure covers shared fixtures and conftest.*

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
