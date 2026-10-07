# Production Evolution

The repository is submission-ready, not production-deployed. Production work should follow measured demand and operational risk.

## Inventory at scale

One row per physical unit is clear and reliable at this scale but can become expensive for large catalogs. Measure row volume, allocation latency, lock waits, and index size first. At higher volume, use product-level conditional counters or bounded unit pools sized from observed demand. Replenish pools asynchronously and retain a safe path to the authoritative inventory count when a pool is exhausted or delayed.

## Audit and security

Merchant actions need an append-only audit trail recording actor, timestamp, request ID, operation, and before/after values. Add authentication, role-based administration, tenant boundaries, rate limits, secret management, encryption policies, and privacy-aware retention. Protect audit records from ordinary application updates.

## Operations and observability

Define service objectives for checkout success, latency, reservation age, and reconciliation time. Add metrics and alerts for inventory contention, pending-order age, payment retries, lease takeovers, coupon conflicts, database saturation, and report latency. Centralize logs and error monitoring, then provide runbooks for stuck payments, inventory discrepancies, and database outages.

## Payments and data safety

Replace the simulator with an idempotent provider adapter supporting signed webhooks, reconciliation, refunds, and dispute handling. Add backup and restore drills, migration rollback plans, zero-downtime deployment checks, and disaster-recovery objectives. Define idempotency retention and archival policies.

## Capacity and resilience

Load-test API and worker concurrency, provider latency, lease expiry, and reporting overlap. Tune indexes, connections, batch sizes, and polling from measurements. Introduce a broker, partitioning, replicas, or regional deployment only when PostgreSQL and the current worker model no longer meet explicit objectives.
