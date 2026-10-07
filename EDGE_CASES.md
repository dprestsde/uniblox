# Edge Cases and Verification Context

Planning checklist for implementing `task.md`. Accepted architecture is summarized in [DECISIONS.md](DECISIONS.md), with details in [DECISION_CONTEXT.md](DECISION_CONTEXT.md). No cases have been implemented or verified yet.

Pricing, rounding, coupon-generation milestones, and report snapshot rules below use proposed defaults that still need acceptance. Additional proposed behaviors are explicitly labeled. Saving this checklist does not mark those choices accepted.

## Customers and order counts

| Case | Expected behavior |
| --- | --- |
| Missing name or invalid email | Reject customer input. |
| Unknown customer when creating/locating a cart | Missing-resource error; do not silently create a customer. |
| Client supplies `orders_count` | Reject as read-only; never trust client purchase counts. |
| New customer | Count starts at zero. |
| Pending or failed order | Count does not increase. |
| First successful order confirmation | Increment once in the finalization transaction. |
| Duplicate confirmation or reconciliation | No additional increment. |
| Local confirmation rolls back after count update | Count rolls back too; recovery increments on successful commit. |
| Concurrent successful orders for one customer | Atomic increments preserve both successes. |
| Count reconciliation | Stored count matches the customer's confirmed orders in a consistent snapshot. |

## Products and carts

| Case | Expected behavior |
| --- | --- |
| Unknown product or cart | Clear missing-resource error; no mutation. |
| Zero, negative, fractional, or malformed quantity | Reject; use the removal operation to remove an item. |
| Same product added again | One cart line; proposed behavior: increase quantity. |
| Quantity exceeds current availability | Proposed behavior: permit cart intent, show availability, enforce stock at checkout. Carts do not reserve inventory. |
| Empty cart checkout | Reject before creating an order or contacting payment. |
| Concurrent cart edits | Serialize through the cart; avoid lost updates. |
| Cart edit races with checkout | Cart locking determines the purchase snapshot. |
| Edit after pending order creation | Proposed policy: reject and preserve the order snapshot. |
| Price changes after adding to cart | Proposed policy: use checkout-time price, then freeze it. |
| Product changes after order creation | Preserve historical name, quantity, prices, and totals. |

## Inventory reservation

| Case | Expected behavior |
| --- | --- |
| Two buyers request the last unit | At most one reserves it. |
| Overlapping quantity requests | Never allocate one unit to multiple reservations. |
| One item in a multi-product cart lacks stock | Roll back all allocation for that checkout. |
| `SKIP LOCKED` returns too few rows | Retry through the bounded waiting path; do not infer stockout from skipped locks. |
| Lock deadline exceeded | Retryable busy response; no partial allocation remains. |
| Crash before transaction commit | No durable allocation changes. |
| Commit succeeds but response is lost | Retry returns the existing reservation/order. |
| Repeated release | Return each reserved unit to availability once. |
| Confirmation races with release | Exactly one terminal transition succeeds. |
| Overdue reservation with unknown payment | Retain and reconcile, rather than independently release. |
| Displayed stock becomes unavailable | Validate at checkout; displayed availability is a snapshot. |

## Checkout and order idempotency

| Case | Expected behavior |
| --- | --- |
| Concurrent identical requests | One order, reservation, and payment attempt. |
| Different request IDs for one cart | Database uniqueness prevents a second order. |
| Request identity reused with different purchase intent/coupon | Conflict, not a new purchase. |
| Retry while processing | Return existing pending order with HTTP 202. |
| Retry after confirmation | Return existing confirmed order. |
| Retry after definitive payment failure | Return terminal failed order; no new charge. |
| Validation fails before initial commit | No durable order/payment attempt or partial holds; cart remains editable. |
| Confirmation response lost | Retry or order-status lookup returns committed result. |

## Fake payment provider

| Case | Expected behavior |
| --- | --- |
| Success | Local finalization confirms order. |
| Definitive failure | Local finalization fails order and releases resources. |
| Repeated payment key | Same outcome, at most one simulated charge. |
| Same key, different amount | Reject mismatch. |
| Payment processed but response lost | Status lookup reveals persisted outcome. |
| Timeout with uncertain outcome | Keep unknown; do not assume failure. |
| Crash before provider call | Recovery resumes the same attempt. |
| Status temporarily unavailable | Retry with backoff. |
| Order transaction rolls back | Independently persisted provider outcome survives. |

