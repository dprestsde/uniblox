# Decision Planning Context

Detailed rationale, implementation notes, verification cases, and unresolved questions supporting [DECISIONS.md](DECISIONS.md). Keep concise accepted decisions there using the format in `task.md`; maintain full planning context here. These decisions are not yet implemented.

Feature-by-feature failure and concurrency checklist: [EDGE_CASES.md](EDGE_CASES.md). That checklist distinguishes accepted behavior from proposed defaults awaiting confirmation.

## Foundation setup (implemented separately from business decisions)

The repository foundation uses Python 3.13, Django 5.2, Django REST Framework, Psycopg 3, PostgreSQL 18, a uv lockfile, and Docker Compose. `src/store` holds the application boundary and worker entry point; `src/fake_payments` is routed to a second PostgreSQL database. The fake-provider interface defines possible outcomes but has no implementation yet. The worker has no business handlers. Readiness checks both database aliases; liveness does not depend on either. The application runs with UTC timestamps and explicit service-owned transactions. This setup keeps fake-provider effects separate from local order transactions when those workflows are built.

Local container configuration and verified commands are documented in [README.md](README.md). Setup choices do not settle pending cart, price, discount, or reward rules.

## Decision: Minimal Customer model

**Status:** Customer fields accepted with Deepak on 2026-10-07. Not implemented. Count semantics below are the implementation choice for this field.

| Field | Definition |
| --- | --- |
| `id` | Server-generated stable customer identifier; use a UUID. |
| `name` | Required customer display name. |
| `email` | Required email, validated on input; no email verification or authentication flow. |
| `orders_count` | Stored nonnegative integer, initially zero; number of confirmed orders, read-only to clients. |

Associate each cart with a customer; order ownership follows its cart. Do not permit reassignment once checkout starts. Customer identity is association, not proof of authorization. Email uniqueness/normalization policy remains to be specified; identity is the customer ID.

Increment `orders_count` with an atomic database expression only when an order first transitions from pending to confirmed, in the same transaction as order, inventory, and coupon finalization. Lock/check the order before this transition. Pending orders, definitive failures, duplicate callbacks, checkout replays, and repeated recovery do not increment the count. A rolled-back confirmation also rolls back the increment. Independent confirmations for one customer must not lose increments.

Treat confirmed order records as the source for verifying the stored count. Incorporate customer-row updates into the common lock order: finalization must not acquire the customer lock after locks held by a competing path that acquires customer first. Count updates briefly serialize confirmations for the same customer, not all customers.

The proposed cart API refinements (one open cart per customer, automatic creation on first add, optional explicit creation, and mutation replay handling) remain planning context to formalize; adding this model does not silently accept every API proposal. Customer order counts also do not decide whether reward milestones are global or per customer.

## Decision: PostgreSQL inventory units with durable reservations

**Status:** Accepted with Deepak on 2026-10-07.

**Context:** Concurrent checkouts must not oversell, retries must not allocate twice, and failed or abandoned checkouts must not permanently hide available stock. The implementation should remain proportionate to the assignment.

**Choice:** Use Django and PostgreSQL, with one database row per inventory unit and a durable reservation per checkout attempt. Keep inventory and reservations in the same database. Do not introduce bounded token pools, replenishment processes, Redis coordination, or independently maintained stock counters.

### Conceptual data model

- `InventoryUnit`: product reference, state, and reservation reference when allocated.
- `Reservation`: unique checkout-attempt identifier, lifecycle status, and expiry time.
- Products and carts remain separate domain records. Exact field names, constraints, and order relationships will be finalized during implementation planning.

Unit transitions:

```text
AVAILABLE -> RESERVED -> SOLD
                |
                +-----> AVAILABLE
```

A reservation transitions from active to confirmed or released. Preserve the reservation record for replay handling and audit; a repeated request must not create new effects. Retain the confirmed allocation's relationship to its reservation/order.

### Operations and transaction rules

