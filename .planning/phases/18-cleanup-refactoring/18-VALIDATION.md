---
phase: 18
slug: cleanup-refactoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 18 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | tests/ directory (existing) |
| **Quick run command** | `python3 -m pytest tests/test_workflow_engine.py tests/test_checkout_workflow.py tests/test_strategies.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command
- **After every plan wave:** Run full suite
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 18-01-01 | 01 | 1 | REF-01 | unit | `python3 -m pytest tests/ -x -q --ignore=tests/test_saga.py --ignore=tests/test_tpc.py` | ✅ | ⬜ pending |
| 18-01-02 | 01 | 1 | REF-02 | unit | `python3 -m pytest tests/test_workflow_engine.py tests/test_strategies.py -x -q` | ✅ | ⬜ pending |
| 18-01-03 | 01 | 1 | REF-03+REF-04 | unit | `python3 -m pytest tests/ -x -q` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*Existing infrastructure covers all phase requirements. All test files already exist.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Benchmark 0 consistency violations | REF-04 | Requires Docker + benchmark runner | Run benchmark, verify 0 violations |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
