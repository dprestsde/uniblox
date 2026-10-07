# Architecture Decisions

Accepted planning decisions; implementation and validation are pending. Detailed notes: [DECISION_CONTEXT.md](DECISION_CONTEXT.md).

## Decision: Inventory units with durable reservations

**Context:** Prevent overselling, duplicate allocations, and stock stranded by failed checkouts.

**Options considered:** Locked product counters, conditional counter updates, bounded unit pools, Redis reservations, and one PostgreSQL row per inventory unit.

**Choice:** Use Django and PostgreSQL with individual units (`AVAILABLE → RESERVED → SOLD`, or release back to `AVAILABLE`) and durable checkout reservations. Reserve the whole cart atomically. Use `SKIP LOCKED` with a bounded waiting fallback; contention is not a stockout. Unique checkout-attempt IDs prevent duplicate allocation. Lock reservation state so confirmation and release are mutually exclusive. Recover overdue holds according to payment outcome.

**Why:** Independent units permit concurrent allocation without a shared reservation counter. Keeping reservations and inventory together provides transactional consistency without pool replenishment or cross-system coordination.

**Consequences:** Storage and writes grow per unit. Availability is counted from available rows. Payment recovery follows the decision below; expiry timings and exact locking details remain pending. Benchmark before adding scaling mechanisms; verify concurrency using separate PostgreSQL connections.

## Decision: Recoverable checkout with simulated payments

**Context:** Payment can succeed or fail while the corresponding order update fails; retries must not charge or finalize twice.

**Options considered:** Treat database commit as payment success, or simulate an independent payment lifecycle with reconciliation.

**Choice:** Atomically persist one pending order per cart, inventory reservation, and payment attempt before contacting an idempotent fake provider. Persist provider outcomes outside the order transaction. Finalize success or failure atomically with inventory changes; leave unknown outcomes pending. Use leased PostgreSQL work records for retry and reconciliation. Return the existing order on retries and HTTP 202 while pending.

**Why:** Durable intent and repeatable finalization recover from crashes and lost responses without a message broker or real payment integration.

**Consequences:** Initially allow one payment attempt per order; failed orders are terminal. Retain inventory while payment is unresolved. Use backoff, worker leases, and monitoring; defer automatic release during prolonged provider outages and late-success refunds. Validate failure injection and concurrent recovery before claiming reliability at scale.

## Decision: Coupon reservation tied to order recovery

**Context:** Concurrent discounted checkouts must not consume the same coupon; failed orders must not lose it.

**Options considered:** Redeem before payment, redeem after payment without a hold, or reserve before payment and finalize with the order.

**Choice:** Lock one coupon row and reserve it atomically with the pending order and inventory. Use `AVAILABLE → RESERVED → REDEEMED`, releasing to available on definitive failure. Redeem/release in the order finalization transaction after verifying ownership. Unknown payment outcomes retain the hold; retries are harmless.

**Why:** Establishes exclusive ownership before charging and reuses existing recovery without a separate coupon worker or reservation table.

**Consequences:** One bearer coupon per order, no stacking or expiry. Freeze discount terms before payment; never silently charge full price. Include reserved coupons in reporting. Generation milestones and rounding remain separate decisions.

## Decision: Minimal customer identity and confirmed-order count

**Context:** Associate carts and purchases with a customer and expose their successful purchase count.

**Options considered:** Anonymous cart IDs, a full account system, or a minimal customer record.

**Choice:** Include `Customer(id, name, email, orders_count)`. Initialize the count to zero and increment it only on the first transition to a confirmed order, atomically with finalization. Clients cannot set the count.

**Why:** Provides customer ownership without authentication scope; transactional counting keeps retries and failures from inflating purchase history.

**Consequences:** Link carts to customers and derive order ownership through the immutable cart relationship. Concurrent confirmations must use atomic increments. Reconcile counts against confirmed orders; reward milestone scope remains a separate decision.

## AI use

Deepak rejected the assistant’s proposed bounded-pool complexity; the accepted design uses individual units and durable reservations. No implementation has been validated yet.