1. **Reserve:** In a short transaction, serialize requests for the same checkout attempt, check for an existing reservation, acquire available units, and assign them to the reservation. Reserve the entire cart or roll back everything. Acquire products and units in consistent order.
2. **Confirm:** Lock the reservation, verify its state, and atomically change its reserved units to sold together with the local order confirmation. Repeated confirmation has no additional effect.
3. **Release:** Lock the reservation and atomically return its reserved units to available. Repeated release has no additional effect. Confirmation and release must not both succeed.
4. **Recover:** Periodically inspect overdue reservations. Release when payment has not started or has definitively failed; reconcile unknown payment outcomes before choosing a transition.

Never hold database locks while waiting for external payment operations. Allocation becomes durable when its database transaction commits.

### Concurrency and availability

- Use `SELECT ... FOR UPDATE SKIP LOCKED` for the initial allocation attempt.
- If it returns too few units, roll back partial allocation and retry through a bounded waiting path using ordinary row locks and fresh validation.
- Lock contention or a timeout means retryable busy, not insufficient inventory. Establish actual shortage before reporting it. The fallback must be tested against concurrent commit, rollback, and release.
- A unique checkout-attempt identifier makes reservation retries idempotent. Reuse with different purchase contents must be rejected rather than silently reinterpreted.
- Availability is the count of `AVAILABLE` unit rows. Index by product and state, and index reservation recovery lookups by status and expiry.
- Displayed availability is a snapshot; only committed reservation guarantees ownership. Adding items to a cart does not reserve stock.

### Invariants and required verification

- A unit belongs to at most one active reservation and is sold at most once.
- A cart allocation succeeds for all requested items or changes nothing.
- Repeated reserve, confirm, or release requests do not duplicate effects.
- Confirmation racing with release produces exactly one terminal outcome.
- Failed transactions leave no partial allocations; released units become available again.
- Locked units are not incorrectly classified as sold out.

Test competing buyers, multi-product rollback, retry races, confirmation versus release, transaction failure, and overdue-reservation recovery against PostgreSQL using separate connections. Include both concentrated demand for one product and demand spread across products when measuring contention.

### Options considered and trade-offs

| Option | Assessment |
| --- | --- |
| One balance row per product | Simple and correct with locking, but reservations for a popular product contend on one row. |
| Conditional counter updates | Compact, but retain the shared-row contention. |
| Bounded allocation-token pool | Reduces materialized rows, but requires refill coordination and additional accounting. Deferred. |
| Redis reservations with PostgreSQL inventory | Adds cross-system consistency and recovery work. Rejected for this scope. |
| One row per inventory unit | Selected: independent unit allocation and direct availability queries, at the cost of storage and per-unit writes. |

**Consequences:** Ten thousand units require ten thousand rows. This is an intentional simplification for the assignment, not a claim of optimal behavior at arbitrary scale. Measure PostgreSQL behavior before adding pooling or other scaling mechanisms.

### Pending decisions

- Payment lifecycle and unknown-payment policy are accepted below; late success after forced release/refunds are deferred.
- Reservation duration, recovery frequency, and lock/retry deadlines; no numeric values are accepted yet.
- Cart edit policy remains pending; the order decision below enforces one order per cart across attempt IDs.
- Exact schema constraints, lock acquisition order, and idempotency fingerprint representation.
- Restock/correction interfaces and payment/refund integration, if included in scope.

These details must preserve the accepted inventory model; they do not require adding token-pool machinery.

### Reference and AI-assisted design correction