Provide deterministic failure injection for tests and demonstrations, not random-only failures.

## Finalization and recovery

| Case | Expected behavior |
| --- | --- |
| Payment succeeds, confirmation transaction fails | Pending order and resource holds remain; recovery confirms later. |
| Payment fails, local failure update succeeds | Fail order and release inventory/coupon. |
| Payment fails, local failure update also fails | Keep pending; recovery retries failure finalization. |
| Failure midway through finalization | Roll back order, inventory, and coupon changes together. |
| Two workers process one attempt | Only one finalization changes state. |
| Worker crashes while holding lease | Another worker resumes after expiry. |
| Old worker resumes after lease reassignment | Ownership token prevents stale scheduling writes. |
| Reconciliation repeats after completion | No duplicate charge, sale, redemption, or release. |
| Cancellation races with payment initiation | Durable state prevents paying against a released reservation. |
| Persistent recovery errors | Preserve recoverable state and flag for attention. |

## Coupon reservation and redemption

| Case | Expected behavior |
| --- | --- |
| Unknown code | Reject before payment. |
| Already redeemed | Distinct conflict. |
| Reserved by another order | `coupon_reserved`; never silently charge full price. |
| Concurrent use of available coupon | Only one checkout reserves it and proceeds to payment. |
| Inventory allocation fails | Coupon reservation rolls back. |
| Unknown payment | Keep coupon reserved. |
| Failed payment | Release coupon atomically with inventory and order failure. |
| Successful payment, failed confirmation | Recovery redeems with order confirmation. |
| Stale release after coupon is assigned elsewhere | Ownership check prevents mutation. |
| Discount terms change later | Preserve frozen order discount and payment amount. |

## Coupon generation — proposed milestone semantics

| Case | Expected behavior |
| --- | --- |
| Fewer than n confirmed orders | No coupon generated. |
| Exactly n confirmed orders | One eligible milestone. |
| Pending/failed orders | Do not advance eligibility. |
| Accumulated milestones | Generate oldest unrewarded milestone; retain other eligibility. |
| Concurrent generation requests | At most one coupon per milestone. |
| Generation transaction fails | Milestone remains eligible. |
| Discounted order confirms | Counts as a successful order. |
| Generation response lost | No duplicate milestone; next request may generate the next eligible milestone. Request-level replay is not proposed initially. |
| Invalid configuration | Reject n < 1 and unsupported discount percentages; proposed range is 1–100. |

## Money and totals — proposed rules

- Use one currency and integer minor units; round discount half-up once at order level.
- Verify fractional-minor-unit discounts round deterministically.
- A 100% discount yields zero, never a negative total.
- Proposed zero-total behavior: confirm without a provider charge; preserve atomic finalization and idempotency.
- Validate large quantities and amounts against supported limits.
- Retries use frozen totals; later product changes do not alter historical orders.
- Gross minus discount equals net.

## Reporting — proposed consistency rules

- Exclude pending and failed orders from purchased quantities and revenue.
- Count retries and recovered confirmations only once.
- Derive historical revenue from order snapshots.
- Reconcile gross minus discounts equals net.
- Reconcile generated coupons equals available plus reserved plus redeemed (accepted coupon accounting).
- Return consistent zero values before any orders exist.
- Use one consistent database snapshot while concurrent finalizations occur.
- Reads never generate coupons, release reservations, or otherwise mutate state.

## Deferred scope and test strategy

Defer real-provider integration, refunds after forced release of uncertain payments, multiple payment attempts on one order, coupon expiry/stacking, multiple currencies, and multi-warehouse allocation. Single-currency and warehouse scope are proposed defaults.

Group related cases into focused tests. Prioritize concurrent allocation, duplicate requests, atomic rollback, and recovery after a persisted payment outcome. Run concurrency tests against PostgreSQL with separate connections and coordinated overlap; assert persisted state as well as responses. Do not claim coverage until tests are implemented and pass.
