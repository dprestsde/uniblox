# Architecture Decisions

Detailed rationale and earlier research live in [DECISION_CONTEXT.md](DECISION_CONTEXT.md). The initial implementation followed the assignment’s 4–6-hour timebox; the requested API architecture hardening was completed afterward.

## Invariants and ambiguities resolved

- A customer has at most one open cart; checkout freezes it and allows a new cart.
- One cart creates at most one order, reservation, and payment attempt. Add idempotency replays an immutable response snapshot; checkout replays the original order's current state.
- An inventory unit is `AVAILABLE`, `RESERVED` by one reservation, or `SOLD` by that reservation. A checkout allocates its entire cart or nothing.
- A coupon is `AVAILABLE`, `RESERVED` by one order, or `REDEEMED` by that order. Definitive failure releases it; an unknown payment retains it.
- Only the first successful finalization increments `Customer.orders_count`. Stored counts must equal confirmed orders.
- Orders preserve product, price, coupon, gross, discount, and net snapshots; `gross - discount = net`.
- Reward milestones count confirmed store-wide orders. Each program/milestone generates at most one coupon.

An active-cart read returns 404 and never creates state; explicit cart creation or the first item addition creates the cart. Cart quantities express intent and do not reserve stock. Additions reject quantities already above visible availability, while checkout rechecks and reserves inventory. Open carts use current prices; checkout freezes current prices into the order. Failed orders are terminal, uncertain payments retain their holds, and rewards count confirmed orders store-wide. Authentication is omitted as permitted by `task.md`.

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

**Choice:** Require `Idempotency-Key` for these three operations. Check committed records before current stock or coupon validation. Equal add input returns its original cart response snapshot; checkout returns the original order's current state; changed input returns 409. Records commit with the mutation, so rolled-back attempts do not consume keys. Checkout replay returns 202 while pending and 200 once terminal.

**Why:** Orders need a current status, while an add response must remain identical after subsequent quantity changes, deletion, or cart rollover. A legacy record without a recoverable snapshot returns a structured conflict.

**Consequences:** Records are retained indefinitely for this assignment. Production needs retention, tenant scoping, and request-size limits.

## Decision: Store-wide rewards and snapshot-consistent reporting

**Context:** Coupon generation must not skip or duplicate milestones, and multi-query reports must reconcile during concurrent finalization.

**Options considered:** Generate coupons during checkout, derive them on read, or use an administrator action protected by the reward-program row.

**Choice:** Seed immutable defaults `n=5`, `x=10`. A keyed administrator action locks the program, counts confirmed orders, and creates the oldest missing eligible milestone under a unique program/milestone constraint. Reports run their aggregates in a read-only Repeatable Read transaction, use order snapshots for money, aggregate quantities by product ID, and display the current catalog name.

**Why:** Generation remains explicit as required, missed milestones remain recoverable, and all report values share one database snapshot.

**Consequences:** Configuration changes and multiple reward programs are deferred. Reports verify revenue and coupon accounting but do not repair inconsistencies.

## Decision: DRF contracts and class-based domain services

**Context:** Request validation, HTTP behavior, and database coordination need clear ownership that can be tested independently.

**Options considered:** Function views with manual parsing, `ModelSerializer` classes that persist directly, or explicit `APIView` and plain serializers backed by domain services.

**Choice:** Route every endpoint through an explicit DRF `APIView`, plain command and response serializers, then an injected class-based service. Body, path, query, and header sources are validated separately; trusted path/header values are passed to command serializers through `.save()`. Body fields owned by another source are rejected. Response serializers validate service DTOs. Services own all ORM access, transactions, locks, and database-dependent validation; workers and commands call the same services. Service errors are framework-independent.

**Why:** Client-controlled values fail before persistence, output contracts cannot silently drift, and all entry points reuse the same concurrency-safe behavior.

**Consequences:** Some rules are deliberately checked twice: serializers enforce request shape while services recheck mutable database state inside transactions. Invalid internal DTOs are logged and returned as safe 500 errors.

## Errors, implementation scope, and validation

Errors use `{error: {code, message, retryable, details?}}`: 400 invalid input with field details, JSON 404 for malformed or unknown API paths, 409 stock/idempotency/state conflicts, 500 safe internal or output-contract errors, and 503 transient contention. Structured logs include request IDs, allowlisted attempt/operation/status context, and exception diagnostics while omitting request bodies, emails, coupon values, and credentials.

Implemented: class-based API and service boundaries, strict input and output contracts, customer/cart/product reads, inventory reservation, checkout snapshots, independent fake payment, leased recovery, coupon reservation and generation, customer counts, reporting, health checks, stable seed data, and deterministic demos. Tests cover serializer delegation, architecture boundaries, API compatibility, immutable add replay, retries, last-unit and coupon competition, customer-first finalization locking, attempt-only worker locks, rollback, provider uncertainty, zero-total recovery, worker database outages, expiry, money, rewards, and reporting.

Deferred: authentication, product administration, real payments, refunds, cancellation after initiation, multiple payment attempts, taxes/shipping, multiple currencies or warehouses, coupon stacking/expiry, archival, metrics dashboards, and load-tested tuning.

## AI use, time spent, and next work

AI helped enumerate failure modes and draft transaction boundaries. I rejected an earlier AI proposal for bounded inventory pools and refill coordination because unit rows meet this assignment with less operational state. I also corrected generated reporting tests to use `TransactionTestCase`; Django `TestCase` had already opened an outer transaction and could not establish Repeatable Read at the required boundary. All accepted code was exercised in PostgreSQL.

Approximate time spent: eight hours total—six hours across the initial foundation, implementation, recovery demonstrations, review fixes, and documentation, plus two hours for the requested DRF and class-based service refactor.

With two more hours, I would add coordinated report/finalization and overlapping multi-product rollback tests, then run multi-process load tests around allocation, worker claiming, and lease expiry. I would use those measurements to add pending-age, retry, and lock-wait metrics and tune polling and indexes.
