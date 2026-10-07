# Architecture Decisions

Detailed rationale and earlier research live in [DECISION_CONTEXT.md](DECISION_CONTEXT.md). The implementation is constrained to the assignment’s 4–6-hour timebox.

## Invariants and selected semantics

- A customer has at most one open cart; checkout freezes it and allows a new cart.
- One cart creates at most one order, reservation, and payment attempt. Committed idempotency keys replay their original resource.
- An inventory unit is `AVAILABLE`, `RESERVED` by one reservation, or `SOLD` by that reservation. A checkout allocates its entire cart or nothing.
- A coupon is `AVAILABLE`, `RESERVED` by one order, or `REDEEMED` by that order. Definitive failure releases it; an unknown payment retains it.
- Only the first successful finalization increments `Customer.orders_count`. Stored counts must equal confirmed orders.
- Orders preserve product, price, coupon, gross, discount, and net snapshots; `gross - discount = net`.
- Reward milestones count confirmed store-wide orders. Each program/milestone generates at most one coupon.

Cart additions increase the existing line and reject a resulting quantity above current availability. Checkout rechecks availability. Current prices are frozen when the pending order is created. Failed orders are terminal. Authentication is omitted as permitted by `task.md`.

## Decision: Individual inventory units and durable reservations

**Context:** Concurrent carts must not oversell stock or report a false shortage merely because another allocator holds locks.

**Options considered:** A locked product counter, conditional counter updates, Redis holds, bounded unit pools, and one PostgreSQL row per unit.

**Choice:** Allocate ordered unit rows using `SELECT FOR UPDATE SKIP LOCKED` inside the pending-order transaction. If skipped locks explain a short allocation, retry with bounded waiting: two-second lock timeout and five-second overall budget. True shortage returns requested and available quantities; exhausted contention returns retryable 503.

**Why:** Unit ownership makes duplicate allocation impossible and keeps reservation state in the same transaction as the order and coupon. The waiting path distinguishes contention from stockout.

**Consequences:** Storage and writes scale per unit, and availability uses counts. This is clear for assignment volume; measured pressure could justify product-level conditional counters or partitioned unit pools later.

## Decision: Durable asynchronous checkout and independent fake payments

**Context:** Payment may persist while local confirmation fails, or its response may be lost.

**Options considered:** Treat checkout commit as payment success, call payment inside the order transaction, or persist intent and reconcile an idempotent provider.

**Choice:** Checkout atomically creates a pending order, immutable items, inventory/coupon holds, payment attempt, frozen cart, and idempotency record. The provider uses a separate database and a unique provider key. A leased worker contacts or queries it, stores known outcomes, then calls one idempotent local finalizer. Unknown outcomes remain pending.

**Why:** Provider effects survive application rollback, while repeated keys prevent duplicate simulated charges. Recovery covers crashes, lost responses, failed success-finalization, and failed failure-finalization.

**Consequences:** Clients receive 202 and poll orders. Before initiation, a five-minute expiry may fail and release the order. After initiation, holds remain until reconciliation. Refunds for an eventual late success are deferred.

## Decision: PostgreSQL locks, leases, and explicit transaction boundaries

**Context:** Multiple API and worker instances must coordinate without an in-process mutex or broker.

**Options considered:** Global serialization, a message broker, advisory locks, or row locks plus a database work queue.

**Choice:** Use short explicit transactions and acquire business rows in the common order Customer, Cart, Order, PaymentAttempt, Reservation, Coupon, then InventoryUnit by product/unit ID. Queue claims lock only due attempt rows and commit before provider I/O. Thirty-second leases carry ownership tokens; stale workers cannot schedule or persist local attempt state. Retries use capped exponential backoff with deterministic jitter.

**Why:** PostgreSQL already provides durable coordination across processes. Token fencing preserves correctness after lease expiry; provider idempotency and terminal state checks remain the ultimate safeguards.

**Consequences:** Polling and connection use are the initial scaling limits. Batch size is ten, polling defaults to five seconds, and attempts are flagged after five failures or 15 pending minutes. Throughput is unmeasured; a broker, partitioning, and provider rate controls follow evidence.

## Decision: Coupon ownership follows order recovery

**Context:** A coupon cannot be used twice or disappear after a failed purchase.

**Options considered:** Redeem before payment, redeem after payment without a hold, or reserve with the pending order.

