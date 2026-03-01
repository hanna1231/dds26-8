# Phase 6: Infrastructure - Context

**Gathered:** 2026-03-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Configure Redis Cluster for high availability per service domain, Kubernetes HPA for auto-scaling domain service replicas, and ensure the entire system runs within the 20 CPU benchmark constraint. Includes updating Docker Compose for local development and Kubernetes manifests for production.

</domain>

<decisions>
## Implementation Decisions

### Redis Cluster topology
- Separate Redis Cluster per domain (Order, Stock, Payment) — full isolation, independent failover
- 3 primary + 3 replica nodes per cluster (18 Redis nodes total)
- Hash tags per entity (e.g., `{order:123}`) to keep all keys for one entity on the same slot — required for multi-key Lua scripts
- AOF persistence with `everysec` fsync policy
- `noeviction` memory policy (as specified in roadmap)

### CPU budget allocation
- Services get CPU priority over Redis nodes (Redis is mostly memory-bound)
- Both resource requests AND limits set on all components — hard limits, no overcommitting
- Goal: fit under 20 CPUs total, no specific utilization target
- If 18 Redis nodes + services don't fit: Claude has discretion to find the best balance (reduce service replicas or Redis CPU allocation)

### Kubernetes scaling policy
- HPA for domain services (Order, Stock, Payment): min 1, max 3 replicas
- Scale-up threshold: CPU > 70%
- Scale-down threshold: CPU < 50% (hysteresis gap to prevent flapping)
- Orchestrator pinned at exactly 1 replica (no HPA) — avoids duplicate SAGA orchestration
- HTTP health endpoints on all services: liveness checks process alive, readiness checks Redis connectivity

### Claude's Discretion
- Exact CPU allocation numbers per component (determined during profiling)
- Tradeoff strategy if CPU budget is tight (reduce replicas vs reduce Redis CPU)
- HPA stabilization window durations
- Health probe intervals and failure thresholds
- Exact Helm chart values for Bitnami Redis Cluster

</decisions>

<specifics>
## Specific Ideas

- Docker Compose Redis topology should be configurable via environment variable — default to simplified (3-node single cluster), with option for full 18-node topology or standalone per domain
- Orchestrator service included in Docker Compose for full local SAGA testing
- Use Bitnami Redis Cluster images in Docker Compose to match production Helm chart configuration
- Makefile targets for developer workflow: `dev-up` (simplified), `dev-cluster` (full topology), `dev-down`

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 06-infrastructure*
*Context gathered: 2026-03-01*
