---
phase: 15
slug: execution-strategies
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | pytest.ini (project root) |
| **Quick run command** | `python3 -m pytest tests/test_strategies.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_strategies.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 15-01-01 | 01 | 1 | STR-01 | unit | `python3 -m pytest tests/test_strategies.py -k "saga_execute" -x -q` | ❌ W0 | ⬜ pending |
| 15-01-02 | 01 | 1 | STR-02 | unit | `python3 -m pytest tests/test_strategies.py -k "saga_compensate" -x -q` | ❌ W0 | ⬜ pending |
| 15-01-03 | 01 | 1 | STR-03 | unit | `python3 -m pytest tests/test_strategies.py -k "tpc_execute" -x -q` | ❌ W0 | ⬜ pending |
| 15-01-04 | 01 | 1 | STR-04 | unit | `python3 -m pytest tests/test_strategies.py -k "both_strategies" -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_strategies.py` — stubs for STR-01, STR-02, STR-03, STR-04
- [ ] Reuse existing `conftest.py` fixtures (`orchestrator_db`, `clean_orchestrator_db`)

*Existing pytest infrastructure covers framework needs. Only test file stubs needed.*

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
