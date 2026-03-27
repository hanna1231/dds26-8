---
phase: 16
slug: workflowengine-checkout-definition
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | tests/ directory (existing) |
| **Quick run command** | `python -m pytest tests/test_workflow_engine.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_workflow_engine.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 16-01-01 | 01 | 1 | ENG-03 | unit | `python -m pytest tests/test_workflow_engine.py -x -q` | ❌ W0 | ⬜ pending |
| 16-01-02 | 01 | 1 | CHK-01 | unit | `python -m pytest tests/test_workflow_engine.py -x -q` | ❌ W0 | ⬜ pending |
| 16-01-03 | 01 | 1 | ENG-03+CHK-01 | integration | `python -m pytest tests/test_workflow_engine.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_workflow_engine.py` — stubs for ENG-03, CHK-01
- [ ] Test fixtures for WorkflowStore mock and strategy mocks

*Existing infrastructure covers pytest framework and Redis test fixtures.*

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