**Choice:** Lock and reserve an available coupon in the checkout transaction. Success redeems it; definitive failure verifies ownership then releases it. Failed-order history stays in the order snapshot after current ownership clears.

**Why:** Exclusivity is established before payment, while all local success or failure effects commit together.

**Consequences:** One coupon per order, without stacking or expiry. Unknown outcomes reduce coupon availability until reconciliation.

## Decision: Exact money and immutable purchase explanations

**Context:** Retried payments, price changes, and percentage rounding must produce the same charge and report.

**Options considered:** Decimal database amounts, floating point, line-level rounding, or integer minor units with order-level rounding.

**Choice:** Use USD integer cents. At checkout, `gross = sum(price × quantity)`, `discount = (gross × percent + 50) // 100`, and `net = gross - discount`. Round half-up once on the subtotal and reject totals above the supported integer bound. A 100% coupon follows normal finalization without a provider call.

**Why:** Integer arithmetic is deterministic and snapshots explain historical totals even after product changes.

**Consequences:** Multiple currencies, tax, shipping, and alternate rounding regimes require an explicit future money model.

## Decision: Keyed idempotency with committed result references

**Context:** Lost HTTP responses cause clients to retry additions, checkout, and coupon generation.

**Options considered:** Rely only on resource uniqueness, cache full responses, or persist operation/scope/key plus a canonical request fingerprint and result reference.

**Choice:** Require `Idempotency-Key` for these three operations. Check committed records before current stock or coupon validation. Equal input returns the original resource; changed input returns 409. Records commit with the mutation, so rolled-back attempts do not consume keys. Checkout replay returns 202 while pending and 200 once terminal.

**Why:** The result reference stays current for orders while preserving exact mutation identity. Add-item replay still points to the original cart after rollover.

**Consequences:** Records are retained indefinitely for this assignment. Production needs retention, tenant scoping, and request-size limits.

## Decision: Store-wide rewards and snapshot-consistent reporting

**Context:** Coupon generation must not skip or duplicate milestones, and multi-query reports must reconcile during concurrent finalization.

**Options considered:** Generate coupons during checkout, derive them on read, or use an administrator action protected by the reward-program row.

**Choice:** Seed immutable defaults `n=5`, `x=10`. A keyed administrator action locks the program, counts confirmed orders, and creates the oldest missing eligible milestone under a unique program/milestone constraint. Reports run their aggregates in a read-only Repeatable Read transaction and use order snapshots.

**Why:** Generation remains explicit as required, missed milestones remain recoverable, and all report values share one database snapshot.

**Consequences:** Configuration changes and multiple reward programs are deferred. Reports verify revenue and coupon accounting but do not repair inconsistencies.

## Errors, implementation scope, and validation

Errors use `{error: {code, message, retryable, details?}}`: 400 invalid input, 404 missing resource, 409 stock/idempotency/state conflicts, and 503 transient contention. Structured logs include request IDs and omit request bodies, emails, and coupon values.

Implemented: customer/cart/product reads, inventory reservation, checkout snapshots, independent fake payment, leased recovery, coupon reservation and generation, customer counts, reporting, health checks, stable seed data, and deterministic demos. PostgreSQL tests cover retries, last-unit competition, simultaneous first cart creation, rollbacks, provider uncertainty, expiry, money, rewards, and reporting.

Deferred: authentication, product administration, real payments, refunds, cancellation after initiation, multiple payment attempts, taxes/shipping, multiple currencies or warehouses, coupon stacking/expiry, archival, metrics dashboards, and load-tested tuning.

## AI use, time spent, and next work

AI helped enumerate failure modes and draft transaction boundaries. I rejected an earlier AI proposal for bounded inventory pools and refill coordination because unit rows meet this assignment with less operational state. I also corrected generated reporting tests to use `TransactionTestCase`; Django `TestCase` had already opened an outer transaction and could not establish Repeatable Read at the required boundary. All accepted code was exercised in PostgreSQL.

Approximate time spent: 5½ hours across foundation, implementation, recovery demonstrations, tests, and documentation.

With two more hours, I would first add coordinated tests for a worker losing its lease during provider latency, coupon competition, and a concurrent report/finalization snapshot. I would then measure lock-wait and worker-claim behavior with multiple processes, add metrics for pending age and retries, and tighten email validation and API schema generation.