[Shopify's inventory reservation engineering article](https://shopify.engineering/scaling-inventory-reservations) informed the use of individually allocatable rows and database-backed reservations. Our design deliberately omits its bounded-pool optimization and is not a reproduction of Shopify's implementation.

During AI-assisted planning, the assistant proposed bounded pools, refill coordination, and additional accounting. Deepak rejected that complexity. The accepted design was simplified to unit rows and durable reservations. Implementation and validation remain outstanding; this records a design correction, not a claim of tested behavior.

## Decision: Recoverable order and payment lifecycle

**Status:** Accepted with Deepak on 2026-10-07. Not implemented.

### States and durable intent

- Order: `PENDING -> CONFIRMED` or `PENDING -> FAILED`.
- Payment outcome: `UNKNOWN -> SUCCEEDED` or `UNKNOWN -> FAILED`. Unknown includes timeouts; record whether initiation has started separately.
- One order per cart, enforced by database uniqueness, and one payment attempt per order initially. Failed orders remain terminal; repeat payment attempts and reopening carts are not part of this initial contract.
- Before contacting the provider, commit the pending order, immutable purchase snapshot, inventory reservation, and payment-attempt record together. A failed initial transaction must not initiate payment.

### Fake provider contract

- `pay(payment_attempt_id, amount)` is idempotent. Reuse with different payment parameters is rejected.
- `get_status(payment_attempt_id)` exposes the durable outcome. Distinguish an unknown/unavailable response from a definitively missing attempt.
- Store simulated provider effects independently of local finalization transactions so order rollback cannot erase successful payment.
- Simulate success, definitive failure, transient unavailability, and a processed payment whose response is lost. Outcomes eventually become discoverable for recovery demonstrations.
- Provider persistence mechanism and failure-injection controls remain implementation details to resolve.

### Finalization and reconciliation

The request handler and worker call one finalization service. Lock the order and related reservation using a consistent protocol; verify state before applying changes.

| Outcome | Atomic local action |
| --- | --- |
| Payment succeeded | Confirm order and mark reserved inventory sold. |
| Payment definitively failed | Fail order and release reserved inventory. |
| Payment unknown | Keep pending and schedule reconciliation. |

Coupon redemption/release joins these transactions as accepted below. Repeated consistent finalization is a no-op; conflicting terminal outcomes must not overwrite existing state silently.

Use payment-attempt records as a durable PostgreSQL work queue with next retry time, attempt count, and worker lease. Claim work in a short transaction, commit, contact the provider, then finalize. Expired leases permit recovery after worker crashes. Use a lease ownership token/version so stale workers cannot overwrite a newer worker's scheduling state. Lease exclusivity is an efficiency measure; provider idempotency and locked local transitions establish correctness.

Use bounded exponential backoff with jitter. Repeated failures flag the order for attention without converting an unknown outcome into failure or deleting recoverable work. Exact intervals, batch size, concurrency, and alert thresholds remain to be measured/configured.

### Expiry and client behavior

- After payment initiation, expiry routes through reconciliation; do not independently release inventory while payment could have succeeded.
- Before initiation, release and initiation must be coordinated through durable state so a worker cannot start paying against a released hold. The exact locking protocol remains to be specified.
- Retain reservations during unresolved provider outages. Automatic bounded release and the associated late-success allocation/refund policy are deferred.
- Checkout retries return the existing order; reject changed purchase intent under the same identity. Return HTTP 202 with the order ID while processing remains pending, and expose an order-status endpoint.

### Required failure demonstrations

| Injected failure | Expected recovery |
| --- | --- |
| Successful payment, failed order confirmation | Order remains pending with reserved inventory; reconciliation confirms once. |
| Failed payment, successful local failure update | Order fails and inventory becomes available. |
| Failed payment, failed local failure update | Order remains pending; reconciliation retries failure and release atomically. |
| Payment response lost after processing | Status lookup discovers the existing outcome without another charge. |
| Crash after intent commit but before provider call | Worker resumes the same attempt using its original provider key. |
| Concurrent retries or expired worker lease | Repeated processing creates neither duplicate payment nor duplicate finalization. |

### Scaling considerations

This design permits multiple application and recovery workers; no global order lock is required. Its capacity is unmeasured. Potential bottlenecks are connection exhaustion, due-work polling and update volume, provider latency/rate limits, repeated processing during outages, and per-unit inventory writes. Keep transactions short, index actionable work, limit worker concurrency, use backoff, and observe pending-order age and recovery throughput. A broker or partitioning is a future response to measured limits, not an initial requirement. Unresolved-payment inventory retention is a business-availability trade-off even at low traffic.

## Decision: Coupon reservation and redemption

**Status:** Accepted with Deepak on 2026-10-07. Not implemented.

### Model and semantics

- One coupon row stores a unique code, discount percentage, state, and owning order. No separate coupon-reservation table initially.
- States: `AVAILABLE -> RESERVED -> REDEEMED`, or `RESERVED -> AVAILABLE` on release.
- Available coupons have no owner; reserved coupons belong to a pending order; redeemed coupons retain the confirmed order that consumed them.
- One bearer coupon per order, no stacking, and no coupon expiry in the initial scope.
- Snapshot the attempted coupon, percentage, and calculated discount on the order before payment. Keep failed-order history even after a coupon is released and used elsewhere. Historical references must not prevent legitimate reuse after failure.
- Freeze payment amount for retries and reconciliation. Money representation and rounding are still separate decisions.

### Reservation and competing checkouts

Within the initial checkout transaction, serialize attempts for the cart and return an existing order on replay. Lock the supplied coupon with `select_for_update()`, validate availability, reserve inventory, and persist coupon ownership, the pending order, and payment attempt together. If any allocation fails, roll back all changes. Payment starts only after commit.

Use ordinary row locking for a specific coupon; `SKIP LOCKED` cannot establish whether that code is valid or available. Locks are held only during database work; durable ownership protects the coupon during external payment. Integrate coupon locks into the common order/reservation/inventory lock ordering during implementation planning.

| Condition | Response |
| --- | --- |
| Available | Reserve for the pending order. |
| Retry by the owning checkout | Return its existing order without new effects. |
| Reserved by another order | HTTP 409, `coupon_reserved`; do not initiate payment or retain partial inventory allocation. |
| Redeemed | HTTP 409, `coupon_redeemed`. |
| Unknown code | `invalid_coupon` error. |
| Lock deadline exceeded | Retryable busy error, not invalid/redeemed. |

Never silently omit a rejected coupon and charge full price. If the first competing transaction rolls back, a waiting checkout can acquire the coupon; if it commits, the other checkout sees its reservation.

### Finalization, failure, and recovery

- Payment success: redeem the coupon, confirm the order, and mark inventory sold in one transaction.
- Definitive payment failure: release the coupon, fail the order, and release inventory in one transaction.
- Unknown payment outcome: retain the coupon reservation and reconcile through the existing payment worker.
- Failed local confirmation or release: roll back all local effects; recovery repeats the appropriate finalization later.
- Verify coupon ownership before every transition. Duplicate operations are harmless; stale recovery for an old order must never release a coupon now held by another order.
- Do not add independent coupon hold expiry. Cancellation before payment follows the coordinated order policy; after initiation, resolve payment before releasing.

### Reporting and verification

Report `generated = available + reserved + redeemed`. Include reserved count alongside the assignment's required counts. Discounts granted derive only from confirmed orders, not pending reservations.

Required tests:

- Two checkouts compete for one coupon: only one reserves it and proceeds to payment.
- Inventory allocation failure leaves the coupon available.
- Payment failure releases inventory and coupon together.
- Successful payment followed by failed confirmation recovers to one redemption.
- Failed payment followed by failed local release recovers both resources together.
- Duplicate or stale recovery cannot mutate another order's coupon.
- Unknown payments retain holds; reporting reconciles across all three states.

### Alternatives and remaining work

Immediate redemption requires reversing apparent usage after failed payments. Redemption only after payment lets competing customers pay discounted totals before exclusivity is established. Reservation is selected because it establishes ownership before payment and fits the accepted recovery lifecycle.

Coupon generation, unique reward milestones, and discount rounding remain to be decided. Exact database checks, ownership relationships, and lock ordering must enforce these semantics without prohibiting historical failed attempts from referencing a reusable coupon.
