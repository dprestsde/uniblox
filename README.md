# Checkout and Rewards Service

A Django/PostgreSQL backend for reliable carts, inventory reservation, asynchronous fake payments, coupons, rewards, and reporting. Checkout commits durable intent first; a leased worker then resolves payment and atomically confirms or fails the order.

Start with the short [`guide/guide.md`](guide/guide.md) for the coding standards, AI-assisted workflow, and production evolution path.

## Architecture

HTTP traffic follows `urls.py → APIView → command serializer → class-based service → ORM`. Plain DRF serializers validate request bodies, path and query values, and idempotency headers before invoking services. Services own database reads, writes, transactions, locking, and state-dependent validation. Separate response serializers verify every service DTO before it is returned; an invalid internal DTO is logged and becomes a safe 500 response.

Workers and management commands construct the same service classes instead of accessing models directly. The fake-payment adapter is the deliberate exception: it exclusively owns provider records in the isolated `payments` database.

## Run locally

Requirements: Docker Desktop with Compose and `make`.

```sh
make setup        # configure, build, migrate both databases, and seed
make up           # start API and payment worker
curl http://127.0.0.1:8000/health/ready
make demo         # run payment failure and recovery demonstrations
```

`make down` preserves PostgreSQL data. `docker compose down --volumes` deliberately deletes it. Setup never overwrites an existing `.env`.

| Command | Purpose |
| --- | --- |
| `make migrate` | Apply migrations to application and provider databases |
| `make seed` | Idempotently create two customers, five products, scarce stock, and rewards |
| `make reset-inventory` | Restore available stock for the five seeded products while preserving orders and reservations |
| `make check` | Run Django and migration-drift checks |
| `make lint` / `make format` | Check or apply Ruff rules |
| `make test` | Run tests in isolated PostgreSQL databases |
| `make api-smoke` | Call every API against the running seeded stack |
| `make worker-once` | Process at most ten due payment attempts and exit |
| `make logs` | Follow API and worker JSON logs |

The equivalent pattern is `docker compose run --rm api python manage.py <command>`. For example, `make seed` runs `docker compose run --rm api python manage.py seed_demo`. To replenish the stable demo products after manual API testing, run `make reset-inventory`; this restores their available quantities to 3, 5, 2, 4, and 1 without changing sold or reserved units.

## API contracts

All request bodies are JSON objects. IDs are UUIDs and money is integer USD cents. Add-item, checkout, and coupon-generation requests require an `Idempotency-Key` header. Body fields owned by the URL or header are rejected.

Common response shapes are:

- **Customer:** `id`, `name`, `email`, `orders_count`.
- **Product:** `id`, `name`, `price_cents`, `currency`, `available_quantity`.
- **Cart:** `id`, `customer_id`, `status`, `currency`, `subtotal_cents`, and `items`; each line contains product ID, snapshotted/current name, quantity, unit price, and line total.
- **Order:** order/customer/cart IDs, status, currency, gross/discount/net cents, optional coupon snapshot, failure code, item snapshots, and timestamps.
- **Coupon:** ID, code, percentage, milestone, status, and optional owner order ID.
- **Page:** `count`, `page`, `page_size`, and `results`. Pagination requires `page >= 1` and `1 <= page_size <= 100`; invalid values return 400.

