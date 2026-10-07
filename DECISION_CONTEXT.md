# Decision Context

This companion to [DECISIONS.md](DECISIONS.md) retains implementation detail and operational reasoning without expanding the required decision record.

## Architecture

The service uses Python 3.13, Django 5.2 LTS, Django REST Framework, Psycopg 3, PostgreSQL 18, uv, Ruff, and Docker Compose. `src/store` contains application state and transaction services. `src/fake_payments` is routed exclusively to a second PostgreSQL database. There are no cross-database relationships: provider attempt IDs are scalar UUID values.

HTTP requests do not receive automatic transactions. Business services choose short transaction boundaries. Provider I/O always occurs after the local claim transaction commits. UTC timestamps, request IDs, structured stdout logs, health checks, and isolated test databases were established in the foundation.

## Inventory selection

The accepted design stores one row per physical inventory unit:

```text
AVAILABLE → RESERVED → SOLD
                 └──→ AVAILABLE on definitive failure
```

Alternatives included a single locked product counter, conditional counter decrements, Redis holds, and Shopify-style bounded pools. Unit rows were chosen because reservation ownership is explicit and all-cart rollback is handled by one PostgreSQL transaction. Deepak rejected bounded pools and refill coordination as premature complexity for the assignment.

Checkout orders products and units deterministically, initially selects available units with `SKIP LOCKED`, and never interprets skipped locked rows as a confirmed shortage. When a normal visibility count shows enough stock, it retries with ordinary row waiting under a two-second lock timeout and five-second overall budget. A true shortage reports product ID, requested quantity, and current availability. A busy database reports retryable 503.

The cost is per-unit storage and writes. At materially larger catalog quantities, measure allocation latency and index size before considering product-level conditional counters, unit-range compression, or bounded pools. [Shopify’s reservation article](https://shopify.engineering/scaling-inventory-reservations) informed the row-allocation approach; this implementation is deliberately smaller and is not a reproduction.

## Cart and snapshot semantics

A partial unique constraint permits one `OPEN` cart per customer. The first keyed addition locks the customer and creates that cart automatically; optional `PUT` explicitly creates an empty cart. Subsequent additions increase one unique cart/product line. Cart quantities represent intent and do not reserve stock, but the API rejects quantities already above visible availability to provide immediate feedback. Checkout always revalidates.

Checkout freezes the cart as `CHECKOUT_STARTED`. A customer can then immediately create a new open cart. Open-cart totals use current prices. Frozen-cart reads and order reads use `OrderItem` snapshots, so later product name or price changes cannot rewrite history. Orders also snapshot coupon code and percentage; released coupons can later belong to another order without changing the failed order’s explanation.

## Checkout, idempotency, and money

Keyed operations store `(operation, scope, key)`, a SHA-256 fingerprint of canonical JSON input, and a committed resource reference. Records are written in the mutation transaction and retained indefinitely for this assignment. The service checks a committed record before revalidating current stock or coupon state. Same input replays; changed input conflicts. Database uniqueness on `Order.cart` rejects a second checkout even with another key.

The pending-order transaction persists order/items, reservation, unit ownership, coupon ownership, payment attempt, cart freeze, and replay record together. Any validation, constraint, or allocation failure rolls back all of them.

Money uses USD integer cents. Current prices are read at pending-order creation. The order-level calculation is:

```text
gross = Σ(unit_price_cents × quantity)
discount = (gross × whole_percentage + 50) // 100
net = gross − discount
```

This is half-up rounding once on subtotal. A supported upper bound is checked before payment creation. A zero net amount creates a successful local attempt and follows the same finalizer without calling the provider.

## Payment persistence and recovery

One `PaymentAttempt` exists per order. Its provider key is unique, and the fake provider stores amount, currency, outcome, and provider reference in the `payments` database. Repeated calls with equal parameters return the stored result; mismatched reuse fails. A one-shot lost-response flag lets a provider outcome commit independently before returning `UNKNOWN`.

The worker claims at most ten due attempts per cycle. A claim uses `SELECT FOR UPDATE SKIP LOCKED`, increments attempts, assigns a unique token and 30-second deadline, then commits. The owner records initiation before the first provider call. For initiated recovery it queries status first and only submits when status definitively says the provider key is absent.

A known provider outcome is saved before local finalization. Success marks units sold, reservation confirmed, coupon redeemed, order confirmed, and atomically increments the customer count. Failure releases units and coupon, releases the reservation, and fails the order. A repeated matching transition is harmless; a conflicting terminal transition fails.

Every scheduling and attempt-state write filters by the lease token. An expired owner can therefore finish provider I/O but cannot overwrite a newer owner’s scheduling state. Provider idempotency and locked finalization remain correctness safeguards if work overlaps. Backoff doubles with deterministic jitter and caps at 60 seconds. Five attempts and orders pending over 15 minutes are flagged, but recovery continues.

Reservations initially expire after five minutes. If initiation has not occurred, worker expiry and initiation lock the same attempt/reservation path; expiry fails locally without contacting the provider. After initiation, expiry cannot release resources because payment may have succeeded. Unknown results retain both inventory and coupon holds.

## Coupons, rewards, and reports

Coupon state is stored on one row:

```text
AVAILABLE → RESERVED → REDEEMED
                 └──→ AVAILABLE on definitive failure
```

Checkout locks a supplied code normally, validates that it is available, and assigns the pending order. It never silently drops a rejected coupon and charges full price. Finalization verifies ownership before redemption or release. There is one coupon per order, with no stacking or expiry.

The immutable reward program defaults to every five confirmed orders and a 10% coupon. Generation is an explicit keyed administrator action. It locks the program, calculates `confirmed_orders // n`, finds the oldest missing eligible milestone, and writes both coupon and replay record atomically. A unique `(program, milestone)` constraint is the final concurrency guard. No eligibility returns 409 without consuming the key.

The report opens a short read-only Repeatable Read transaction before issuing aggregate queries. Confirmed order snapshots provide product quantities, gross revenue, discounts, net revenue, and successful order count. Coupon counts include available, reserved, redeemed, and generated. Independent aggregates avoid join multiplication, and empty results become zeros. Reporting never creates coupons or repairs state.

## Failure demonstrations and verification limits

`make demo` pauses the regular worker, creates isolated records, and exercises success, definitive payment failure, pre-initiation expiry, provider response loss, rollback after successful-payment finalization writes, and rollback after failed-payment release writes. Fault flags are consumed outside the deliberately rolled-back transaction, so recovery can succeed on the next pass.

PostgreSQL tests use real database aliases. Threaded `TransactionTestCase` tests coordinate simultaneous first additions and last-unit checkout. Other tests verify replay fingerprints, frozen snapshots, coupon release, independent provider persistence, local rollback, recovery, expiry, reward replay, money rounding, and reporting. The implemented mechanisms also address multi-product rollback, worker fencing, and coupon/milestone uniqueness; longer overlapping stress tests for those paths remain the first validation extension.

Capacity is not claimed. Likely limits are connection counts, due-work polling, provider latency and rate limits, unresolved holds, and unit-row volume. Production evolution would add metrics for pending age, retries, lease takeovers, and lock waits; tune batches and indexes; and introduce a broker or partitioning only when measurements justify it.
