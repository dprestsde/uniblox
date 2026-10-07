# Edge Cases and Verification Context

This is the acceptance map for `task.md`. `tests/test_commerce.py` uses PostgreSQL `TransactionTestCase`; `tests/test_foundation.py` covers infrastructure; `tests/test_api_architecture.py` covers serializer and layer contracts. `make demo` exercises independent provider persistence and recovery faults.

## Verified automated behavior

| Area | Cases and invariant | Verification |
| --- | --- | --- |
| Customers | Required input, normalized email, duplicate conflict, read-only count | `test_customer_normalization_duplicate_and_read_only_count` |
| Cart identity | Concurrent first additions create one open cart; checkout permits a later new cart | `test_concurrent_first_additions_share_one_open_cart`, `test_new_purchase_gets_a_new_open_cart` |
| Cart input | Addition replay does not add twice; changed keyed input conflicts; booleans and excess quantities fail | `test_add_is_idempotent_and_rejects_invalid_or_excess_quantity` |
| Add replay snapshot | Later add, absolute update, deletion, checkout, and cart rollover cannot change the original replay body | `test_add_replay_returns_immutable_snapshot_after_cart_changes` |
| Price history | Checkout freezes product name, price, quantities, and totals | `test_checkout_reserves_snapshot_and_replays_terminal_order` |
| Inventory | Competing buyers cannot reserve the last unit twice; pending checkout owns one unit | `test_last_unit_competition_has_one_winner`, checkout tests |
| Checkout replay | Lost-response replay returns the original pending or terminal order; no second order/payment exists | `test_checkout_reserves_snapshot_and_replays_terminal_order` |
| Coupon ownership | Failed payment releases its owned coupon; rollback retains ownership; two buyers cannot reserve one code | `test_payment_failure_releases_inventory_and_coupon`, `test_lost_provider_response_and_failed_local_confirmation_recover`, `test_two_customers_competing_for_one_coupon_have_one_winner` |
| Lock ordering | Same-customer finalization and checkout against its held coupon complete without deadlock | `test_finalization_and_same_customer_coupon_checkout_do_not_deadlock` |
| Provider idempotency | Provider state is on `payments`; a persisted result is queried without another charge | `test_lost_provider_response_and_failed_local_confirmation_recover` |
| Local rollback | Injected failure after success writes rolls back order, units, coupon/count changes; retry finalizes once | Same test and `make demo` |
| Failure-finalization rollback | A failed payment plus failed local failure update remains recoverable | `make demo` scenario `failed-failure-finalization` |
| Unknown outcome | Lost response retains holds, reschedules, and later confirms from provider status | Lost-response test and demo |
| Expiry race rule | Pre-initiation expiry fails without provider persistence; initiated work is reconciled | `test_expiry_before_initiation_does_not_call_provider` |
| Zero total | Failed request-side finalization is recovered without `pay()` or `get_status()` | `test_zero_total_recovery_never_calls_provider` |
| Rewards | Only confirmed orders qualify; generation is keyed and replays one milestone | `test_reward_rounding_generation_and_report` |
| Money | Integer cents, order-level half-up boundary, historical totals, balanced report revenue | `test_half_up_rounding_is_applied_once_to_subtotal`, report test |
| Reporting | Confirmed-only revenue and quantities, coupon accounting, no read mutation | `test_reward_rounding_generation_and_report` |
| Product rename reporting | Multiple historical names aggregate once by product ID and display the current catalog name | `test_report_aggregates_renamed_product_by_id_with_current_name` |
| Database routing | Provider and application aliases use distinct databases and routing | `DatabaseTests` |
| API errors/logs | Malformed and unknown API paths use JSON; unexpected failures are hidden from clients; logs retain safe diagnostics | `test_api_fallbacks_return_json_envelopes`, `test_unexpected_exception_is_logged_and_hidden`, `test_json_logs_include_safe_context_and_exception` |
| Health/worker | Readiness fails generically; continuous mode recovers from a database error; one-cycle mode fails visibly | `test_readiness_hides_database_errors`, `test_continuous_worker_recovers_after_database_failure`, `test_once_worker_reports_database_failure`, `make worker-once` |
| Worker lock scope | Claiming a due payment attempt locks only its queue row and is not blocked by an independently locked order | `test_payment_claim_locks_only_the_attempt_row` |
| Serializer input | Unknown/read-only fields, non-object bodies, malformed UUIDs, booleans, fractions, nonpositive quantities, invalid pagination, and missing keys fail before service calls | `SerializerTests`, `ApiArchitectureTests.test_non_object_json_bodies_return_validation_errors`, `test_invalid_pagination_returns_field_errors` |
| Input-source ownership | Body fields owned by paths or `Idempotency-Key` cannot override trusted values | `ApiArchitectureTests.test_body_cannot_override_path_or_idempotency_header` |
| Service delegation | Command serializer `.save()` invokes the injected service with normalized validated values | `SerializerTests` |
| Output contracts | Service DTOs are response-validated; invalid output is logged and returned as a safe 500 | `ApiArchitectureTests.test_invalid_service_output_returns_safe_internal_error` |
| API compatibility | APIViews preserve customer, product, cart, order, coupon, report, replay, and error contracts | `ApiArchitectureTests`, `test_read_and_cart_creation_endpoints_preserve_contracts` |
| Layer boundary | API views and runtime commands have no direct model or transaction access; legacy procedural service modules are absent | `ApiArchitectureTests.test_runtime_entrypoints_do_not_use_orm_or_transactions` |