| Method and path | Request | Success response | Important errors |
| --- | --- | --- | --- |
| `GET /health/live` | None | 200 `{"status":"live"}` | None |
| `GET /health/ready` | None | 200 `{"status":"ready"}` | 503 when either database is unavailable |
| `POST /api/v1/customers` | `{"name":"Ada","email":"ada@example.test"}` | 201 Customer | 400 invalid/unknown fields; 409 duplicate email |
| `GET /api/v1/customers/{id}` | None | 200 Customer | 404 unknown customer or malformed UUID |
| `GET /api/v1/products` | Query `page`, `page_size` | 200 Product page | 400 invalid pagination |
| `GET /api/v1/products/{id}` | None | 200 Product | 404 unknown product or malformed UUID |
| `GET /api/v1/customers/{id}/cart` | None | 200 open Cart | 404 unknown customer or no open cart |
| `PUT /api/v1/customers/{id}/cart` | `{}` | 201 new or 200 existing Cart | 400 nonempty/invalid body; 404 unknown customer |
| `POST /api/v1/customers/{id}/cart/items` | Keyed; `{"product_id":"…","quantity":2}` | 201 Cart; replay 200 | 400 validation/key; 404 customer/product; 409 key conflict or insufficient inventory |
| `GET /api/v1/carts/{id}` | None | 200 Cart | 404 unknown cart or malformed UUID |
| `PATCH /api/v1/carts/{id}/items/{product_id}` | `{"quantity":2}` | 200 Cart | 400 validation; 404 cart/product; 409 frozen cart or insufficient inventory |
| `DELETE /api/v1/carts/{id}/items/{product_id}` | Empty body | 204 | 404 unknown cart; 409 frozen cart |
| `POST /api/v1/carts/{id}/checkout` | Keyed; `{}` or `{"coupon_code":"…"}` | 202 pending Order; replayed terminal Order 200 | 400 validation/key; 404 cart/coupon; 409 empty/frozen cart, shortage, coupon, or key conflict; 503 contention |
| `GET /api/v1/orders/{id}` | None | 200 Order | 404 unknown order or malformed UUID |
| `POST /api/v1/admin/coupons/generate` | Keyed; `{}` | 201 Coupon; replay 200 | 400 validation/key; 409 no eligible milestone or key conflict |
| `GET /api/v1/admin/coupons` | Query `page`, `page_size` | 200 Coupon page | 400 invalid pagination |
| `GET /api/v1/admin/reports/summary` | None | 200 confirmed quantities, revenue, order total, and coupon counts | 500 if a consistent database snapshot cannot be read |

Administration endpoints intentionally omit authentication for this assignment.

## Test each API

After `make setup && make up`, run `make api-smoke`. The script calls every endpoint, captures generated IDs without `jq`, verifies idempotent replay and report balances, processes the order, and accepts the documented `no_coupon_eligible` result when the next reward milestone has not been reached. It creates a customer and consumes one inventory unit, so use a freshly seeded setup or ensure a product remains available.

To inspect endpoints individually, set `BASE=http://127.0.0.1:8000`, run these requests in order, and copy IDs from each JSON response into the shell variables:

```sh
curl "$BASE/health/live"
curl "$BASE/health/ready"
curl -X POST "$BASE/api/v1/customers" -H 'Content-Type: application/json' \
  -d '{"name":"API Tester","email":"api-tester@example.test"}'
CUSTOMER_ID=copy-customer-id
curl "$BASE/api/v1/customers/$CUSTOMER_ID"

curl "$BASE/api/v1/products?page=1&page_size=20"
PRODUCT_ID=copy-available-product-id
curl "$BASE/api/v1/products/$PRODUCT_ID"

curl "$BASE/api/v1/customers/$CUSTOMER_ID/cart"                 # initial 404
curl -X PUT "$BASE/api/v1/customers/$CUSTOMER_ID/cart" \
  -H 'Content-Type: application/json' -d '{}'
CART_ID=copy-cart-id
curl "$BASE/api/v1/customers/$CUSTOMER_ID/cart"
curl -X POST "$BASE/api/v1/customers/$CUSTOMER_ID/cart/items" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: add-001' \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":1}"
curl "$BASE/api/v1/carts/$CART_ID"
curl -X PATCH "$BASE/api/v1/carts/$CART_ID/items/$PRODUCT_ID" \
  -H 'Content-Type: application/json' -d '{"quantity":1}'
curl -X DELETE "$BASE/api/v1/carts/$CART_ID/items/$PRODUCT_ID"
curl -X POST "$BASE/api/v1/customers/$CUSTOMER_ID/cart/items" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: add-002' \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":1}"

curl -X POST "$BASE/api/v1/carts/$CART_ID/checkout" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: checkout-001' -d '{}'
ORDER_ID=copy-order-id
curl "$BASE/api/v1/orders/$ORDER_ID"
make worker-once
curl "$BASE/api/v1/orders/$ORDER_ID"

curl -X POST "$BASE/api/v1/admin/coupons/generate" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: reward-001' -d '{}'
curl "$BASE/api/v1/admin/coupons?page=1&page_size=20"
curl "$BASE/api/v1/admin/reports/summary"
```

