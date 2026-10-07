# Checkout and Rewards Service

A Django/PostgreSQL backend for reliable carts, inventory reservation, asynchronous fake payments, coupons, rewards, and reporting. Checkout commits durable intent first; a leased worker then resolves payment and atomically confirms or fails the order.

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
| `make check` | Run Django and migration-drift checks |
| `make lint` / `make format` | Check or apply Ruff rules |
| `make test` | Run tests in isolated PostgreSQL databases |
| `make worker-once` | Process at most ten due payment attempts and exit |
| `make logs` | Follow API and worker JSON logs |

The equivalent pattern is `docker compose run --rm api python manage.py <command>`. For example, `make seed` runs `docker compose run --rm api python manage.py seed_demo`.

## API

All bodies are JSON. Mutations marked **keyed** require `Idempotency-Key`. IDs are UUIDs and money is integer USD cents.

| Method and path | Success | Behavior |
| --- | --- | --- |
| `POST /api/v1/customers` | 201 | Create from `{"name":"Ada","email":"ada@example.test"}` |
| `GET /api/v1/customers/{id}` | 200 | Return identity and confirmed `orders_count` |
| `GET /api/v1/products[/{id}]` | 200 | Return current price and available units |
| `GET /api/v1/customers/{id}/cart` | 200 | Read the open cart; 404 if absent |
| `PUT /api/v1/customers/{id}/cart` | 200/201 | Return or create an empty open cart |
| `POST /api/v1/customers/{id}/cart/items` | 201 | **Keyed.** Add `{"product_id":"…","quantity":2}`; creates cart |
| `GET /api/v1/carts/{id}` | 200 | Open cart uses current prices; frozen cart uses order snapshot |
| `PATCH /api/v1/carts/{id}/items/{product_id}` | 200 | Set absolute `{"quantity":2}` |
| `DELETE /api/v1/carts/{id}/items/{product_id}` | 204 | Remove; repeated open-cart removal is harmless |
| `POST /api/v1/carts/{id}/checkout` | 202/200 | **Keyed.** Optional `{"coupon_code":"…"}`; 202 while pending |
| `GET /api/v1/orders/{id}` | 200 | Return state, snapshots, totals, and failure code |
| `POST /api/v1/admin/coupons/generate` | 201/200 | **Keyed.** Generate the oldest eligible milestone |
| `GET /api/v1/admin/coupons` | 200 | Paginated coupon states |
| `GET /api/v1/admin/reports/summary` | 200 | Repeatable-read business totals |

List endpoints accept `page` and `page_size` (maximum 100) and use stable ordering. Administration endpoints intentionally have no authentication for this assignment.

```sh
curl -X POST http://127.0.0.1:8000/api/v1/carts/$CART_ID/checkout \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: checkout-001' \
  -d '{"coupon_code":"REWARD-1-ABC123"}'
```

Errors use a stable envelope. Validation is 400, missing resources 404, state or stock conflicts 409, and exhausted lock contention 503:

```json
{"error":{"code":"insufficient_inventory","message":"Reduce the quantity to the available quantity.","retryable":false,"details":{"product_id":"…","requested":3,"available":1}}}
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

This is a submission-ready, focused six-hour assignment implementation; it is not described as production-deployed. Deferred scope includes authentication, product administration, real payment integration, refunds, multiple payment attempts, multiple warehouses/currencies, coupon stacking/expiry, and measured throughput tuning. Longer multi-product contention, report/finalization overlap, and load tests are also deferred. See [DECISIONS.md](DECISIONS.md) and [EDGE_CASES.md](EDGE_CASES.md).

Unknown `/api/v1` paths, malformed path UUIDs, validation failures, and unexpected API exceptions all use the documented JSON error envelope. Server logs retain safe diagnostic exception data plus allowlisted request, attempt, operation, and status fields; request bodies, emails, coupon codes, and credentials are not logged by default.

If readiness returns 503, inspect `docker compose logs db api` and confirm both databases exist. Rebuild with `docker compose build` after dependency or image configuration changes.