## Enforced by service and database constraints

DRF serializers reject malformed client-controlled values and report field-specific details before calling a service. Services independently reject unknown customers, products, carts, coupons, empty carts, frozen-cart edits, stale availability, and totals beyond the supported bound inside their transaction boundaries. Cart updates use absolute quantities; repeated deletion from an open cart is harmless. Cart additions use current availability without reserving, while checkout rechecks inside its transaction.

Conditional uniqueness enforces one open cart per customer. Unique constraints enforce one order per cart, one reservation and payment attempt per order, one product line per cart/order, one idempotency operation/scope/key, and one coupon per reward milestone. Check constraints enforce positive quantities, balanced nonnegative money, valid percentages, and inventory/coupon state ownership.

Checkout locks Customer → Cart → Coupon → ordered InventoryUnit rows. Partial multi-product allocation, coupon reservation, order creation, and idempotency commit in one transaction, so any exception rolls everything back. `SKIP LOCKED` short allocation with sufficient visible units takes the waiting path; a true shortage returns product, requested, and available values. A five-second exhausted contention budget returns retryable 503.

Finalization locks Customer before the pending Order, PaymentAttempt, Reservation, Coupon, and InventoryUnit rows. Matching repeated terminal transitions do nothing; conflicting transitions fail. `F()` increments protect concurrent customer confirmations. Failed-order coupon history remains on the immutable order snapshot after ownership is released.

Worker claims use `SKIP LOCKED`, 30-second leases, and unique fencing tokens. Provider calls occur after claim commit. Known outcomes persist before local finalization; stale tokens cannot write scheduling state. Retries use exponential backoff with jitter capped at 60 seconds and flag repeated or old work without fabricating payment failure.

## Deterministic demonstration matrix

`make demo` creates isolated data and prints before, first-attempt, recovery, and final states for:

1. ordinary payment success;
2. definitive payment failure and release;
3. timeout/expiry before provider initiation;
4. provider persistence followed by a lost response;
5. payment success followed by failed local confirmation;
6. payment failure followed by failed local failure-finalization.

One-shot local fault flags are consumed outside the deliberately failed transaction, allowing the next worker cycle to prove recovery. The regular worker is stopped for deterministic injection and restarted afterward.

## Explicitly deferred or requiring deeper stress coverage

The implementation does not support malformed external provider callbacks, refunds or late success after forced release, cancellation after initiation, multiple payment attempts, coupon stacking/expiry, multiple currencies, multiple warehouses, taxes, shipping, or product deletion. These are deferred product scope, not silent behavior.

Focused concurrency tests prove the principal cart, inventory, coupon, and customer-first lock invariants. Longer stress coverage remains deferred for overlapping multi-product allocations with an observed blocking rollback, two workers crossing an actual lease-expiry boundary, stale-worker resumption during provider latency, concurrent milestone requests, and a report held open while another connection finalizes. Multi-process load and throughput measurements are also deferred; no capacity claim is made.