For a successful reward flow, confirm orders until `confirmed_orders` reaches the next multiple of five, generate a coupon with a new key, copy its `code`, and submit it as `{"coupon_code":"CODE"}` during the next cart checkout. Coupon generation before eligibility returns `no_coupon_eligible` without consuming the key.

Errors use a stable envelope. Validation is 400, missing resources 404, state or stock conflicts 409, and exhausted lock contention 503:

```json
{"error":{"code":"insufficient_inventory","message":"Reduce the quantity to the available quantity.","retryable":false,"details":{"product_id":"…","requested":3,"available":1}}}
```

Serializer failures identify invalid fields without exposing internals:

```json
{"error":{"code":"validation_error","message":"Invalid request.","retryable":false,"details":{"quantity":["Ensure this value is greater than or equal to 1."]}}}
```

An add-item replay with the same key and input returns HTTP 200 and the exact cart snapshot produced by the original HTTP 201, even after later edits, deletion, checkout, or cart rollover. Checkout replay intentionally returns the original order's current state. Reusing a key with different input returns 409. Failed transactions do not consume keys. Legacy add records without a recoverable snapshot return a structured 409 instead of a server error.

## Payments and recovery

`fake_payments` is routed to the separate `payments` database. Provider effects survive an application finalization rollback, and repeated provider keys cannot create another simulated charge. The worker claims up to ten rows with 30-second tokenized leases, marks initiation before payment, reconciles uncertain outcomes, and retries with capped exponential backoff. Five failed processing attempts or a pending age over 15 minutes is flagged without inventing an outcome. The continuous worker logs database failures, closes stale connections, waits one poll interval, and retries; Compose also restarts it unless explicitly stopped. `--once` remains strict and exits nonzero on a database failure.

Reservations expire after five minutes only before payment initiation. Initiated or unknown payments retain inventory and coupon holds until reconciliation. A zero-total order stores a successful, initiated `zero-total` attempt in the checkout transaction and is finalized locally without calling either provider method. Run one attempt directly with:

```sh
docker compose run --rm worker python manage.py run_worker --once --attempt-id UUID
```

`make demo` pauses the regular worker and demonstrates success, definitive payment failure, pre-initiation expiry, a lost provider response, failed confirmation, and failed failure-finalization. It then runs recovery and restores the worker.

## Data and operating limits

Application data uses `default`; fake-provider data uses `payments`. Cross-database foreign keys are prohibited. Inventory is represented by individual rows and allocated in a short transaction using `SKIP LOCKED` plus bounded waiting. Reports use one read-only repeatable-read snapshot.

This is a submission-ready assignment implementation, not a production deployment. Work took approximately eight hours: six hours for the initial implementation and review fixes, followed by two hours of requested API and service-boundary hardening. Deferred scope includes authentication, product administration, real payment integration, refunds, multiple payment attempts, multiple warehouses/currencies, coupon stacking/expiry, and measured throughput tuning. Longer multi-product contention, report/finalization overlap, and load tests are also deferred. See [DECISIONS.md](DECISIONS.md) and [EDGE_CASES.md](EDGE_CASES.md).

Unknown `/api/v1` paths, malformed path UUIDs, validation failures, and unexpected API exceptions all use the documented JSON error envelope. Server logs retain safe diagnostic exception data plus allowlisted request, attempt, operation, and status fields; request bodies, emails, coupon codes, and credentials are not logged by default.

If readiness returns 503, inspect `docker compose logs db api` and confirm both databases exist. Rebuild with `docker compose build` after dependency or image configuration changes.
