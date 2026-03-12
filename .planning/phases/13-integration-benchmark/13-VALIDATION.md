---
phase: 13
slug: integration-benchmark
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-12
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (unit/integration), unittest (Docker integration) |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/ -x -v` |
| **Full suite command** | `pytest tests/ -v && cd test && python -m pytest test_microservices.py -v` |
| **Estimated runtime** | ~120 seconds (local), ~300 seconds (full Docker 4-mode) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -v`
- **After every plan wave:** Run full Docker integration across all 4 modes
- **Before `/gsd:verify-work`:** Full suite must be green in all 4 mode combinations
- **Max feedback latency:** 30 seconds (local), 300 seconds (Docker)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 13-01-01 | 01 | 0 | INT-01 | integration | `pytest tests/ -x -v` | ✅ | ⬜ pending |
| 13-01-02 | 01 | 0 | INT-01 | integration | `cd test && python -m pytest test_microservices.py -v` | ✅ | ⬜ pending |
| 13-02-01 | 02 | 1 | INT-01 | e2e (Docker) | `COMM_MODE=queue TRANSACTION_PATTERN=saga docker compose up -d && cd test && python -m pytest test_microservices.py -v` | ✅ | ⬜ pending |
| 13-02-02 | 02 | 1 | INT-02 | e2e (Docker) | `TRANSACTION_PATTERN=2pc python scripts/kill_test.py --all` | ✅ | ⬜ pending |
| 13-02-03 | 02 | 1 | INT-03 | e2e (Docker) | `make benchmark` (x4 modes) | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `stock/app.py` — wire COMM_MODE-conditional queue consumer startup
- [ ] `payment/app.py` — wire COMM_MODE-conditional queue consumer startup
- [ ] `docker-compose.yml` — add COMM_MODE and TRANSACTION_PATTERN env vars
- [ ] Makefile — add multi-mode test/benchmark targets (optional)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Kill-test visual inspection | INT-02 | Kill timing is non-deterministic | Run kill_test.py, verify 0 consistency violations in output |
| Benchmark under load | INT-03 | Requires Docker environment + benchmark tool | Run `make benchmark` in each mode, verify 0 violations |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s (local)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
