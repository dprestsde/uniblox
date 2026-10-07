# Repository Guidelines

## Project Structure & Module Organization

`task.md` is the assignment contract. Architecture and verification context live in `DECISIONS.md`, `DECISION_CONTEXT.md`, and `EDGE_CASES.md`. Django configuration is in `src/config/`. The `src/store/` app owns commerce models, HTTP views, transaction services, payment recovery, and management commands. `src/fake_payments/` contains the independently persisted provider simulator. Tests live in `tests/`; database initialization and automation live in `docker/`, `scripts/`, `compose.yaml`, and `Makefile`.

## Build, Test, and Development Commands

Run `make setup` once to create `.env`, build images, migrate both databases, and seed demo data. Use `make up`, `make down`, and `make logs` for daily development. `make check` runs Django checks and migration-drift detection. `make lint` checks Ruff lint and formatting; `make format` applies formatting. `make test` runs the suite against isolated PostgreSQL databases. `make demo` exercises payment and local-finalization recovery; `make worker-once` drains one worker batch.

## Coding Style & Naming Conventions

Use Python 3.13, four-space indentation, descriptive domain names, and Ruff. Keep views focused on HTTP parsing and serialization. Put state transitions in `src/store/services/` under explicit `transaction.atomic()` boundaries. Acquire business locks in the documented order. Never call a payment provider inside an application database transaction or add cross-database model relationships.

## Testing Guidelines

Use Django’s test runner, DRF’s API client, and PostgreSQL. Name tests for observable behavior, such as `test_last_unit_competition_has_one_winner`. Use `TransactionTestCase`, separate connections, and coordination barriers for lock behavior. Assert responses and durable state across both database aliases. Map new edge cases in `EDGE_CASES.md`, then run `make check`, `make lint`, and `make test`.

## Commit & Pull Request Guidelines

Use focused imperative commits, for example `Add leased payment recovery worker`. PRs should explain behavior, invariants, deferred limits, and validation. Update README examples when contracts change. Never commit `.env`, credentials, generated caches, or private AI transcripts.
