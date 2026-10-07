# Checkout and Rewards Service

This repository currently contains the **development foundation only**. Customer and cart operations, inventory reservation, order/payment flows, coupons, reporting, and seed data are planned in [DECISIONS.md](DECISIONS.md), [DECISION_CONTEXT.md](DECISION_CONTEXT.md), and [EDGE_CASES.md](EDGE_CASES.md); they are not implemented.

## Prerequisites and startup

Install Docker Desktop with Compose and `make`, then start the Docker daemon. Python is supplied by the application image; the host Python is used only to prepare `.env`.

```sh
make setup
make up
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

`make setup` checks Docker, creates `.env` if absent with a generated development secret, builds the application image, starts PostgreSQL, and migrates both database aliases. Existing `.env` values are never overwritten. The API and worker start with `make up`. If port 8000 is occupied, change its host mapping in `compose.yaml`.

Only `GET /health/live` and `GET /health/ready` exist. Liveness does not access the database. Readiness checks both configured databases and returns a generic JSON 503 when either is unavailable. There are no customer, product, cart, order, payment, or administration endpoints yet.

## Commands

| Command | Compose equivalent or effect |
| --- | --- |
| `make setup` | Prepare `.env`; `docker compose build`; `docker compose up -d --wait db`; migrate both aliases |
| `make up` | `docker compose up -d --wait api worker` |
| `make down` | `docker compose down` (keeps database data) |
| `make logs` | `docker compose logs -f api worker` |
| `make migrate` | `docker compose run --rm api python manage.py migrate --database default --noinput`, then repeat for `payments` |
| `make check` | `docker compose run --rm api python manage.py check` and `makemigrations --check --dry-run` |
| `make lint` | Run `ruff check` and `ruff format --check` in the API image |
| `make format` | Run `ruff format` explicitly in the API image |
| `make test` | `docker compose run --rm api python manage.py test tests --settings=config.test_settings` |
| `make worker-once` | `docker compose run --rm worker python manage.py run_worker --once` |

The worker currently logs `worker_idle_no_handlers_registered` and reports zero processed jobs. It has no payment or reservation handlers. Continuous mode polls at `WORKER_POLL_SECONDS`; Ctrl-C or Compose shutdown stops it cleanly.

## Database and configuration

PostgreSQL initializes `uniblox` for application data and `uniblox_payments` for future fake-provider effects. Django aliases are `default` and `payments`. The router directs `fake_payments` models to the latter and other models to the former. Future migrations must run against both aliases. Tests use separate `test_uniblox` and `test_uniblox_payments` databases. Cross-database model relations are not supported.

The named `pgdata` volume persists between `make down` and `make up`. To deliberately delete local data, run `docker compose down --volumes`; this is destructive. If database names or credentials change after initialization, use a fresh volume or update PostgreSQL manually, because initialization scripts run only for a new volume.

`.env.example` contains local-only defaults. `.env` is ignored by Git. The application image pins Python and uv; `uv.lock` freezes application and development dependencies. Both Django database connections use explicit transactions where needed later; per-request transactions are disabled. All timestamps use UTC.

## Troubleshooting and verification

- `make setup` says Docker is unavailable: start Docker Desktop, then rerun it.
- Readiness returns 503: inspect `docker compose logs db api` and verify both databases were initialized.
- Container startup fails after dependency changes: run `docker compose build` and then `make up`.
- Worker reports no handlers: expected until business processing is implemented.
- The test command creates and destroys dedicated PostgreSQL test databases; it does not modify development data.

CI runs setup, health checks, migration drift checks, lint, tests, and a one-cycle worker run on GitHub Actions. No remote repository or deployment is configured here.
