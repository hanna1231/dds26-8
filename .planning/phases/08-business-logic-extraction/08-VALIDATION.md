---
phase: 8
slug: business-logic-extraction
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (existing) |
| **Config file** | `tests/conftest.py` (session-scoped fixtures) |
| **Quick run command** | `pytest tests/test_grpc_integration.py -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_grpc_integration.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | BLE-01 | integration | `pytest tests/test_grpc_integration.py -x -q` | Yes | ⬜ pending |
| 08-01-02 | 01 | 1 | BLE-01 | structural | `grep -c "db\.\|eval\|redis" stock/grpc_server.py` | N/A | ⬜ pending |
| 08-02-01 | 02 | 1 | BLE-02 | integration | `pytest tests/test_grpc_integration.py -x -q` | Yes | ⬜ pending |
| 08-02-02 | 02 | 1 | BLE-02 | structural | `grep -c "db\.\|eval\|redis" payment/grpc_server.py` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| No Lua/Redis calls in stock servicer | BLE-01 | Structural code inspection | `grep -cE "db\.\|eval\|redis" stock/grpc_server.py` should return 0 |
| No Lua/Redis calls in payment servicer | BLE-02 | Structural code inspection | `grep -cE "db\.\|eval\|redis" payment/grpc_server.py` should return 0 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
