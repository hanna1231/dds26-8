# Phase 7: Validation and Delivery - Context

**Gathered:** 2026-03-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate the distributed checkout system against the wdm-project-benchmark, verify consistency under container-kill scenarios, and produce an architecture design document for team presentation prep. The contributions.txt is written by the team manually — not part of automated work.

</domain>

<decisions>
## Implementation Decisions

### Architecture document structure
- Organized by system layer: Communication (gRPC) → Orchestration (SAGA) → Events (Streams) → Resilience (fault tolerance) → Infrastructure (Redis Cluster, K8s)
- Audience: team members preparing for project presentation — each section must explain what was chosen, alternatives considered, and why the decision was made
- Decision-focused depth: concise (1-2 pages per topic), covering rationale and tradeoffs rather than implementation details
- Mermaid diagrams for visual architecture representation (sequence diagrams, flowcharts, topology)

### Kill-test scenarios
- Kill each service individually (Order, Stock, Payment, Orchestrator) during active load
- Eventually consistent expectation: after recovery + 30-second fixed timeout, balances must converge to correct values
- Automated shell/Python scripts that kill containers, wait for recovery, then assert consistency
- Scripts should be repeatable and CI-friendly

### Benchmark strategy
- Benchmark source: https://github.com/delftdata/wdm-project-benchmark (needs to be cloned)
- Run the wdm-project-benchmark unmodified against the system
- Fix any failures discovered (correctness vs performance triage at Claude's discretion)
- Integration test and benchmark sequencing at Claude's discretion

### Contributions file
- Written manually by the team — excluded from automated phase work
- Only create an empty placeholder or skip entirely

### Claude's Discretion
- Triage order for benchmark failures (correctness vs performance)
- Whether integration tests gate benchmark runs or run independently
- Specific kill-test timing (which SAGA states to target)
- Exact Mermaid diagram types per architecture section

</decisions>

<specifics>
## Specific Ideas

- Architecture doc is for team presentation prep — team members need to understand and explain every architectural decision
- "Why it was chosen, which alternatives were considered, and why it has been done" — this framing should guide each section
- Kill-test scripts should be automated and repeatable, not manual checklists

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 07-validation-and-delivery*
*Context gathered: 2026-03-01*
