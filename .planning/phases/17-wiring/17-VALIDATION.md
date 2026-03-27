---
phase: 17
slug: wiring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | tests/ directory (existing) |
| **Quick run command** | `python3 -m pytest tests/ -x -q --tb=short` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ -x -q --tb=short`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 17-01-01 | 01 | 1 | CHK-02 | integration | `python3 -m pytest tests/test_grpc_integration.py -x -q` | ✅ | ⬜ pending |
| 17-01-02 | 01 | 1 | CHK-03 | integration | `python3 -m pytest tests/test_fault_tolerance.py -x -q` | ✅ | ⬜ pending |
| 17-01-03 | 01 | 1 | CHK-02+CHK-03 | full | `python3 -m pytest tests/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements. All test files already exist.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Kill-test 0 consistency violations | CHK-02+CHK-03 | Requires Docker containers and benchmark runner | Run `docker compose up`, execute kill-test script, verify 0 violations |
| COMM_MODE=queue integration | CHK-02 | Requires Redis Streams infrastructure | Set COMM_MODE=queue, run integration tests |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
